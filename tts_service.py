import asyncio
import base64
import html
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests
from pydub import AudioSegment

try:
    import boto3
except ImportError:  # pragma: no cover - optional dependency
    boto3 = None

try:
    import edge_tts
except ImportError:  # pragma: no cover - optional dependency
    edge_tts = None


ProgressCallback = Optional[Callable[[float, str], None]]
CancelCallback = Optional[Callable[[], bool]]
DEFAULT_PREVIEW_TEXT = "Xin chao, day la doan nghe thu giong doc cua he thong thuyet minh vien."


class ProcessingCancelledError(Exception):
    pass


def _progress(callback: ProgressCallback, percent: float, message: str) -> None:
    if callback:
        callback(max(0.0, min(percent, 1.0)), message)


def clean_text(text: str) -> str:
    clean = re.compile("<.*?>")
    return re.sub(clean, "", text or "").strip()


def clean_subtitle_text(text: str) -> str:
    """Keep only spoken subtitle content, stripping stray SRT counters/timestamps if present."""
    cleaned = clean_text(text)
    kept_lines = []
    timestamp_pattern = re.compile(
        r"^\s*\d{2}:\d{2}:\d{2}[,.:]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.:]\d{3}\s*$"
    )
    counter_pattern = re.compile(r"^\s*\d+\s*$")

    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if timestamp_pattern.match(stripped):
            continue
        if counter_pattern.match(stripped):
            continue
        kept_lines.append(stripped)

    return "\n".join(kept_lines).strip()


def get_audio_duration(file_path: str) -> int:
    audio = AudioSegment.from_file(file_path)
    return len(audio)


def create_silence(duration_ms: int) -> AudioSegment:
    return AudioSegment.silent(duration=max(0, duration_ms))


def _tmp_mp3_path() -> str:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        return tmp.name


def build_atempo_filter(speed_ratio: float) -> str:
    ratio = max(0.5, float(speed_ratio))
    filters = []
    while ratio > 2.0:
        filters.append("atempo=2.0")
        ratio /= 2.0
    while ratio < 0.5:
        filters.append("atempo=0.5")
        ratio /= 0.5
    filters.append(f"atempo={ratio:.5f}")
    return ",".join(filters)


def _ssml_escape(text: str) -> str:
    return html.escape(text, quote=False)


def subrip_time_to_ms(subrip_time: Any) -> int:
    return (
        (subrip_time.hours * 3600 + subrip_time.minutes * 60 + subrip_time.seconds) * 1000
        + subrip_time.milliseconds
    )


def clamp_speed(speed: float) -> float:
    try:
        value = float(speed)
    except (TypeError, ValueError):
        value = 1.0
    return max(0.9, min(2.0, round(value, 1)))


def speed_to_edge_rate(speed: float) -> str:
    percent = int(round((clamp_speed(speed) - 1.0) * 100))
    if percent >= 0:
        return f"+{percent}%"
    return f"{percent}%"


def estimate_speech_duration_seconds(text: str, speed: float = 1.0) -> float:
    normalized = clean_text(text)
    if not normalized:
        return 0.0
    words = [word for word in re.split(r"\s+", normalized) if word]
    word_count = len(words)
    if word_count == 0:
        return 0.0
    base_wpm = 165.0
    return (word_count / (base_wpm * clamp_speed(speed))) * 60.0


def format_duration_estimate(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} gio {minutes:02} phut {secs:02} giay"
    if minutes:
        return f"{minutes} phut {secs:02} giay"
    return f"{secs} giay"


def fit_audio_to_duration(file_path: str, target_duration_ms: int) -> int:
    if target_duration_ms <= 0:
        return get_audio_duration(file_path)

    original_duration_ms = get_audio_duration(file_path)
    if original_duration_ms <= target_duration_ms:
        return original_duration_ms

    speed_ratio = original_duration_ms / max(target_duration_ms, 1)
    temp_output = _tmp_mp3_path()
    try:
        command = [
            "ffmpeg",
            "-y",
            "-i",
            file_path,
            "-filter:a",
            build_atempo_filter(speed_ratio),
            "-vn",
            temp_output,
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        adjusted_duration_ms = get_audio_duration(temp_output)
        if adjusted_duration_ms > target_duration_ms:
            trimmed = AudioSegment.from_file(temp_output)[:target_duration_ms]
            trimmed.export(temp_output, format="mp3")
            adjusted_duration_ms = len(trimmed)
        os.replace(temp_output, file_path)
        return adjusted_duration_ms
    finally:
        if os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except OSError:
                pass


def apply_audio_speed(file_path: str, speed: float) -> int:
    target_speed = clamp_speed(speed)
    if abs(target_speed - 1.0) < 0.01:
        return get_audio_duration(file_path)

    temp_output = _tmp_mp3_path()
    try:
        command = [
            "ffmpeg",
            "-y",
            "-i",
            file_path,
            "-filter:a",
            build_atempo_filter(target_speed),
            "-vn",
            temp_output,
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        adjusted_duration_ms = get_audio_duration(temp_output)
        os.replace(temp_output, file_path)
        return adjusted_duration_ms
    finally:
        if os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except OSError:
                pass


def get_provider_concurrency(provider_id: str, total_entries: int) -> int:
    defaults = {
        "edge": 10,
        "google": 8,
        "amazon_polly": 6,
        "vbee": 6,
    }
    fallback = defaults.get(provider_id, 6)
    return max(1, min(fallback, total_entries))


@dataclass(frozen=True)
class VoiceOption:
    id: str
    label: str
    language: str
    gender: str = "unknown"
    engine: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseTTSProvider:
    provider_id = ""
    label = ""
    config_help = ""
    supports_voice_preview = True

    def get_status(self) -> Dict[str, str]:
        return {"configured": "true", "message": ""}

    def list_voices(self) -> List[VoiceOption]:
        return []

    async def synthesize(self, text: str, voice_id: str, speed: float, output_path: str) -> None:
        raise NotImplementedError


class EdgeTTSProvider(BaseTTSProvider):
    provider_id = "edge"
    label = "Microsoft Edge TTS"
    config_help = "Không cần cấu hình thêm."
    _voice_cache: Optional[List[VoiceOption]] = None

    def get_status(self) -> Dict[str, str]:
        if edge_tts is None:
            return {"configured": "false", "message": "Thiếu package edge-tts."}
        return {"configured": "true", "message": "Sẵn sàng sử dụng."}

    def list_voices(self) -> List[VoiceOption]:
        fallback = [
            VoiceOption("vi-VN-HoaiMyNeural", "Hoài Mỹ", "vi-VN", "female"),
            VoiceOption("vi-VN-NamMinhNeural", "Nam Minh", "vi-VN", "male"),
        ]
        if edge_tts is None:
            return fallback
        if self._voice_cache is not None:
            return self._voice_cache
        try:
            raw_voices = asyncio.run(edge_tts.list_voices())
            voices = []
            for voice in raw_voices:
                locale = voice.get("Locale", "")
                short_name = voice.get("ShortName", "")
                friendly_name = voice.get("FriendlyName", short_name)
                gender = voice.get("Gender", "unknown").lower()
                label = f"{friendly_name} ({locale})" if locale else friendly_name
                voices.append(
                    VoiceOption(
                        short_name,
                        label,
                        locale,
                        gender,
                        metadata={"friendly_name": friendly_name},
                    )
                )
            voices = sorted(
                voices,
                key=lambda item: (0 if item.language == "vi-VN" else 1, item.language, item.label.lower()),
            )
            self._voice_cache = voices or fallback
            return self._voice_cache
        except Exception:
            return fallback

    async def synthesize(self, text: str, voice_id: str, speed: float, output_path: str) -> None:
        if edge_tts is None:
            raise RuntimeError("edge-tts chưa được cài đặt.")
        communicate = edge_tts.Communicate(text, voice_id, rate=speed_to_edge_rate(speed))
        await communicate.save(output_path)


class GoogleTTSProvider(BaseTTSProvider):
    provider_id = "google"
    label = "Google Cloud TTS"
    config_help = "Nhập Google API key để gọi REST API của Cloud Text-to-Speech."

    @staticmethod
    def _api_key() -> str:
        return os.getenv("GOOGLE_TTS_API_KEY", "").strip()

    def get_status(self) -> Dict[str, str]:
        if not self._api_key():
            return {"configured": "false", "message": "Chưa nhập Google API key."}
        return {"configured": "true", "message": "Sẵn sàng sử dụng qua Google Cloud TTS REST API."}

    def list_voices(self) -> List[VoiceOption]:
        fallback = [
            VoiceOption("vi-VN-Standard-A", "Google Standard A", "vi-VN"),
            VoiceOption("vi-VN-Standard-B", "Google Standard B", "vi-VN"),
            VoiceOption("vi-VN-Wavenet-A", "Google Wavenet A", "vi-VN"),
            VoiceOption("vi-VN-Wavenet-B", "Google Wavenet B", "vi-VN"),
        ]
        api_key = self._api_key()
        if not api_key:
            return fallback
        try:
            response = requests.get(
                "https://texttospeech.googleapis.com/v1/voices",
                params={"languageCode": "vi-VN", "key": api_key},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            voices = []
            for voice in payload.get("voices", []):
                language_codes = voice.get("languageCodes", ["vi-VN"])
                gender = voice.get("ssmlGender", "unknown").lower()
                voices.append(VoiceOption(voice["name"], voice["name"], language_codes[0], gender))
            return voices or fallback
        except Exception:
            return fallback

    async def synthesize(self, text: str, voice_id: str, speed: float, output_path: str) -> None:
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("Google TTS chưa có API key.")

        def _sync_call():
            response = requests.post(
                "https://texttospeech.googleapis.com/v1/text:synthesize",
                params={"key": api_key},
                json={
                    "input": {"text": text},
                    "voice": {
                        "languageCode": "-".join(voice_id.split("-")[:2]),
                        "name": voice_id,
                    },
                    "audioConfig": {
                        "audioEncoding": "MP3",
                        "speakingRate": clamp_speed(speed),
                    },
                },
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            with open(output_path, "wb") as handle:
                handle.write(base64.b64decode(payload["audioContent"]))

        await asyncio.to_thread(_sync_call)


class AmazonPollyProvider(BaseTTSProvider):
    provider_id = "amazon_polly"
    label = "Amazon Polly"
    config_help = "Cần AWS credentials. Amazon Polly hiện không có giọng vi-VN chính thức."

    def get_status(self) -> Dict[str, str]:
        if boto3 is None:
            return {"configured": "false", "message": "Thiếu package boto3."}
        try:
            self._client()
        except Exception as exc:
            return {"configured": "false", "message": f"Chưa cấu hình AWS Polly: {exc}"}
        return {
            "configured": "true",
            "message": "Sẵn sàng sử dụng. Lưu ý: catalog mặc định của Polly trong tool này dùng voice không phải tiếng Việt.",
        }

    def _client(self):
        if boto3 is None:
            raise RuntimeError("boto3 chưa được cài đặt.")
        region = os.getenv("AWS_REGION", "us-east-1")
        return boto3.client("polly", region_name=region)

    def list_voices(self) -> List[VoiceOption]:
        fallback = [
            VoiceOption("Joanna", "Joanna (en-US)", "en-US", "female", "neural", {"supports_news_style": True}),
            VoiceOption("Matthew", "Matthew (en-US)", "en-US", "male", "neural"),
            VoiceOption("Amy", "Amy (en-GB)", "en-GB", "female", "neural"),
        ]
        try:
            client = self._client()
            response = client.describe_voices(Engine="neural")
            voices = []
            for voice in response.get("Voices", []):
                voices.append(
                    VoiceOption(
                        voice["Id"],
                        f'{voice["Name"]} ({voice["LanguageCode"]})',
                        voice["LanguageCode"],
                        voice.get("Gender", "unknown").lower(),
                        "neural",
                        {"supports_news_style": voice["Id"] in {"Joanna", "Matthew", "Lupe", "Amy"}},
                    )
                )
            return voices or fallback
        except Exception:
            return fallback

    async def synthesize(self, text: str, voice_id: str, speed: float, output_path: str) -> None:
        voices = {voice.id: voice for voice in self.list_voices()}
        voice = voices.get(voice_id)
        rate_percent = int(round(clamp_speed(speed) * 100))
        ssml_text = f"<speak><prosody rate=\"{rate_percent}%\">{_ssml_escape(text)}</prosody></speak>"

        def _sync_call():
            client = self._client()
            response = client.synthesize_speech(
                Text=ssml_text,
                TextType="ssml",
                VoiceId=voice_id,
                OutputFormat="mp3",
                Engine=(voice.engine if voice and voice.engine else "neural"),
            )
            with open(output_path, "wb") as handle:
                handle.write(response["AudioStream"].read())

        await asyncio.to_thread(_sync_call)


class VbeeProvider(BaseTTSProvider):
    provider_id = "vbee"
    label = "Vbee"
    config_help = "Cần cấu hình VBEE_TTS_URL, VBEE_API_TOKEN và VBEE_VOICES_JSON."

    def get_status(self) -> Dict[str, str]:
        missing = []
        if not os.getenv("VBEE_TTS_URL"):
            missing.append("VBEE_TTS_URL")
        if not os.getenv("VBEE_API_TOKEN"):
            missing.append("VBEE_API_TOKEN")
        if not os.getenv("VBEE_VOICES_JSON"):
            missing.append("VBEE_VOICES_JSON")
        if missing:
            return {
                "configured": "false",
                "message": f"Thiếu cấu hình Vbee: {', '.join(missing)}.",
            }
        return {"configured": "true", "message": "Sẵn sàng sử dụng theo cấu hình tài khoản Vbee hiện tại."}

    def list_voices(self) -> List[VoiceOption]:
        raw = os.getenv("VBEE_VOICES_JSON", "[]")
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"VBEE_VOICES_JSON không hợp lệ: {exc}") from exc
        voices = []
        for item in items:
            voices.append(
                VoiceOption(
                    item["id"],
                    item.get("label", item["id"]),
                    item.get("language", "vi-VN"),
                    item.get("gender", "unknown"),
                    metadata=item,
                )
            )
        return voices

    async def synthesize(self, text: str, voice_id: str, speed: float, output_path: str) -> None:
        url = os.getenv("VBEE_TTS_URL")
        token = os.getenv("VBEE_API_TOKEN")
        app_id = os.getenv("VBEE_APP_ID")
        response_mode = os.getenv("VBEE_RESPONSE_MODE", "auto")
        timeout = int(os.getenv("VBEE_TIMEOUT_SECONDS", "120"))
        voices = {voice.id: voice for voice in self.list_voices()}
        voice = voices.get(voice_id)
        if voice is None:
            raise RuntimeError(f"Không tìm thấy voice Vbee: {voice_id}")

        payload = {
            "text": text,
            "input_text": text,
            "voice": voice_id,
            "speaker_id": voice.metadata.get("speaker_id", voice_id),
            "audio_format": "mp3",
            "audio_type": "mp3",
            "speed": clamp_speed(voice.metadata.get("speed", speed)),
        }
        if app_id:
            payload["app_id"] = app_id

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        def _write_binary(content: bytes) -> None:
            with open(output_path, "wb") as handle:
                handle.write(content)

        def _download_audio(download_url: str) -> None:
            audio_response = requests.get(download_url, timeout=timeout)
            audio_response.raise_for_status()
            _write_binary(audio_response.content)

        def _sync_call():
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")

            if response_mode == "binary" or content_type.startswith("audio/"):
                _write_binary(response.content)
                return

            data = response.json()
            if "audio_base64" in data:
                _write_binary(base64.b64decode(data["audio_base64"]))
                return
            if "audio_url" in data:
                _download_audio(data["audio_url"])
                return
            if "data" in data and isinstance(data["data"], dict):
                nested = data["data"]
                if "audio_base64" in nested:
                    _write_binary(base64.b64decode(nested["audio_base64"]))
                    return
                if "audio_url" in nested:
                    _download_audio(nested["audio_url"])
                    return
            raise RuntimeError(
                "Không đọc được phản hồi Vbee. Hãy kiểm tra lại endpoint hoặc đặt VBEE_RESPONSE_MODE phù hợp."
            )

        await asyncio.to_thread(_sync_call)


PROVIDERS: Dict[str, BaseTTSProvider] = {
    "edge": EdgeTTSProvider(),
    "google": GoogleTTSProvider(),
    "amazon_polly": AmazonPollyProvider(),
    "vbee": VbeeProvider(),
}


def apply_runtime_settings(settings: Optional[Dict[str, Any]]) -> None:
    settings = settings or {}
    google_settings = settings.get("google", {})
    polly_settings = settings.get("amazon_polly", {})
    vbee_settings = settings.get("vbee", {})

    if google_settings.get("api_key"):
        os.environ["GOOGLE_TTS_API_KEY"] = google_settings["api_key"]
    else:
        os.environ.pop("GOOGLE_TTS_API_KEY", None)

    env_mapping = {
        "AWS_ACCESS_KEY_ID": polly_settings.get("aws_access_key_id", ""),
        "AWS_SECRET_ACCESS_KEY": polly_settings.get("aws_secret_access_key", ""),
        "AWS_REGION": polly_settings.get("aws_region", ""),
        "VBEE_API_TOKEN": vbee_settings.get("api_token", ""),
        "VBEE_TTS_URL": vbee_settings.get("tts_url", ""),
        "VBEE_APP_ID": vbee_settings.get("app_id", ""),
        "VBEE_RESPONSE_MODE": vbee_settings.get("response_mode", ""),
        "VBEE_VOICES_JSON": vbee_settings.get("voices_json", ""),
    }
    for env_key, env_value in env_mapping.items():
        if env_value:
            os.environ[env_key] = str(env_value)
        else:
            os.environ.pop(env_key, None)


def list_tts_capabilities() -> List[Dict[str, Any]]:
    capabilities = []
    for provider_id, provider in PROVIDERS.items():
        status = provider.get_status()
        try:
            voices = provider.list_voices() if status["configured"] == "true" else []
        except Exception as exc:
            voices = []
            status = {"configured": "false", "message": str(exc)}
        capabilities.append(
            {
                "id": provider_id,
                "label": provider.label,
                "status": status,
                "config_help": provider.config_help,
                "supports_voice_preview": provider.supports_voice_preview,
                "voices": [
                    {
                        "id": voice.id,
                        "label": voice.label,
                        "language": voice.language,
                        "gender": voice.gender,
                        "engine": voice.engine,
                        "metadata": voice.metadata,
                    }
                    for voice in voices
                ],
            }
        )
    return capabilities


async def _retry_async(
    runner: Callable[[], Any],
    progress_callback: ProgressCallback,
    stage_label: str,
    cancel_callback: CancelCallback = None,
    retries: int = 3,
    base_delay: float = 1.0,
) -> Any:
    last_error = None
    for attempt in range(1, retries + 1):
        if cancel_callback and cancel_callback():
            raise ProcessingCancelledError("Đã dừng xử lý theo yêu cầu người dùng.")
        try:
            return await runner()
        except Exception as exc:  # pragma: no cover - retry branch depends on network/provider failures
            last_error = exc
            if attempt >= retries:
                break
            _progress(
                progress_callback,
                0.0,
                f"{stage_label} thất bại ở lần {attempt}/{retries}, đang retry...",
            )
            await asyncio.sleep(base_delay * attempt)
    raise RuntimeError(f"{stage_label} thất bại sau {retries} lần thử: {last_error}") from last_error


async def synthesize_text(
    text: str,
    provider_id: str,
    voice_id: str,
    speed: float = 1.0,
    progress_callback: ProgressCallback = None,
    cancel_callback: CancelCallback = None,
) -> str:
    normalized_text = clean_text(text)
    if not normalized_text:
        raise RuntimeError("Không có nội dung để chuyển thành giọng nói.")
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise RuntimeError(f"Provider không hợp lệ: {provider_id}")

    output_path = _tmp_mp3_path()
    _progress(progress_callback, 0.05, "Đang khởi tạo request TTS...")
    if cancel_callback and cancel_callback():
        raise ProcessingCancelledError("Đã dừng xử lý theo yêu cầu người dùng.")

    async def _run():
        await provider.synthesize(normalized_text, voice_id, 1.0, output_path)
        return output_path

    _progress(progress_callback, 0.15, f"Đang gọi {provider.label}...")
    path = await _retry_async(_run, progress_callback, f"Gọi {provider.label}", cancel_callback)
    if cancel_callback and cancel_callback():
        raise ProcessingCancelledError("Đã dừng xử lý theo yêu cầu người dùng.")
    if abs(clamp_speed(speed) - 1.0) >= 0.01:
        _progress(progress_callback, 0.85, f"Đang tinh chỉnh tốc độ {clamp_speed(speed):.1f}x mà không đổi pitch...")
        await asyncio.to_thread(apply_audio_speed, path, speed)
    _progress(progress_callback, 1.0, "Đã tạo xong file âm thanh.")
    return path


async def preview_voice(
    provider_id: str,
    voice_id: str,
    speed: float = 1.0,
    sample_text: Optional[str] = None,
    progress_callback: ProgressCallback = None,
    cancel_callback: CancelCallback = None,
) -> str:
    text = sample_text or DEFAULT_PREVIEW_TEXT
    _progress(progress_callback, 0.05, "Đang tạo file nghe thử...")
    return await synthesize_text(text, provider_id, voice_id, speed, progress_callback, cancel_callback)


async def process_text_only(
    text: str,
    voice: str,
    provider_id: str = "edge",
    speed: float = 1.0,
    progress_callback: ProgressCallback = None,
    cancel_callback: CancelCallback = None,
):
    return await synthesize_text(text, provider_id, voice, speed, progress_callback, cancel_callback)


async def process_srt_logic(
    srt_blocks,
    voice: str,
    provider_id: str = "edge",
    speed: float = 1.0,
    progress_callback: ProgressCallback = None,
    cancel_callback: CancelCallback = None,
    concurrency: Optional[int] = None,
):
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise RuntimeError(f"Provider không hợp lệ: {provider_id}")

    entries = []
    for block in srt_blocks:
        text = clean_subtitle_text(block.text)
        if not text:
            continue
        start_ms = subrip_time_to_ms(block.start)
        end_ms = subrip_time_to_ms(block.end)
        entries.append(
            {
                "text": text,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration_ms": max(0, end_ms - start_ms),
            }
        )

    if not entries:
        raise RuntimeError("File SRT không có nội dung hợp lệ để đọc.")

    total = len(entries)
    resolved_concurrency = concurrency or get_provider_concurrency(provider_id, total)
    generated = 0
    semaphore = asyncio.Semaphore(max(1, resolved_concurrency))
    temp_files: List[str] = []

    async def _generate(index: int, entry: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal generated
        async with semaphore:
            if cancel_callback and cancel_callback():
                raise ProcessingCancelledError("Đã dừng xử lý theo yêu cầu người dùng.")
            _progress(
                progress_callback,
                0.05 + (index / max(total, 1)) * 0.75,
                f"Đang tạo audio block {index + 1}/{total}...",
            )
            seg_path = _tmp_mp3_path()
            temp_files.append(seg_path)

            async def _run():
                await provider.synthesize(entry["text"], voice, 1.0, seg_path)
                return seg_path

            await _retry_async(
                _run,
                progress_callback,
                f"Tạo audio block {index + 1}/{total}",
                cancel_callback,
            )

            duration_ms = get_audio_duration(seg_path)
            if abs(clamp_speed(speed) - 1.0) >= 0.01:
                _progress(
                    progress_callback,
                    0.05 + (index / max(total, 1)) * 0.75,
                    f"Đang chỉnh tốc độ block {index + 1}/{total} sang {clamp_speed(speed):.1f}x mà không đổi pitch...",
                )
                duration_ms = await asyncio.to_thread(apply_audio_speed, seg_path, speed)
            target_duration_ms = entry["duration_ms"]
            compressed = False
            if target_duration_ms > 0 and duration_ms > target_duration_ms:
                _progress(
                    progress_callback,
                    0.05 + (index / max(total, 1)) * 0.75,
                    (
                        f"Block {index + 1}/{total} dai {duration_ms}ms > {target_duration_ms}ms, "
                        "dang nen audio de fit vao subtitle..."
                    ),
                )
                duration_ms = await asyncio.to_thread(fit_audio_to_duration, seg_path, target_duration_ms)
                compressed = True

            if target_duration_ms > 0 and duration_ms > target_duration_ms:
                _progress(
                    progress_callback,
                    0.05 + (index / max(total, 1)) * 0.75,
                    (
                        f"Block {index + 1}/{total} van dai hon subtitle "
                        f"({duration_ms}ms > {target_duration_ms}ms) sau khi nen audio."
                    ),
                )

            generated += 1
            _progress(
                progress_callback,
                0.05 + (generated / total) * 0.75,
                f"Đã tạo {generated}/{total} block audio.",
            )
            return {
                "path": seg_path,
                "start_ms": entry["start_ms"],
                "end_ms": entry["end_ms"],
                "duration_ms": duration_ms,
                "used_speed": clamp_speed(speed),
                "compressed_to_fit": compressed,
                "index": index,
            }

    try:
        results = await asyncio.gather(*[_generate(i, entry) for i, entry in enumerate(entries)])
        if cancel_callback and cancel_callback():
            raise ProcessingCancelledError("Đã dừng xử lý theo yêu cầu người dùng.")
        _progress(progress_callback, 0.86, "Đang ghép các block audio...")

        combined_audio = AudioSegment.empty()
        current_time_ms = 0
        for item in sorted(results, key=lambda value: value["index"]):
            if cancel_callback and cancel_callback():
                raise ProcessingCancelledError("Đã dừng xử lý theo yêu cầu người dùng.")
            segment_audio = AudioSegment.from_file(item["path"])
            gap = item["start_ms"] - current_time_ms
            if gap > 0:
                combined_audio += create_silence(gap)
                current_time_ms += gap
            combined_audio += segment_audio
            current_time_ms += len(segment_audio)

        final_path = _tmp_mp3_path()
        combined_audio.export(final_path, format="mp3")
        _progress(progress_callback, 1.0, "Đã tạo xong file SRT MP3.")
        return final_path
    finally:
        for file_path in temp_files:
            try:
                os.remove(file_path)
            except OSError:
                pass
