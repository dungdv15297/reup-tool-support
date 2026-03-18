/**
 * Auto Translator & TTS — Frontend Logic (Desktop App)
 */

// ─── DOM Elements ─────────────────────────────────
const btnPickFile = document.getElementById('btn-pick-file');
const filePath = document.getElementById('file-path');
const fileNameDisplay = document.getElementById('file-name-display');
const btnPickFolder = document.getElementById('btn-pick-folder');
const outputDir = document.getElementById('output-dir');
const outputDirDisplay = document.getElementById('output-dir-display');
const modelSelect = document.getElementById('model-select');
const refreshModelsBtn = document.getElementById('refresh-models');
const sourceLang = document.getElementById('source-lang');
const targetLang = document.getElementById('target-lang');
const btnTranslate = document.getElementById('btn-translate');
const btnTts = document.getElementById('btn-tts');
const consoleOutput = document.getElementById('console-output');
const btnClearConsole = document.getElementById('btn-clear-console');
const presetCards = document.querySelectorAll('.preset-card');

// AI Manager elements
const ollamaDot = document.getElementById('ollama-dot');
const ollamaStatusText = document.getElementById('ollama-status-text');
const modelsChips = document.getElementById('models-chips');
const btnRefreshAi = document.getElementById('btn-refresh-ai');
const modelNameInput = document.getElementById('model-name-input');
const btnDownloadModel = document.getElementById('btn-download-model');
const btnCancelDownload = document.getElementById('btn-cancel-download');
const progressContainer = document.getElementById('progress-container');
const progressFill = document.getElementById('progress-fill');
const progressText = document.getElementById('progress-text');
const downloadSection = document.getElementById('download-section');

let currentSessionId = null;
let currentPullModel = null;
let ollamaReady = false;

// ─── Init ─────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    checkOllama();
    loadModels();
    loadSettings();
    setupNativePickers();
    setupPresets();
    setupButtons();
    setupAiManager();
});

// ─── Load Ollama Models ───────────────────────────
async function loadModels() {
    try {
        const resp = await fetch('/api/models');
        const data = await resp.json();
        modelSelect.innerHTML = '';

        if (data.models && data.models.length > 0) {
            data.models.forEach(model => {
                const opt = document.createElement('option');
                opt.value = model;
                opt.textContent = model;
                modelSelect.appendChild(opt);
            });
            logToConsole('info', `Đã tải ${data.models.length} models từ Ollama`);
        } else {
            modelSelect.innerHTML = '<option value="">Không tìm thấy model nào</option>';
            logToConsole('error', 'Không tìm thấy model. Hãy đảm bảo Ollama đang chạy.');
        }

        // Restore saved model selection
        const saved = await loadSettings();
        if (saved.model && data.models?.includes(saved.model)) {
            modelSelect.value = saved.model;
        }
    } catch (err) {
        modelSelect.innerHTML = '<option value="">Lỗi kết nối Ollama</option>';
        logToConsole('error', 'Không thể kết nối Ollama. Kiểm tra ollama serve.');
    }
}

// ─── Native File/Folder Pickers ───────────────────
function setupNativePickers() {
    btnPickFile.addEventListener('click', async () => {
        try {
            const resp = await fetch('/api/pick-file', { method: 'POST' });
            const data = await resp.json();
            if (data.path) {
                filePath.value = data.path;
                const name = data.path.split('/').pop();
                fileNameDisplay.textContent = name;
                fileNameDisplay.classList.add('active');
                logToConsole('info', `Đã chọn file: ${name}`);
            }
        } catch (err) {
            logToConsole('error', 'Lỗi mở file picker');
        }
    });

    btnPickFolder.addEventListener('click', async () => {
        try {
            const resp = await fetch('/api/pick-folder', { method: 'POST' });
            const data = await resp.json();
            if (data.path) {
                outputDir.value = data.path;
                outputDirDisplay.textContent = data.path;
                outputDirDisplay.classList.add('active');
                logToConsole('info', `Thư mục output: ${data.path}`);
                saveCurrentSettings();
            }
        } catch (err) {
            logToConsole('error', 'Lỗi mở folder picker');
        }
    });
}

// ─── Voice Presets ────────────────────────────────
function setupPresets() {
    presetCards.forEach(card => {
        card.addEventListener('click', () => {
            presetCards.forEach(c => c.classList.remove('active'));
            card.classList.add('active');
            card.querySelector('input[type="radio"]').checked = true;
        });
    });
}

// ─── Buttons ──────────────────────────────────────
function setupButtons() {
    btnTranslate.addEventListener('click', startTranslation);
    btnTts.addEventListener('click', startTts);
    refreshModelsBtn.addEventListener('click', loadModels);
    btnClearConsole.addEventListener('click', clearConsole);

    // Auto-save settings on change
    [modelSelect, sourceLang, targetLang].forEach(el => {
        el.addEventListener('change', saveCurrentSettings);
    });
}

async function startTranslation() {
    if (!filePath.value) {
        logToConsole('error', 'Chưa chọn file đầu vào!');
        return;
    }
    if (!modelSelect.value) {
        logToConsole('error', 'Chưa chọn model AI!');
        return;
    }
    if (!outputDir.value) {
        logToConsole('error', 'Chưa chọn thư mục output!');
        return;
    }

    setBtnLoading(btnTranslate, true);
    btnTts.disabled = true;
    logToConsole('info', '🚀 Bắt đầu quá trình dịch...');

    try {
        const resp = await fetch('/api/translate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                file_path: filePath.value,
                model: modelSelect.value,
                source_lang: sourceLang.value,
                target_lang: targetLang.value,
                output_dir: outputDir.value,
            }),
        });
        const data = await resp.json();

        if (data.error) {
            logToConsole('error', data.error);
            setBtnLoading(btnTranslate, false);
            return;
        }

        currentSessionId = data.session_id;
        listenProgress(data.session_id, () => {
            setBtnLoading(btnTranslate, false);
            btnTts.disabled = false;
            logToConsole('success', '🎉 Dịch hoàn tất! Có thể bắt đầu TTS.');
        });

    } catch (err) {
        logToConsole('error', `Lỗi: ${err.message}`);
        setBtnLoading(btnTranslate, false);
    }

    saveCurrentSettings();
}

async function startTts() {
    if (!currentSessionId) {
        logToConsole('error', 'Chưa có kết quả dịch. Hãy dịch file trước.');
        return;
    }

    const preset = document.querySelector('input[name="voice-preset"]:checked')?.value || 'commercial';

    setBtnLoading(btnTts, true);
    logToConsole('info', `🎙️ Bắt đầu TTS với preset: ${preset}...`);

    try {
        const resp = await fetch('/api/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: currentSessionId,
                preset: preset,
                output_dir: outputDir.value,
            }),
        });
        const data = await resp.json();

        if (data.error) {
            logToConsole('error', data.error);
            setBtnLoading(btnTts, false);
            return;
        }

        listenProgress(data.session_id, () => {
            setBtnLoading(btnTts, false);
            logToConsole('success', '🎉 TTS hoàn tất!');
        });

    } catch (err) {
        logToConsole('error', `Lỗi TTS: ${err.message}`);
        setBtnLoading(btnTts, false);
    }
}

// ─── SSE Progress Listener ────────────────────────
function listenProgress(sessionId, onComplete) {
    const evtSource = new EventSource(`/api/progress/${sessionId}`);

    evtSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);

            if (data.msg === 'heartbeat') return;

            if (data.finished) {
                evtSource.close();
                if (data.msg === '__DONE__') {
                    onComplete?.();
                } else if (data.msg === '__ERROR__') {
                    logToConsole('error', 'Quá trình kết thúc với lỗi.');
                }
                return;
            }

            // Determine log type
            let type = 'info';
            if (data.msg.includes('✓') || data.msg.includes('✅')) type = 'success';
            else if (data.msg.includes('❌')) type = 'error';

            logToConsole(type, data.msg);
        } catch (e) {
            // ignore parse errors
        }
    };

    evtSource.onerror = () => {
        evtSource.close();
        logToConsole('error', 'Mất kết nối SSE.');
    };
}

// ─── Settings Persistence ─────────────────────────
async function loadSettings() {
    try {
        const resp = await fetch('/api/settings');
        const settings = await resp.json();

        if (settings.model) modelSelect.value = settings.model;
        if (settings.output_dir) {
            outputDir.value = settings.output_dir;
            outputDirDisplay.textContent = settings.output_dir;
        }
        if (settings.source_lang) sourceLang.value = settings.source_lang;
        if (settings.target_lang) targetLang.value = settings.target_lang;
        if (settings.preset) {
            presetCards.forEach(card => {
                const isMatch = card.dataset.preset === settings.preset;
                card.classList.toggle('active', isMatch);
                card.querySelector('input[type="radio"]').checked = isMatch;
            });
        }

        return settings;
    } catch {
        return {};
    }
}

async function saveCurrentSettings() {
    const preset = document.querySelector('input[name="voice-preset"]:checked')?.value || 'commercial';
    const settings = {
        model: modelSelect.value,
        output_dir: outputDir.value,
        source_lang: sourceLang.value,
        target_lang: targetLang.value,
        preset: preset,
    };

    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings),
        });
    } catch {
        // silently ignore
    }
}

// ─── Console Helpers ──────────────────────────────
function logToConsole(type, message) {
    const now = new Date();
    const ts = now.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    const line = document.createElement('div');
    line.className = `console-line ${type}`;
    line.innerHTML = `<span class="timestamp">[${ts}]</span><span>${escapeHtml(message)}</span>`;

    consoleOutput.appendChild(line);
    consoleOutput.scrollTop = consoleOutput.scrollHeight;
}

function clearConsole() {
    consoleOutput.innerHTML = '';
    logToConsole('info', 'Console đã được xóa.');
}

// ─── Utilities ────────────────────────────────────
function setBtnLoading(btn, loading) {
    btn.classList.toggle('loading', loading);
    btn.disabled = loading;
    if (loading) {
        btn.querySelector('.btn-icon-left').textContent = '⏳';
    } else {
        if (btn === btnTranslate) btn.querySelector('.btn-icon-left').textContent = '🚀';
        if (btn === btnTts) btn.querySelector('.btn-icon-left').textContent = '🔊';
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ─── AI Resource Manager ──────────────────────────

async function checkOllama() {
    try {
        const resp = await fetch('/api/ollama-check');
        const data = await resp.json();

        ollamaDot.className = 'status-dot';

        if (data.installed && data.running) {
            ollamaDot.classList.add('online');
            ollamaStatusText.textContent = `Ollama sẵn sàng (${data.version})`;
            ollamaReady = true;
            btnDownloadModel.disabled = false;
            refreshModelChips();
        } else if (data.installed && !data.running) {
            ollamaDot.classList.add('warning');
            ollamaStatusText.textContent = data.message;
            ollamaReady = false;
            btnDownloadModel.disabled = true;
        } else {
            ollamaDot.classList.add('offline');
            ollamaStatusText.textContent = data.message;
            ollamaReady = false;
            btnDownloadModel.disabled = true;
        }
    } catch (err) {
        ollamaDot.className = 'status-dot offline';
        ollamaStatusText.textContent = 'Không thể kiểm tra Ollama';
        ollamaReady = false;
        btnDownloadModel.disabled = true;
    }
}

function setupAiManager() {
    btnDownloadModel.addEventListener('click', startModelPull);
    btnCancelDownload.addEventListener('click', cancelModelPull);
    btnRefreshAi.addEventListener('click', () => {
        checkOllama();
        refreshModelChips();
        loadModels();
    });
}

async function refreshModelChips() {
    try {
        const resp = await fetch('/api/models');
        const data = await resp.json();
        renderModelChips(data.models || []);
    } catch {
        modelsChips.innerHTML = '<span class="model-chip empty">Không thể tải danh sách</span>';
    }
}

function renderModelChips(models) {
    modelsChips.innerHTML = '';

    if (!models || models.length === 0) {
        modelsChips.innerHTML = '<span class="model-chip empty">Chưa có model nào</span>';
        return;
    }

    models.forEach(name => {
        const chip = document.createElement('span');
        chip.className = 'model-chip';
        chip.innerHTML = `${escapeHtml(name)} <span class="chip-delete" title="Xóa model">✕</span>`;

        chip.querySelector('.chip-delete').addEventListener('click', (e) => {
            e.stopPropagation();
            deleteModel(name);
        });

        modelsChips.appendChild(chip);
    });
}

async function startModelPull() {
    const modelName = modelNameInput.value.trim();
    if (!modelName) {
        logToConsole('error', 'Chưa nhập tên model!');
        return;
    }

    if (!ollamaReady) {
        logToConsole('error', 'Ollama chưa sẵn sàng. Kiểm tra cài đặt và chạy ollama serve.');
        return;
    }

    currentPullModel = modelName;
    btnDownloadModel.disabled = true;
    btnCancelDownload.classList.remove('hidden');
    progressContainer.classList.remove('hidden');
    progressFill.style.width = '0%';
    progressText.textContent = 'Đang chờ...';
    logToConsole('info', `⬇️ Bắt đầu tải model: ${modelName}`);

    try {
        const resp = await fetch('/api/model-pull', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: modelName }),
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // keep incomplete line

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;

                try {
                    const data = JSON.parse(line.slice(6));

                    if (data.error) {
                        logToConsole('error', data.error);
                        resetPullUI();
                        return;
                    }

                    if (data.cancelled) {
                        logToConsole('info', '⏹️ Đã hủy tải model.');
                        resetPullUI();
                        return;
                    }

                    if (data.percent !== undefined) {
                        progressFill.style.width = `${data.percent}%`;
                    }

                    if (data.display) {
                        progressText.textContent = data.display;
                    }

                    if (data.done) {
                        logToConsole('success', `✅ Đã tải xong model: ${modelName}`);
                        resetPullUI();
                        refreshModelChips();
                        loadModels();
                        return;
                    }
                } catch {
                    // ignore parse errors
                }
            }
        }

        // Stream ended without explicit done
        resetPullUI();
        refreshModelChips();
        loadModels();

    } catch (err) {
        logToConsole('error', `Lỗi kết nối: ${err.message}`);
        resetPullUI();
    }
}

async function cancelModelPull() {
    if (!currentPullModel) return;

    try {
        await fetch('/api/model-cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: currentPullModel }),
        });
    } catch {
        // ignore
    }
}

function resetPullUI() {
    currentPullModel = null;
    btnDownloadModel.disabled = false;
    btnCancelDownload.classList.add('hidden');
    progressContainer.classList.add('hidden');
    progressFill.style.width = '0%';
    progressText.textContent = '';
}

async function deleteModel(modelName) {
    if (!confirm(`Xóa model "${modelName}"?`)) return;

    logToConsole('info', `🗑️ Đang xóa model: ${modelName}...`);

    try {
        const resp = await fetch('/api/model-delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: modelName }),
        });
        const data = await resp.json();

        if (data.error) {
            logToConsole('error', data.error);
        } else {
            logToConsole('success', data.message);
            refreshModelChips();
            loadModels();
        }
    } catch (err) {
        logToConsole('error', `Lỗi xóa model: ${err.message}`);
    }
}
