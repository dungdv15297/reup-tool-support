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
PROVIDER_TUNING = {
    "deepseek": {
        "max_lines": 22,
        "max_chars": 3200,
        "max_concurrent": 4,
        "request_stagger_seconds": 0.12,
    },
    "google": {
        "max_lines": 18,
        "max_chars": 2600,
        "max_concurrent": 4,
        "request_stagger_seconds": 0.18,
    },
}
DEEPSEEK_BASE_URL = "https://api.deepseek.com/chat/completions"
GOOGLE_BASE_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GOOGLE_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
HAN_CHARACTER_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


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
    "Bản dịch cuối phải là tiếng Việt hoàn toàn; không để sót chữ Hán hay từ tiếng Trung trong output. "
    "Không thêm bình luận. Không bỏ sót mục nào. "
    "Phải giữ nguyên số lượng mục và id. "
    "Chỉ trả về JSON hợp lệ theo đúng schema được yêu cầu."
)


@dataclass
class TranslationItem:
    item_id: int
    text: str
    max_chars: Optional[int] = None


def contains_han_characters(text: str) -> bool:
    return bool(HAN_CHARACTER_RE.search(text or ""))


def _build_system_prompt(custom_prompt: str = "") -> str:
    system_prompt = SYSTEM_PROMPT
    if custom_prompt.strip():
        system_prompt += "\n\nYêu cầu bổ sung từ người dùng:\n" + custom_prompt.strip()
    return system_prompt


def _build_translate_user_prompt(
    items: List[TranslationItem],
    source_lang: str,
    target_lang: str,
) -> str:
    payload_items = []
    for item in items:
        payload = {"id": item.item_id, "text": item.text}
        if item.max_chars is not None:
            payload["max_chars"] = item.max_chars
        payload_items.append(payload)
    return (
        f"Dịch danh sách sau từ {LANGUAGE_OPTIONS.get(source_lang, source_lang)} "
        f"sang {LANGUAGE_OPTIONS.get(target_lang, target_lang)}.\n"
        "Trả về JSON object với cấu trúc: "
        '{"items":[{"id":1,"translated":"..."}, ...]}. '
        "Giữ nguyên toàn bộ id, không thêm hoặc bớt phần tử.\n"
        "Nếu phần tử có trường max_chars thì translated phải cố gắng không vượt quá số ký tự đó. "
        "Hãy ưu tiên câu ngắn, gọn, tự nhiên, liền mạch, giữ đúng ý và ngữ cảnh; "
        "không được dịch cụt lủn hay liệt kê máy móc chỉ để ép độ dài.\n"
        "Tuyệt đối không để sót ký tự Hán hay từ tiếng Trung trong translated.\n"
        f"Danh sách nguồn:\n{json.dumps(payload_items, ensure_ascii=False)}"
    )


def _build_refine_user_prompt(
    items: List[TranslationItem],
    current_translations: Dict[int, str],
    source_lang: str,
    target_lang: str,
) -> str:
    payload_items = []
    for item in items:
        payload = {
            "id": item.item_id,
            "source_text": item.text,
            "current_translation": current_translations.get(item.item_id, ""),
        }
        if item.max_chars is not None:
            payload["max_chars"] = item.max_chars
        payload_items.append(payload)
    return (
        f"Hãy chỉnh sửa lại các bản dịch sau sang {LANGUAGE_OPTIONS.get(target_lang, target_lang)} hoàn toàn tự nhiên.\n"
        f"Ngôn ngữ nguồn ban đầu là {LANGUAGE_OPTIONS.get(source_lang, source_lang)}.\n"
        "Trả về JSON object với cấu trúc: "
        '{"items":[{"id":1,"translated":"..."}, ...]}. '
        "Giữ nguyên toàn bộ id, không thêm hoặc bớt phần tử.\n"
        "Bắt buộc Việt hóa hoàn toàn: không để lại bất kỳ chữ Hán, từ tiếng Trung hoặc ký tự Trung Quốc nào trong translated.\n"
        "Nếu gặp tên riêng, địa danh, thuật ngữ cổ phong Trung Quốc thì đổi sang âm Hán Việt hoặc cách gọi quen thuộc bằng tiếng Việt.\n"
        "Nếu phần tử có trường max_chars thì translated phải cố gắng không vượt quá số ký tự đó. "
        "Giữ đúng ý, ngữ cảnh và câu phải liền mạch, dễ hiểu.\n"
        f"Danh sách cần sửa:\n{json.dumps(payload_items, ensure_ascii=False)}"
    )


def _progress(callback: ProgressCallback, percent: float, message: str) -> None:
    if callback:
        callback(max(0.0, min(percent, 1.0)), message)


def build_job_hash(input_type: str, payload: str, source_lang: str, target_lang: str) -> str:
    raw = f"{input_type}|{source_lang}|{target_lang}|{payload}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def get_provider_tuning(llm_provider: str) -> Dict[str, float]:
    return PROVIDER_TUNING.get(
        llm_provider,
        {
            "max_lines": DEFAULT_MAX_BATCH_LINES,
            "max_chars": DEFAULT_MAX_BATCH_CHARS,
            "max_concurrent": DEFAULT_MAX_CONCURRENT,
            "request_stagger_seconds": 0.12,
        },
    )


class RateLimitError(RuntimeError):
    def __init__(self, retry_after_seconds: float = 0.0, message: str = "429"):
        super().__init__(message)
        self.retry_after_seconds = max(0.0, float(retry_after_seconds or 0.0))


def _extract_retry_after_seconds(response: requests.Response) -> float:
    retry_after = response.headers.get("Retry-After", "").strip()
    if not retry_after:
        return 0.0
    try:
        return max(0.0, float(retry_after))
    except ValueError:
        return 0.0


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


def _extract_google_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return (response.text or "").strip()
    error = payload.get("error", {})
    details = error.get("message") or payload.get("message") or ""
    status = error.get("status") or ""
    if details and status:
        return f"{status}: {details}"
    return details or status or (response.text or "").strip()


def _google_auth_params(api_key: str) -> Dict[str, str]:
    return {"key": api_key}


def _google_auth_headers(api_key: str) -> Dict[str, str]:
    return {"x-goog-api-key": api_key}


def list_google_models(api_key: str) -> List[str]:
    if not api_key.strip():
        raise RuntimeError("Bạn chưa nhập Google API key.")
    response = requests.get(
        GOOGLE_MODELS_URL,
        headers=_google_auth_headers(api_key),
        params=_google_auth_params(api_key),
        timeout=60,
    )
    if response.status_code in {401, 403}:
        details = _extract_google_error(response)
        raise RuntimeError(
            "Google API key không hợp lệ hoặc chưa được cấp quyền Gemini API."
            + (f" Chi tiết từ Google: {details}" if details else "")
        )
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
    custom_prompt: str = "",
    retries: int = 4,
) -> Dict[int, str]:
    user_prompt = _build_translate_user_prompt(items, source_lang, target_lang)
    system_prompt = _build_system_prompt(custom_prompt)

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
                    {"role": "system", "content": system_prompt},
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
            raise RateLimitError(_extract_retry_after_seconds(response))

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
            if isinstance(exc, RateLimitError) and attempt < retries:
                await asyncio.sleep(max(exc.retry_after_seconds, min(2 * attempt, 8)))
                continue
            if attempt < retries and any(token in str(exc).lower() for token in ["timeout", "tempor", "502", "503", "504"]):
                await asyncio.sleep(min(2 * attempt, 8))
                continue
            break
    raise RuntimeError(f"Gọi DeepSeek thất bại: {last_error}") from last_error


async def _call_deepseek_refine(
    api_key: str,
    model: str,
    items: List[TranslationItem],
    current_translations: Dict[int, str],
    source_lang: str,
    target_lang: str,
    custom_prompt: str = "",
    retries: int = 4,
) -> Dict[int, str]:
    user_prompt = _build_refine_user_prompt(items, current_translations, source_lang, target_lang)
    system_prompt = _build_system_prompt(custom_prompt)

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
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            },
            timeout=120,
        )

        if response.status_code == 401:
            raise RuntimeError("API key DeepSeek không hợp lệ.")
        if response.status_code == 402:
            raise RuntimeError("Tài khoản DeepSeek không đủ số dư hoặc bị giới hạn thanh toán.")
        if response.status_code == 429:
            raise RateLimitError(_extract_retry_after_seconds(response))

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
            if isinstance(exc, RateLimitError) and attempt < retries:
                await asyncio.sleep(max(exc.retry_after_seconds, min(2 * attempt, 8)))
                continue
            if attempt < retries and any(token in str(exc).lower() for token in ["timeout", "tempor", "502", "503", "504"]):
                await asyncio.sleep(min(2 * attempt, 8))
                continue
            break
    raise RuntimeError(f"Gọi DeepSeek refine thất bại: {last_error}") from last_error


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


def _is_json_like_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in [
            "expecting",
            "json",
            "delimiter",
            "candidate hợp lệ",
            "thiếu số lượng mục",
        ]
    )


async def _call_google(
    api_key: str,
    model: str,
    items: List[TranslationItem],
    source_lang: str,
    target_lang: str,
    custom_prompt: str = "",
    retries: int = 4,
) -> Dict[int, str]:
    user_prompt = _build_translate_user_prompt(items, source_lang, target_lang)
    system_prompt = _build_system_prompt(custom_prompt)

    def _sync_request() -> Dict[int, str]:
        response = requests.post(
            GOOGLE_BASE_URL_TEMPLATE.format(model=model),
            headers={
                "Content-Type": "application/json",
                **_google_auth_headers(api_key),
            },
            params=_google_auth_params(api_key),
            json={
                "system_instruction": {"parts": [{"text": system_prompt}]},
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
            details = _extract_google_error(response)
            raise RuntimeError(
                "Google API key không hợp lệ hoặc chưa được cấp quyền Gemini API."
                + (f" Chi tiết từ Google: {details}" if details else "")
            )
        if response.status_code == 429:
            raise RateLimitError(_extract_retry_after_seconds(response))

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
            if isinstance(exc, RateLimitError) and attempt < retries:
                await asyncio.sleep(max(exc.retry_after_seconds, min(2 * attempt, 8)))
                continue
            if attempt < retries and _is_json_like_error(exc):
                await asyncio.sleep(min(2 * attempt, 8))
                continue
            if attempt < retries and any(token in str(exc).lower() for token in ["timeout", "tempor", "500", "502", "503", "504"]):
                await asyncio.sleep(min(2 * attempt, 8))
                continue
            break
    raise RuntimeError(f"Gọi Google Gemini thất bại: {last_error}") from last_error


async def _call_google_refine(
    api_key: str,
    model: str,
    items: List[TranslationItem],
    current_translations: Dict[int, str],
    source_lang: str,
    target_lang: str,
    custom_prompt: str = "",
    retries: int = 4,
) -> Dict[int, str]:
    user_prompt = _build_refine_user_prompt(items, current_translations, source_lang, target_lang)
    system_prompt = _build_system_prompt(custom_prompt)

    def _sync_request() -> Dict[int, str]:
        response = requests.post(
            GOOGLE_BASE_URL_TEMPLATE.format(model=model),
            headers={
                "Content-Type": "application/json",
                **_google_auth_headers(api_key),
            },
            params=_google_auth_params(api_key),
            json={
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                },
            },
            timeout=120,
        )

        if response.status_code == 400:
            raise RuntimeError(f"Google Gemini request không hợp lệ: {response.text}")
        if response.status_code == 401 or response.status_code == 403:
            details = _extract_google_error(response)
            raise RuntimeError(
                "Google API key không hợp lệ hoặc chưa được cấp quyền Gemini API."
                + (f" Chi tiết từ Google: {details}" if details else "")
            )
        if response.status_code == 429:
            raise RateLimitError(_extract_retry_after_seconds(response))

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
            raise RuntimeError("Google Gemini trả về thiếu số lượng mục cần sửa.")
        return translated

    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return await asyncio.to_thread(_sync_request)
        except Exception as exc:
            last_error = exc
            if isinstance(exc, RateLimitError) and attempt < retries:
                await asyncio.sleep(max(exc.retry_after_seconds, min(2 * attempt, 8)))
                continue
            if attempt < retries and _is_json_like_error(exc):
                await asyncio.sleep(min(2 * attempt, 8))
                continue
            if attempt < retries and any(token in str(exc).lower() for token in ["timeout", "tempor", "500", "502", "503", "504"]):
                await asyncio.sleep(min(2 * attempt, 8))
                continue
            break
    raise RuntimeError(f"Gọi Google Gemini refine thất bại: {last_error}") from last_error


async def translate_items(
    items: List[TranslationItem],
    llm_provider: str,
    api_key: str,
    model: str,
    source_lang: str,
    target_lang: str,
    custom_prompt: str = "",
    existing_translations: Optional[Dict[int, str]] = None,
    progress_callback: ProgressCallback = None,
    checkpoint_callback: CheckpointCallback = None,
    cancel_callback: CancelCallback = None,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> Dict[int, str]:
    if not api_key.strip():
        raise RuntimeError("Bạn chưa nhập API key cho LLM đang chọn.")

    translations = dict(existing_translations or {})
    tuning = get_provider_tuning(llm_provider)
    effective_concurrency = max(1, min(int(tuning["max_concurrent"]), int(max_concurrent or DEFAULT_MAX_CONCURRENT)))
    semaphore = asyncio.Semaphore(effective_concurrency)
    completed = 0
    request_stagger = float(tuning["request_stagger_seconds"])
    cooldown_lock = asyncio.Lock()
    cooldown_until = 0.0

    pending_items = [item for item in items if item.item_id not in translations]
    batches = batch_items(
        pending_items,
        max_lines=int(tuning["max_lines"]),
        max_chars=int(tuning["max_chars"]),
    )
    total_batches = len(batches)

    async def _wait_for_cooldown():
        nonlocal cooldown_until
        while True:
            async with cooldown_lock:
                now = asyncio.get_running_loop().time()
                remaining = cooldown_until - now
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 1.0))

    async def _set_cooldown(seconds: float):
        nonlocal cooldown_until
        delay = max(seconds, 1.0)
        async with cooldown_lock:
            now = asyncio.get_running_loop().time()
            cooldown_until = max(cooldown_until, now + delay)

    async def _call_provider(
        batch_items_list: List[TranslationItem],
        refine_translations: Optional[Dict[int, str]] = None,
    ) -> Dict[int, str]:
        if llm_provider == "google":
            try:
                if refine_translations is not None:
                    return await _call_google_refine(
                        api_key=api_key,
                        model=model,
                        items=batch_items_list,
                        current_translations=refine_translations,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        custom_prompt=custom_prompt,
                    )
                return await _call_google(
                    api_key=api_key,
                    model=model,
                    items=batch_items_list,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    custom_prompt=custom_prompt,
                )
            except RateLimitError as exc:
                await _set_cooldown(max(exc.retry_after_seconds, 2.0))
                raise
        try:
            if refine_translations is not None:
                return await _call_deepseek_refine(
                    api_key=api_key,
                    model=model,
                    items=batch_items_list,
                    current_translations=refine_translations,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    custom_prompt=custom_prompt,
                )
            return await _call_deepseek(
                api_key=api_key,
                model=model,
                items=batch_items_list,
                source_lang=source_lang,
                target_lang=target_lang,
                custom_prompt=custom_prompt,
            )
        except RateLimitError as exc:
            await _set_cooldown(max(exc.retry_after_seconds, 2.0))
            raise

    async def _run_translate_batch(batch_index: int, batch_items_list: List[TranslationItem]):
        nonlocal completed
        async with semaphore:
            if batch_index > 0 and request_stagger > 0:
                await asyncio.sleep((batch_index % effective_concurrency) * request_stagger)
            await _wait_for_cooldown()
            if cancel_callback and cancel_callback():
                raise TranslationCancelledError("Đã dừng dịch theo yêu cầu người dùng.")
            start_line = batch_items_list[0].item_id
            end_line = batch_items_list[-1].item_id
            _progress(
                progress_callback,
                completed / max(total_batches, 1),
                f"Đang dịch batch {batch_index + 1}/{total_batches} (mục {start_line}-{end_line})...",
            )
            try:
                translated = await _call_provider(batch_items_list)
            except Exception as exc:
                if llm_provider != "google" or not _is_json_like_error(exc) or len(batch_items_list) == 1:
                    raise
                _progress(
                    progress_callback,
                    completed / max(total_batches, 1),
                    (
                        f"Batch {batch_index + 1}/{total_batches} trả JSON lỗi, "
                        "đang tách nhỏ để dịch ổn định hơn..."
                    ),
                )
                translated = {}
                split_batches = batch_items(
                    batch_items_list,
                    max_lines=1 if len(batch_items_list) <= 4 else max(1, len(batch_items_list) // 2),
                    max_chars=max(120, int(tuning["max_chars"] * 0.35)),
                )
                for split_index, split_batch in enumerate(split_batches, start=1):
                    if cancel_callback and cancel_callback():
                        raise TranslationCancelledError("Đã dừng dịch theo yêu cầu người dùng.")
                    await _wait_for_cooldown()
                    if request_stagger > 0:
                        await asyncio.sleep(min(request_stagger, 0.15))
                    split_start = split_batch[0].item_id
                    split_end = split_batch[-1].item_id
                    _progress(
                        progress_callback,
                        completed / max(total_batches, 1),
                        (
                            f"Đang dịch lại phần nhỏ {split_index}/{len(split_batches)} "
                            f"của batch {batch_index + 1} (mục {split_start}-{split_end})..."
                        ),
                    )
                    try:
                        split_translated = await _call_provider(split_batch)
                        translated.update(split_translated)
                    except Exception as split_exc:
                        if not _is_json_like_error(split_exc) or len(split_batch) == 1:
                            raise
                        for single_item in split_batch:
                            if cancel_callback and cancel_callback():
                                raise TranslationCancelledError("Đã dừng dịch theo yêu cầu người dùng.")
                            await _wait_for_cooldown()
                            try:
                                single_translated = await _call_provider([single_item])
                                translated.update(single_translated)
                            except Exception as single_exc:
                                if _is_json_like_error(single_exc):
                                    raise RuntimeError(
                                        f"Gemini không trả JSON hợp lệ cho mục {single_item.item_id} ngay cả khi tách lẻ."
                                    ) from single_exc
                                raise
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

    if batches:
        await asyncio.gather(*[_run_translate_batch(index, batch) for index, batch in enumerate(batches)])
    else:
        _progress(progress_callback, 0.98, "Không có batch mới, đang kiểm tra lại output hiện có.")

    refine_candidates = [
        item
        for item in items
        if contains_han_characters(translations.get(item.item_id, ""))
    ]
    if refine_candidates:
        refine_batches = batch_items(
            refine_candidates,
            max_lines=max(1, min(6, int(tuning["max_lines"]))),
            max_chars=max(160, int(tuning["max_chars"] * 0.45)),
        )
        total_refine_batches = len(refine_batches)
        for refine_index, refine_batch in enumerate(refine_batches):
            if request_stagger > 0:
                await asyncio.sleep(min(request_stagger, 0.2))
            await _wait_for_cooldown()
            if cancel_callback and cancel_callback():
                raise TranslationCancelledError("Đã dừng dịch theo yêu cầu người dùng.")
            start_line = refine_batch[0].item_id
            end_line = refine_batch[-1].item_id
            _progress(
                progress_callback,
                0.92 + (refine_index / max(total_refine_batches, 1)) * 0.07,
                (
                    f"Đang Việt hóa lại batch {refine_index + 1}/{total_refine_batches} "
                    f"cho các mục còn chữ Hán ({start_line}-{end_line})..."
                ),
            )
            try:
                refined = await _call_provider(
                    refine_batch,
                    refine_translations={item.item_id: translations.get(item.item_id, "") for item in refine_batch},
                )
            except Exception as exc:
                if llm_provider != "google" or not _is_json_like_error(exc) or len(refine_batch) == 1:
                    raise
                _progress(
                    progress_callback,
                    0.92 + (refine_index / max(total_refine_batches, 1)) * 0.07,
                    (
                        f"Batch refine {refine_index + 1}/{total_refine_batches} trả JSON lỗi, "
                        "đang tách nhỏ từng mục để xử lý ổn định hơn..."
                    ),
                )
                refined = {}
                for single_index, single_item in enumerate(refine_batch, start=1):
                    if cancel_callback and cancel_callback():
                        raise TranslationCancelledError("Đã dừng dịch theo yêu cầu người dùng.")
                    await _wait_for_cooldown()
                    try:
                        single_result = await _call_provider(
                            [single_item],
                            refine_translations={single_item.item_id: translations.get(single_item.item_id, "")},
                        )
                        refined.update(single_result)
                    except Exception as single_exc:
                        if _is_json_like_error(single_exc):
                            _progress(
                                progress_callback,
                                0.92 + (refine_index / max(total_refine_batches, 1)) * 0.07,
                                (
                                    f"Cảnh báo: mục {single_item.item_id} vẫn lỗi JSON khi refine, "
                                    "giữ lại bản dịch hiện tại cho mục này."
                                ),
                            )
                            continue
                        raise
            translations.update(refined)
            if checkpoint_callback:
                checkpoint_callback({str(key): value for key, value in translations.items()})

        remaining_han = [
            item.item_id
            for item in refine_candidates
            if contains_han_characters(translations.get(item.item_id, ""))
        ]
        if remaining_han:
            _progress(
                progress_callback,
                0.99,
                "Cảnh báo: vẫn còn một số mục chứa chữ Hán sau khi Việt hóa lại: "
                + ", ".join(str(item_id) for item_id in remaining_han[:10]),
            )

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


def build_srt_items(subs, chars_per_second: float = 32.0) -> List[TranslationItem]:
    items = []
    for index, sub in enumerate(subs):
        duration_seconds = max(0.0, sub.duration.ordinal / 1000.0)
        max_chars = max(1, int(duration_seconds * float(chars_per_second))) if duration_seconds > 0 else 1
        items.append(TranslationItem(index + 1, sub.text.strip() or " ", max_chars=max_chars))
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
