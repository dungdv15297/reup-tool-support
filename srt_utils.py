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
