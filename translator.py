"""
Translator — Context-aware translation via Ollama local LLM.
"""
import json
import requests
from typing import List, Generator, Optional
from srt_parser import SrtEntry, chunk_entries

OLLAMA_BASE_URL = "http://localhost:11434"

SYSTEM_PROMPT = """Bạn là một phiên dịch viên chuyên nghiệp cho phụ đề video. Nhiệm vụ của bạn:

1. **Dịch chính xác** nghĩa gốc, TUYỆT ĐỐI KHÔNG tự bịa thêm chi tiết hoặc bỏ sót nội dung.
2. **Giữ nguyên số lượng dòng** — mỗi dòng gốc phải có đúng 1 dòng dịch tương ứng.
3. **Văn phong tự nhiên, hiện đại** — dùng từ ngữ mượt mà, linh hoạt, tránh kiểu dịch máy khô khan. Thỉnh thoảng dùng từ vựng trending, phong cách GenZ một cách tinh tế (nếu phù hợp ngữ cảnh).
4. **Bám sát ngữ cảnh** — đọc toàn bộ đoạn hội thoại để hiểu mạch câu chuyện trước khi dịch.

**Format trả lời:** Chỉ trả về các dòng đã dịch, mỗi dòng tương ứng với dòng gốc. KHÔNG thêm giải thích, KHÔNG đánh số, KHÔNG thêm bất kỳ ký tự nào khác."""


def get_available_models() -> list:
    """Fetch list of available models from Ollama."""
    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception as e:
        print(f"[Ollama] Error fetching models: {e}")
        return []


def translate_chunk(
    chunk: List[SrtEntry],
    model: str,
    source_lang: str,
    target_lang: str,
) -> Generator[str, None, List[str]]:
    """
    Translate a chunk of SRT entries using Ollama chat API.
    Yields progress messages, returns translated lines.
    """
    # Build the text block for translation
    lines = [entry.text for entry in chunk]
    text_block = '\n'.join(f"{i+1}. {line}" for i, line in enumerate(lines))

    source_info = f"từ {source_lang} " if source_lang and source_lang != "auto" else ""
    user_prompt = f"""Dịch {source_info}sang {target_lang} các dòng phụ đề sau. Trả về ĐÚNG {len(lines)} dòng đã dịch, mỗi dòng trên 1 hàng riêng:

{text_block}"""

    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "top_p": 0.9,
                }
            },
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        result_text = data.get("message", {}).get("content", "")

        # Parse result lines
        result_lines = []
        for line in result_text.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            # Remove numbering if present (e.g. "1. ", "1) ")
            cleaned = _remove_numbering(line)
            result_lines.append(cleaned)

        # Ensure same number of lines as input
        if len(result_lines) < len(lines):
            # Pad with original text if LLM returned fewer lines
            result_lines.extend(lines[len(result_lines):])
        elif len(result_lines) > len(lines):
            # Truncate if LLM returned extra lines
            result_lines = result_lines[:len(lines)]

        return result_lines

    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            "Không thể kết nối Ollama. Hãy đảm bảo Ollama đang chạy (ollama serve)."
        )
    except Exception as e:
        raise RuntimeError(f"Lỗi dịch thuật: {e}")


def translate_entries(
    entries: List[SrtEntry],
    model: str,
    source_lang: str,
    target_lang: str,
    chunk_size: int = 15,
    progress_callback=None,
) -> List[SrtEntry]:
    """
    Translate all SRT entries using chunking strategy.
    Returns new list of SrtEntry with translated text.
    """
    chunks = chunk_entries(entries, chunk_size)
    translated_entries = []
    total_chunks = len(chunks)

    for i, chunk in enumerate(chunks):
        if progress_callback:
            progress_callback(f"Đang dịch chunk {i+1}/{total_chunks} ({len(chunk)} dòng)...")

        translated_lines = translate_chunk(chunk, model, source_lang, target_lang)

        for entry, translated_text in zip(chunk, translated_lines):
            new_entry = SrtEntry(
                index=entry.index,
                start=entry.start,
                end=entry.end,
                text=translated_text,
            )
            translated_entries.append(new_entry)

        if progress_callback:
            progress_callback(f"✓ Hoàn thành chunk {i+1}/{total_chunks}")

    return translated_entries


def _remove_numbering(line: str) -> str:
    """Remove leading numbering like '1. ', '1) ', '1: ' from a line."""
    import re
    return re.sub(r'^\d+[\.\)\:]\s*', '', line)
