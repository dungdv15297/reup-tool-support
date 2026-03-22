import pysrt


def _format_subrip_time(subrip_time):
    return f"{subrip_time.hours:02}:{subrip_time.minutes:02}:{subrip_time.seconds:02},{subrip_time.milliseconds:03}"


def parse_srt(srt_file_content):
    """Parses SRT file content into a list of blocks."""
    # pysrt.from_string needs a string, not bytes
    if isinstance(srt_file_content, bytes):
        srt_file_content = srt_file_content.decode('utf-8', errors='ignore')
    
    subs = pysrt.from_string(srt_file_content)
    return subs

def get_srt_total_duration(subs):
    """Returns the total duration of the SRT as a string."""
    if not subs:
        return "00:00:00"
    last_sub = subs[-1]
    return str(last_sub.end)


def serialize_srt(subs):
    """Serializes pysrt subtitles back to SRT text."""
    blocks = []
    for item in subs:
        blocks.append(
            "\n".join(
                [
                    str(item.index),
                    f"{_format_subrip_time(item.start)} --> {_format_subrip_time(item.end)}",
                    item.text.rstrip(),
                ]
            )
        )
    return "\n\n".join(blocks).rstrip() + "\n"


def _time_to_ms(subrip_time):
    return (
        (subrip_time.hours * 3600 + subrip_time.minutes * 60 + subrip_time.seconds) * 1000
        + subrip_time.milliseconds
    )


def _is_strong_sentence_end(text):
    return (text or "").rstrip().endswith((".", "!", "?", "...", "…", "。", "！", "？"))


def normalize_srt_blocks(
    subs,
    min_duration_ms=1200,
    min_chars=18,
    max_gap_ms=320,
    max_merge_blocks=8,
    max_merged_duration_ms=14000,
    max_merged_chars=220,
):
    if not subs:
        return pysrt.SubRipFile(), {"original_blocks": 0, "normalized_blocks": 0, "merged_blocks": 0}

    normalized = []
    buffer_item = None
    buffer_count = 0

    def flush_buffer():
        nonlocal buffer_item, buffer_count
        if buffer_item is not None:
            buffer_item.index = len(normalized) + 1
            normalized.append(buffer_item)
        buffer_item = None
        buffer_count = 0

    for sub in subs:
        text = (sub.text or "").strip()
        if buffer_item is None:
            buffer_item = pysrt.SubRipItem(index=1, start=sub.start, end=sub.end, text=text)
            buffer_count = 1
            continue

        current_text = (buffer_item.text or "").strip()
        current_duration_ms = _time_to_ms(buffer_item.end) - _time_to_ms(buffer_item.start)
        next_duration_ms = _time_to_ms(sub.end) - _time_to_ms(sub.start)
        gap_ms = _time_to_ms(sub.start) - _time_to_ms(buffer_item.end)
        merged_chars = len((current_text + " " + text).strip())
        merged_duration_ms = _time_to_ms(sub.end) - _time_to_ms(buffer_item.start)

        merge_due_to_short = (
            current_duration_ms < min_duration_ms
            or next_duration_ms < min_duration_ms
            or len(current_text) < min_chars
            or len(text) < min_chars
        )
        merge_due_to_sentence = not _is_strong_sentence_end(current_text)
        merge_due_to_gap = gap_ms <= max_gap_ms
        within_limits = (
            buffer_count < max_merge_blocks
            and merged_duration_ms <= max_merged_duration_ms
            and merged_chars <= max_merged_chars
        )

        merge_due_to_continuation = text[:1].islower() or text[:1] in {",", ".", ":", ";", "-", "(", "["}
        if within_limits and (
            merge_due_to_short
            or (merge_due_to_sentence and merge_due_to_gap)
            or (merge_due_to_continuation and merge_due_to_gap)
        ):
            buffer_item.text = (current_text + "\n" + text).strip()
            buffer_item.end = sub.end
            buffer_count += 1
            continue

        flush_buffer()
        buffer_item = pysrt.SubRipItem(index=1, start=sub.start, end=sub.end, text=text)
        buffer_count = 1

    flush_buffer()
    normalized_subs = pysrt.SubRipFile(items=normalized)
    return normalized_subs, {
        "original_blocks": len(subs),
        "normalized_blocks": len(normalized_subs),
        "merged_blocks": max(0, len(subs) - len(normalized_subs)),
    }
