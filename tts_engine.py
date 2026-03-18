"""
TTS Engine — Text-to-Speech using edge-tts with voice presets.
"""
import asyncio
import os
import tempfile
import edge_tts
from typing import List, Optional
from pydub import AudioSegment
from srt_parser import SrtEntry

# Voice Presets mapping
VOICE_PRESETS = {
    "commercial": {
        "label": "X - Quảng cáo (Commercial)",
        "description": "Tươi sáng, rõ ràng, dứt khoát, năng lượng cao",
        "voice": "vi-VN-HoaiMyNeural",
        "rate": "+15%",
        "pitch": "+5Hz",
    },
    "recap": {
        "label": "Y - Review Phim (Recap)",
        "description": "Dồn dập, lôi cuốn, nhấn nhá mạnh ở các cú twist",
        "voice": "vi-VN-NamMinhNeural",
        "rate": "+25%",
        "pitch": "+2Hz",
    },
    "storytelling": {
        "label": "Z - Storytelling (Kể chuyện)",
        "description": "Trầm tĩnh, ma mị, ngắt nghỉ dài tạo sự tò mò, hồi hộp",
        "voice": "vi-VN-NamMinhNeural",
        "rate": "-10%",
        "pitch": "-3Hz",
    },
}


async def _synthesize_text(
    text: str,
    voice: str,
    rate: str,
    pitch: str,
    output_path: str,
) -> None:
    """Synthesize a single text segment to an audio file."""
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        pitch=pitch,
    )
    await communicate.save(output_path)


async def _synthesize_entries(
    entries: List[SrtEntry],
    preset_key: str,
    temp_dir: str,
    progress_callback=None,
) -> List[str]:
    """Synthesize all entries to individual audio files."""
    preset = VOICE_PRESETS[preset_key]
    audio_files = []
    total = len(entries)

    for i, entry in enumerate(entries):
        if not entry.text.strip():
            continue

        output_path = os.path.join(temp_dir, f"segment_{i:04d}.mp3")

        if progress_callback:
            progress_callback(f"🔊 TTS segment {i+1}/{total}...")

        await _synthesize_text(
            text=entry.text,
            voice=preset["voice"],
            rate=preset["rate"],
            pitch=preset["pitch"],
            output_path=output_path,
        )

        audio_files.append(output_path)

        if progress_callback:
            progress_callback(f"✓ Segment {i+1}/{total} done")

    return audio_files


def generate_tts(
    entries: List[SrtEntry],
    preset_key: str,
    output_path: str,
    progress_callback=None,
) -> str:
    """
    Generate TTS audio from translated entries.
    Concatenates all segments into a single MP3 file.

    Args:
        entries: List of SrtEntry with translated text
        preset_key: One of 'commercial', 'recap', 'storytelling'
        output_path: Full path for the output MP3 file
        progress_callback: Optional callback for progress updates

    Returns:
        Path to the generated MP3 file
    """
    if preset_key not in VOICE_PRESETS:
        raise ValueError(f"Unknown preset: {preset_key}. Available: {list(VOICE_PRESETS.keys())}")

    with tempfile.TemporaryDirectory() as temp_dir:
        if progress_callback:
            progress_callback("🎙️ Bắt đầu tạo audio...")

        # Run async synthesis
        loop = asyncio.new_event_loop()
        try:
            audio_files = loop.run_until_complete(
                _synthesize_entries(entries, preset_key, temp_dir, progress_callback)
            )
        finally:
            loop.close()

        if not audio_files:
            raise RuntimeError("Không có audio segment nào được tạo.")

        if progress_callback:
            progress_callback("🔗 Đang nối các audio segments...")

        # Concatenate all audio segments
        combined = AudioSegment.empty()
        for audio_file in audio_files:
            segment = AudioSegment.from_mp3(audio_file)
            combined += segment
            # Add a small pause between segments (200ms)
            combined += AudioSegment.silent(duration=200)

        # Export final MP3
        combined.export(output_path, format="mp3")

        if progress_callback:
            progress_callback(f"✅ Audio đã được lưu: {output_path}")

    return output_path


def get_presets_info() -> dict:
    """Return preset information for the frontend."""
    return {
        key: {
            "label": p["label"],
            "description": p["description"],
            "voice": p["voice"],
        }
        for key, p in VOICE_PRESETS.items()
    }
