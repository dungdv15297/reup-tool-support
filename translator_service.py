import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import requests


ProgressCallback = Optional[Callable[[float, str], None]]
CheckpointCallback = Optional[Callable[[Dict[str, Any]], None]]
CancelCallback = Optional[Callable[[], bool]]

DEFAULT_MAX_BATCH_LINES = 18
DEFAULT_MAX_BATCH_CHARS = 2400
DEFAULT_MAX_CONCURRENT = 3
DEEPSEEK_BASE_URL = "https://api.deepseek.com/chat/completions"
GOOGLE_BASE_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GOOGLE_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


LANGUAGE_OPTIONS = {
    "auto": "Auto Detect",
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "vi": "Vietnamese",
}


SYSTEM_PROMPT = (
    "Bạn là một phiên dịch viên chuyên nghiệp. "
    "Nhiệm vụ là dịch sang tiếng Việt tự nhiên, mạch lạc, giữ đúng nội dung, giọng điệu và ngữ cảnh. "
    "Ưu tiên âm Hán Việt cho tên người, địa danh, thuật ngữ cổ phong Trung Quốc khi phù hợp. "
    "Không thêm bình luận. Không bỏ sót mục nào. "
    "Phải giữ nguyên số lượng mục và id. "
    "Chỉ trả về JSON hợp lệ theo đúng schema được yêu cầu."
)


@dataclass
class TranslationItem:
    item_id: int
    text: str


def _progress(callback: ProgressCallback, percent: float, message: str) -> None:
    if callback:
        callback(max(0.0, min(percent, 1.0)), message)


def build_job_hash(input_type: str, payload: str, source_lang: str, target_lang: str) -> str:
    raw = f"{input_type}|{source_lang}|{target_lang}|{payload}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def batch_items(
    items: List[TranslationItem],
    max_lines: int = DEFAULT_MAX_BATCH_LINES,
    max_chars: int = DEFAULT_MAX_BATCH_CHARS,
) -> List[List[TranslationItem]]:
    batches: List[List[TranslationItem]] = []
    current_batch: List[TranslationItem] = []
    current_chars = 0

    for item in items:
        item_chars = len(item.text)
        if current_batch and (len(current_batch) >= max_lines or current_chars + item_chars > max_chars):
            batches.append(current_batch)
            current_batch = []
            current_chars = 0
        current_batch.append(item)
        current_chars += item_chars

    if current_batch:
        batches.append(current_batch)
    return batches


def estimate_tokens_from_text(text: str) -> int:
    normalized = (text or "").strip()
    if not normalized:
        return 0
    return max(1, int(len(normalized) / 4))


def estimate_batch_tokens(items: List[TranslationItem]) -> Dict[str, int]:
    input_chars = sum(len(item.text or "") for item in items)
    input_tokens = sum(estimate_tokens_from_text(item.text) for item in items)
    prompt_overhead = 220 + len(items) * 12
    estimated_output_tokens = int(input_tokens * 1.25) + 32
    return {
        "items": len(items),
        "chars": input_chars,
        "input_tokens": input_tokens + prompt_overhead,
        "output_tokens": estimated_output_tokens,
        "total_tokens": input_tokens + prompt_overhead + estimated_output_tokens,
    }


def estimate_job_tokens(items: List[TranslationItem]) -> Dict[str, Any]:
    batches = batch_items(items)
    batch_estimates = [estimate_batch_tokens(batch) for batch in batches]
    return {
        "batches": len(batches),
        "items": len(items),
        "input_tokens": sum(item["input_tokens"] for item in batch_estimates),
        "output_tokens": sum(item["output_tokens"] for item in batch_estimates),
        "total_tokens": sum(item["total_tokens"] for item in batch_estimates),
        "max_batch_tokens": max((item["total_tokens"] for item in batch_estimates), default=0),
    }


def list_google_models(api_key: str) -> List[str]:
    if not api_key.strip():
        raise RuntimeError("Bạn chưa nhập Google API key.")
    response = requests.get(
        GOOGLE_MODELS_URL,
        headers={"x-goog-api-key": api_key},
        params={"pageSize": 1000},
        timeout=60,
    )
    if response.status_code in {401, 403}:
        raise RuntimeError("Google API key không hợp lệ hoặc chưa được cấp quyền Gemini API.")
    response.raise_for_status()
    data = response.json()
    models = []
    for item in data.get("models", []):
        methods = item.get("supportedGenerationMethods", [])
        name = item.get("name", "")
        if "generateContent" not in methods or not name.startswith("models/"):
            continue
        model_code = name.split("/", 1)[1]
        if "tts" in model_code.lower():
            continue
        models.append(model_code)
    preferred_prefixes = ("gemini-2.5", "gemini-3", "gemini-2.0")
    models = sorted(set(models), key=lambda model: (0 if model.startswith(preferred_prefixes) else 1, model))
    return models


async def _call_deepseek(
    api_key: str,
    model: str,
    items: List[TranslationItem],
    source_lang: str,
    target_lang: str,
    retries: int = 4,
) -> Dict[int, str]:
    payload_items = [{"id": item.item_id, "text": item.text} for item in items]
    user_prompt = (
        f"Dịch danh sách sau từ {LANGUAGE_OPTIONS.get(source_lang, source_lang)} "
        f"sang {LANGUAGE_OPTIONS.get(target_lang, target_lang)}.\n"
        "Trả về JSON object với cấu trúc: "
        '{"items":[{"id":1,"translated":"..."}, ...]}. '
        "Giữ nguyên toàn bộ id, không thêm hoặc bớt phần tử.\n"
        f"Danh sách nguồn:\n{json.dumps(payload_items, ensure_ascii=False)}"
    )

    def _sync_request() -> Dict[int, str]:
        response = requests.post(
            DEEPSEEK_BASE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
            },
            timeout=120,
        )

        if response.status_code == 401:
            raise RuntimeError("API key DeepSeek không hợp lệ.")
        if response.status_code == 402:
            raise RuntimeError("Tài khoản DeepSeek không đủ số dư hoặc bị giới hạn thanh toán.")
        if response.status_code == 429:
            raise RuntimeError("429")

        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        translated = {}
        for item in parsed.get("items", []):
            translated[int(item["id"])] = item["translated"].strip()
        return translated

    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return await asyncio.to_thread(_sync_request)
        except Exception as exc:
            last_error = exc
            if str(exc) == "429" and attempt < retries:
                await asyncio.sleep(min(2 * attempt, 8))
                continue
            if attempt < retries and any(token in str(exc).lower() for token in ["timeout", "tempor", "502", "503", "504"]):
                await asyncio.sleep(min(2 * attempt, 8))
                continue
            break
    raise RuntimeError(f"Gọi DeepSeek thất bại: {last_error}") from last_error


def _extract_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    text = text.replace("\u201c", "\"").replace("\u201d", "\"")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


async def _call_google(
    api_key: str,
    model: str,
    items: List[TranslationItem],
    source_lang: str,
    target_lang: str,
    retries: int = 4,
) -> Dict[int, str]:
    payload_items = [{"id": item.item_id, "text": item.text} for item in items]
    user_prompt = (
        f"Dịch danh sách sau từ {LANGUAGE_OPTIONS.get(source_lang, source_lang)} "
        f"sang {LANGUAGE_OPTIONS.get(target_lang, target_lang)}.\n"
        "Trả về duy nhất JSON object với cấu trúc: "
        '{"items":[{"id":1,"translated":"..."}, ...]}. '
        "Giữ nguyên toàn bộ id, không thêm hoặc bớt phần tử.\n"
        f"Danh sách nguồn:\n{json.dumps(payload_items, ensure_ascii=False)}"
    )

    def _sync_request() -> Dict[int, str]:
        response = requests.post(
            GOOGLE_BASE_URL_TEMPLATE.format(model=model),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            json={
                "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "responseMimeType": "application/json",
                },
            },
            timeout=120,
        )

        if response.status_code == 400:
            raise RuntimeError(f"Google Gemini request không hợp lệ: {response.text}")
        if response.status_code == 401 or response.status_code == 403:
            raise RuntimeError("Google API key không hợp lệ hoặc chưa được cấp quyền Gemini API.")
        if response.status_code == 429:
            raise RuntimeError("429")

        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Google Gemini không trả về candidate hợp lệ.")
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise RuntimeError("Google Gemini không trả về nội dung hợp lệ.")
        content = "".join(part.get("text", "") for part in parts)
        parsed = _extract_json_object(content)
        translated = {}
        for item in parsed.get("items", []):
            translated[int(item["id"])] = item["translated"].strip()
        if len(translated) != len(items):
            raise RuntimeError("Google Gemini trả về thiếu số lượng mục cần dịch.")
        return translated

    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return await asyncio.to_thread(_sync_request)
        except Exception as exc:
            last_error = exc
            if str(exc) == "429" and attempt < retries:
                await asyncio.sleep(min(2 * attempt, 8))
                continue
            if attempt < retries and any(token in str(exc).lower() for token in ["expecting", "json", "candidate hợp lệ", "thiếu số lượng mục"]):
                await asyncio.sleep(min(2 * attempt, 8))
                continue
            if attempt < retries and any(token in str(exc).lower() for token in ["timeout", "tempor", "500", "502", "503", "504"]):
                await asyncio.sleep(min(2 * attempt, 8))
                continue
            break
    raise RuntimeError(f"Gọi Google Gemini thất bại: {last_error}") from last_error


async def translate_items(
    items: List[TranslationItem],
    llm_provider: str,
    api_key: str,
    model: str,
    source_lang: str,
    target_lang: str,
    existing_translations: Optional[Dict[int, str]] = None,
    progress_callback: ProgressCallback = None,
    checkpoint_callback: CheckpointCallback = None,
    cancel_callback: CancelCallback = None,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> Dict[int, str]:
    if not api_key.strip():
        raise RuntimeError("Bạn chưa nhập API key cho LLM đang chọn.")

    translations = dict(existing_translations or {})
    pending_items = [item for item in items if item.item_id not in translations]
    if not pending_items:
        _progress(progress_callback, 1.0, "Không có batch mới, dữ liệu đã sẵn sàng để xuất.")
        return translations

    if llm_provider == "google":
        batches = batch_items(pending_items, max_lines=12, max_chars=1600)
    else:
        batches = batch_items(pending_items)
    semaphore = asyncio.Semaphore(max(1, max_concurrent))
    completed = 0
    total_batches = len(batches)

    async def _run_batch(batch_index: int, batch_items_list: List[TranslationItem]):
        nonlocal completed
        async with semaphore:
            if cancel_callback and cancel_callback():
                raise TranslationCancelledError("Đã dừng dịch theo yêu cầu người dùng.")
            start_line = batch_items_list[0].item_id
            end_line = batch_items_list[-1].item_id
            _progress(
                progress_callback,
                completed / max(total_batches, 1),
                f"Đang dịch batch {batch_index + 1}/{total_batches} (mục {start_line}-{end_line})...",
            )
            if llm_provider == "google":
                translated = await _call_google(
                    api_key=api_key,
                    model=model,
                    items=batch_items_list,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
            else:
                translated = await _call_deepseek(
                    api_key=api_key,
                    model=model,
                    items=batch_items_list,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
            if cancel_callback and cancel_callback():
                raise TranslationCancelledError("Đã dừng dịch theo yêu cầu người dùng.")
            translations.update(translated)
            completed += 1
            if checkpoint_callback:
                checkpoint_callback({str(key): value for key, value in translations.items()})
            _progress(
                progress_callback,
                completed / max(total_batches, 1),
                f"Đã dịch xong batch {completed}/{total_batches}.",
            )

    await asyncio.gather(*[_run_batch(index, batch) for index, batch in enumerate(batches)])
    if cancel_callback and cancel_callback():
        raise TranslationCancelledError("Đã dừng dịch theo yêu cầu người dùng.")
    _progress(progress_callback, 1.0, f"Đã dịch xong {len(items)}/{len(items)} mục.")
    return translations


def translate_plain_text_to_items(text: str) -> List[TranslationItem]:
    lines = [line for line in text.splitlines()]
    if not any(line.strip() for line in lines):
        raise RuntimeError("Không có nội dung văn bản để dịch.")
    return [TranslationItem(index + 1, line if line.strip() else " ") for index, line in enumerate(lines)]


def render_plain_text(items: List[TranslationItem], translations: Dict[int, str]) -> str:
    rendered = []
    for item in items:
        rendered.append(translations.get(item.item_id, item.text if item.text != " " else ""))
    return "\n".join(rendered)


def build_srt_items(subs) -> List[TranslationItem]:
    items = []
    for index, sub in enumerate(subs):
        items.append(TranslationItem(index + 1, sub.text.strip() or " "))
    return items


def apply_translations_to_subs(subs, translations: Dict[int, str]):
    for index, sub in enumerate(subs):
        sub.text = translations.get(index + 1, sub.text if sub.text.strip() else "")
    return subs


def build_resume_payload(
    job_hash: str,
    input_type: str,
    source_lang: str,
    target_lang: str,
    translated_items: Dict[int, str],
    last_error: str = "",
    last_output_path: str = "",
) -> Dict[str, Any]:
    return {
        "job_hash": job_hash,
        "input_type": input_type,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "translated_items": {str(key): value for key, value in translated_items.items()},
        "last_error": last_error,
        "last_output_path": last_output_path,
    }


def parse_resume_payload(resume_payload: Optional[Dict[str, Any]]) -> Dict[int, str]:
    if not resume_payload:
        return {}
    return {int(key): value for key, value in resume_payload.get("translated_items", {}).items()}
class TranslationCancelledError(Exception):
    pass
