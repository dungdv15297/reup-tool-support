"""
Auto Translator & TTS — Desktop App (pywebview + Flask)
"""
import os
import sys
import json
import threading
import queue
import socket
import subprocess
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response
from srt_parser import parse_srt, parse_txt, rebuild_srt, rebuild_txt, SrtEntry
from translator import get_available_models, translate_entries
from tts_engine import generate_tts, get_presets_info

import webview

app = Flask(__name__)

# Global progress queue for SSE
progress_queues = {}
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_settings.json")

# Store translation results in memory for TTS step
translation_results = {}

# Reference to the pywebview window (set at launch)
_window = None


def get_timestamp_str() -> str:
    """Generate timestamp string in ddmm_hhmmss format."""
    now = datetime.now()
    return now.strftime("%d%m_%H%M%S")


def load_settings() -> dict:
    """Load user settings from JSON file."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_settings(settings: dict) -> None:
    """Save user settings to JSON file."""
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def find_free_port() -> int:
    """Find an available port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


# ─── Routes ─────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/models', methods=['GET'])
def api_models():
    """Get available Ollama models."""
    models = get_available_models()
    return jsonify({"models": models})


@app.route('/api/presets', methods=['GET'])
def api_presets():
    """Get TTS voice presets."""
    return jsonify({"presets": get_presets_info()})


@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    """Get saved user settings."""
    return jsonify(load_settings())


@app.route('/api/settings', methods=['POST'])
def api_save_settings():
    """Save user settings."""
    settings = request.json
    save_settings(settings)
    return jsonify({"status": "ok"})


@app.route('/api/pick-folder', methods=['POST'])
def api_pick_folder():
    """Open native folder picker dialog."""
    global _window
    if _window is None:
        return jsonify({"error": "Window not available"}), 500

    result = _window.create_file_dialog(
        webview.FOLDER_DIALOG,
        directory=os.path.expanduser('~'),
    )
    if result and len(result) > 0:
        return jsonify({"path": result[0]})
    return jsonify({"path": ""})


@app.route('/api/pick-file', methods=['POST'])
def api_pick_file():
    """Open native file picker dialog."""
    global _window
    if _window is None:
        return jsonify({"error": "Window not available"}), 500

    file_types = ('SRT files (*.srt)', 'Text files (*.txt)', 'All files (*.*)')
    result = _window.create_file_dialog(
        webview.OPEN_DIALOG,
        directory=os.path.expanduser('~'),
        allow_multiple=False,
        file_types=file_types,
    )
    if result and len(result) > 0:
        return jsonify({"path": result[0]})
    return jsonify({"path": ""})


# ─── AI Resource Manager APIs ──────────────────────

# Track active pull requests for cancellation
_active_pulls = {}


@app.route('/api/ollama-check', methods=['GET'])
def api_ollama_check():
    """Check if Ollama is installed and running."""
    # Check if ollama binary exists
    try:
        result = subprocess.run(
            ['ollama', '-v'], capture_output=True, text=True, timeout=5
        )
        installed = result.returncode == 0
        version = result.stdout.strip() if installed else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return jsonify({
            "installed": False,
            "running": False,
            "version": "",
            "message": "Ollama chưa được cài đặt. Vui lòng cài Ollama trước: https://ollama.com/download"
        })

    # Check if Ollama server is running
    import requests as req
    running = False
    try:
        resp = req.get("http://localhost:11434/api/tags", timeout=3)
        running = resp.status_code == 200
    except Exception:
        pass

    message = ""
    if not running:
        message = "Ollama đã cài nhưng chưa chạy. Hãy chạy 'ollama serve' trong Terminal."

    return jsonify({
        "installed": installed,
        "running": running,
        "version": version,
        "message": message,
    })


@app.route('/api/model-pull', methods=['POST'])
def api_model_pull():
    """
    Pull (download) an Ollama model with streaming progress.
    SSE endpoint that streams JSON progress events.
    """
    data = request.json
    model_name = data.get('model', '')

    if not model_name:
        return jsonify({"error": "Chưa nhập tên model"}), 400

    def generate():
        import requests as req
        pull_id = model_name
        _active_pulls[pull_id] = True

        try:
            resp = req.post(
                "http://localhost:11434/api/pull",
                json={"name": model_name, "stream": True},
                stream=True,
                timeout=600,
            )

            if resp.status_code != 200:
                error_text = resp.text
                if "insufficient" in error_text.lower() or "no space" in error_text.lower():
                    yield f"data: {json.dumps({'error': 'Không đủ dung lượng ổ cứng. Cần tối thiểu 10GB dung lượng trống.'})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': f'Ollama lỗi: {error_text}'})}\n\n"
                return

            for line in resp.iter_lines():
                # Check if cancelled
                if not _active_pulls.get(pull_id, False):
                    resp.close()
                    yield f"data: {json.dumps({'status': 'Đã hủy tải', 'cancelled': True})}\n\n"
                    return

                if not line:
                    continue

                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                status = chunk.get('status', '')
                completed = chunk.get('completed', 0)
                total = chunk.get('total', 0)

                progress_data = {'status': status}

                if total > 0:
                    percent = round((completed / total) * 100, 1)
                    progress_data['percent'] = percent
                    progress_data['completed'] = completed
                    progress_data['total'] = total

                    # Format status text
                    if 'pulling' in status:
                        progress_data['display'] = f"Đang tải... {percent}%"
                    elif 'verifying' in status:
                        progress_data['display'] = f"Đang xác minh... {percent}%"
                else:
                    if 'pulling' in status:
                        progress_data['display'] = "Đang tải..."
                    elif 'verifying' in status:
                        progress_data['display'] = "Đang xác minh..."
                    elif 'writing' in status or 'manifest' in status.lower():
                        progress_data['display'] = "Đang giải nén..."
                    elif status == 'success':
                        progress_data['display'] = "Sẵn sàng"
                        progress_data['done'] = True
                    else:
                        progress_data['display'] = status

                yield f"data: {json.dumps(progress_data)}\n\n"

        except req.exceptions.ConnectionError:
            yield f"data: {json.dumps({'error': 'Lỗi kết nối mạng, vui lòng thử lại.'})}\n\n"
        except req.exceptions.Timeout:
            yield f"data: {json.dumps({'error': 'Lỗi kết nối mạng, vui lòng thử lại.'})}\n\n"
        except Exception as e:
            error_msg = str(e)
            if "insufficient" in error_msg.lower() or "no space" in error_msg.lower():
                yield f"data: {json.dumps({'error': 'Không đủ dung lượng ổ cứng. Cần tối thiểu 10GB dung lượng trống.'})}\n\n"
            else:
                yield f"data: {json.dumps({'error': f'Lỗi: {error_msg}'})}\n\n"
        finally:
            _active_pulls.pop(pull_id, None)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


@app.route('/api/model-cancel', methods=['POST'])
def api_model_cancel():
    """Cancel an active model pull."""
    data = request.json
    model_name = data.get('model', '')
    if model_name in _active_pulls:
        _active_pulls[model_name] = False
        return jsonify({"status": "cancelled"})
    return jsonify({"status": "not_found"})


@app.route('/api/model-delete', methods=['POST'])
def api_model_delete():
    """Delete a downloaded model."""
    import requests as req
    data = request.json
    model_name = data.get('model', '')

    if not model_name:
        return jsonify({"error": "Chưa chọn model để xóa"}), 400

    try:
        resp = req.delete(
            "http://localhost:11434/api/delete",
            json={"name": model_name},
            timeout=10,
        )
        if resp.status_code == 200:
            return jsonify({"status": "ok", "message": f"Đã xóa model: {model_name}"})
        else:
            return jsonify({"error": f"Không thể xóa: {resp.text}"}), 400
    except Exception as e:
        return jsonify({"error": f"Lỗi: {str(e)}"}), 500


@app.route('/api/translate', methods=['POST'])
def api_translate():
    """
    Translate uploaded SRT/TXT file.
    Expects JSON with:
    - file_path: absolute path to the .srt or .txt file
    - model: Ollama model name
    - source_lang: source language
    - target_lang: target language
    - output_dir: output directory path
    """
    data = request.json
    file_path = data.get('file_path', '')
    model = data.get('model', '')
    source_lang = data.get('source_lang', 'auto')
    target_lang = data.get('target_lang', 'Tiếng Việt')
    output_dir = data.get('output_dir', '')

    if not file_path or not os.path.isfile(file_path):
        return jsonify({"error": "File không tồn tại"}), 400
    if not model:
        return jsonify({"error": "Chưa chọn model"}), 400
    if not output_dir:
        return jsonify({"error": "Chưa chọn thư mục output"}), 400

    filename = os.path.basename(file_path)
    is_srt = filename.lower().endswith('.srt')

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return jsonify({"error": f"Không đọc được file: {e}"}), 400

    # Parse the file
    if is_srt:
        entries = parse_srt(content)
    else:
        entries = parse_txt(content)

    if not entries:
        return jsonify({"error": "File rỗng hoặc không đọc được"}), 400

    # Create a session ID for progress tracking
    session_id = get_timestamp_str()

    # Set up progress queue
    q = queue.Queue()
    progress_queues[session_id] = q

    def progress_cb(msg):
        q.put(msg)

    def run_translation():
        try:
            progress_cb(f"📄 Đã đọc {len(entries)} dòng từ {filename}")
            progress_cb(f"🧠 Đang dịch bằng model: {model}")

            translated = translate_entries(
                entries, model, source_lang, target_lang,
                progress_callback=progress_cb
            )

            # Build output
            ext = '.srt' if is_srt else '.txt'
            output_filename = f"o_translate_{session_id}{ext}"
            output_path = os.path.join(output_dir, output_filename)

            if is_srt:
                output_content = rebuild_srt(translated)
            else:
                output_content = rebuild_txt(translated)

            os.makedirs(output_dir, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(output_content)

            # Store for TTS
            translation_results[session_id] = {
                "entries": translated,
                "output_path": output_path,
                "output_dir": output_dir,
            }

            progress_cb(f"✅ Dịch xong! File: {output_filename}")
            progress_cb("__DONE__")

        except Exception as e:
            progress_cb(f"❌ Lỗi: {str(e)}")
            progress_cb("__ERROR__")

    thread = threading.Thread(target=run_translation, daemon=True)
    thread.start()

    return jsonify({"session_id": session_id, "status": "started"})


@app.route('/api/tts', methods=['POST'])
def api_tts():
    """
    Generate TTS from translated text.
    Expects JSON with:
    - session_id: from translation step
    - preset: voice preset key
    - output_dir: output directory (optional, defaults to translation output_dir)
    """
    data = request.json
    session_id = data.get('session_id', '')
    preset = data.get('preset', 'commercial')
    output_dir = data.get('output_dir', '')

    if session_id not in translation_results:
        return jsonify({"error": "Chưa có kết quả dịch. Hãy dịch file trước."}), 400

    result = translation_results[session_id]
    entries = result["entries"]
    if not output_dir:
        output_dir = result["output_dir"]

    # Create new progress session for TTS
    tts_session_id = f"tts_{session_id}"
    q = queue.Queue()
    progress_queues[tts_session_id] = q

    def progress_cb(msg):
        q.put(msg)

    def run_tts():
        try:
            output_filename = f"o_tts_{session_id}.mp3"
            output_path = os.path.join(output_dir, output_filename)

            generate_tts(entries, preset, output_path, progress_callback=progress_cb)

            progress_cb(f"✅ TTS hoàn thành! File: {output_filename}")
            progress_cb("__DONE__")

        except Exception as e:
            progress_cb(f"❌ Lỗi TTS: {str(e)}")
            progress_cb("__ERROR__")

    thread = threading.Thread(target=run_tts, daemon=True)
    thread.start()

    return jsonify({"session_id": tts_session_id, "status": "started"})


@app.route('/api/progress/<session_id>')
def api_progress(session_id):
    """SSE endpoint for real-time progress updates."""
    def generate():
        q = progress_queues.get(session_id)
        if not q:
            yield f"data: {json.dumps({'msg': 'No session found'})}\n\n"
            return

        while True:
            try:
                msg = q.get(timeout=60)
                if msg in ("__DONE__", "__ERROR__"):
                    yield f"data: {json.dumps({'msg': msg, 'finished': True})}\n\n"
                    # Cleanup
                    progress_queues.pop(session_id, None)
                    break
                yield f"data: {json.dumps({'msg': msg})}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'msg': 'heartbeat'})}\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


# ─── Desktop App Launcher ──────────────────────────

def start_flask(port):
    """Run Flask server in a background thread."""
    app.run(host='127.0.0.1', port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == '__main__':
    port = find_free_port()

    # Start Flask in background thread
    server_thread = threading.Thread(target=start_flask, args=(port,), daemon=True)
    server_thread.start()

    # Create native desktop window
    _window = webview.create_window(
        title='Auto Translator & TTS',
        url=f'http://127.0.0.1:{port}',
        width=1150,
        height=850,
        min_size=(900, 650),
        resizable=True,
        text_select=True,
    )

    # Start the native GUI event loop (blocks until window is closed)
    webview.start(debug=False)
