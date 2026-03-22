from pydub.generators import Sine

from tts_service import combine_audio_segments_on_timeline


def test_combine_audio_segments_respects_gap_between_subtitles():
    tone_a = Sine(440).to_audio_segment(duration=1000)
    tone_b = Sine(660).to_audio_segment(duration=1000)
    items = [
        {"index": 0, "start_ms": 1000, "end_ms": 2000, "audio": tone_a},
        {"index": 1, "start_ms": 3000, "end_ms": 4000, "audio": tone_b},
    ]

    combined = combine_audio_segments_on_timeline(items, loader=lambda item: item["audio"])

    assert len(combined) == 4000
    assert combined[0:1000].rms == 0
    assert combined[1000:2000].rms > 0
    assert combined[2000:3000].rms == 0
    assert combined[3000:4000].rms > 0
