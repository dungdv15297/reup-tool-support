import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict


BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "config.json"
LEGACY_STATE_FILE = BASE_DIR / "user_state.json"


DEFAULT_STATE: Dict[str, Any] = {
    "credentials": {
        "google": {
            "api_key": "",
        },
        "amazon_polly": {
            "aws_access_key_id": "",
            "aws_secret_access_key": "",
            "aws_region": "us-east-1",
        },
        "vbee": {
            "api_token": "",
            "tts_url": "",
            "app_id": "",
            "response_mode": "auto",
            "voices_json": "[]",
        },
    },
    "tts": {
        "provider_id": "edge",
        "voice_id": "",
        "speed": 1.0,
        "text_input": "",
        "preview_text": "",
        "selected_srt_path": "",
        "output_dir": "",
        "logs": [],
    },
    "translator": {
        "selected_llm_provider": "deepseek",
        "selected_llm_model": "deepseek-chat",
        "api_keys": {
            "deepseek": "",
            "google": "",
        },
        "preferences": {
            "source_lang": "auto",
            "target_lang": "vi",
        },
        "text_input": "",
        "selected_srt_path": "",
        "output_dir": "",
        "logs": [],
        "resume": {
            "job_hash": "",
            "input_type": "",
            "source_lang": "auto",
            "target_lang": "vi",
            "translated_items": {},
            "last_error": "",
            "last_output_path": "",
        },
    },
}


def _merge_dicts(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_legacy_state(data: Dict[str, Any]) -> Dict[str, Any]:
    normalized = deepcopy(DEFAULT_STATE)
    if "last_session" in data:
        normalized["credentials"] = _merge_dicts(normalized["credentials"], data.get("credentials", {}))
        normalized["tts"] = _merge_dicts(normalized["tts"], data.get("last_session", {}))
        if "translator" in data:
            normalized["translator"] = _merge_dicts(normalized["translator"], data["translator"])
    else:
        normalized = _merge_dicts(normalized, data)

    translator = normalized.get("translator", {})
    legacy_llm = translator.get("selected_llm", "")
    if legacy_llm and not translator.get("selected_llm_model"):
        translator["selected_llm_model"] = legacy_llm
    if not translator.get("selected_llm_provider"):
        model = translator.get("selected_llm_model", "")
        translator["selected_llm_provider"] = "google" if model.startswith("gemini-") else "deepseek"
    translator.setdefault("api_keys", {})
    translator["api_keys"].setdefault("google", "")
    normalized["translator"] = translator

    tts = normalized.get("tts", {})
    legacy_speed = tts.get("speed")
    if legacy_speed in (None, "", 0):
        tts["speed"] = 1.0
    try:
        tts["speed"] = float(tts.get("speed", 1.0))
    except (TypeError, ValueError):
        tts["speed"] = 1.0
    normalized["tts"] = tts
    return normalized


def load_state() -> Dict[str, Any]:
    source = STATE_FILE if STATE_FILE.exists() else LEGACY_STATE_FILE
    if not source.exists():
        return deepcopy(DEFAULT_STATE)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return deepcopy(DEFAULT_STATE)
    normalized = _normalize_legacy_state(data)
    return _merge_dicts(DEFAULT_STATE, normalized)


def save_state(state: Dict[str, Any]) -> None:
    payload = _merge_dicts(DEFAULT_STATE, state)
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
