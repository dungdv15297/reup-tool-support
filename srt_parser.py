"""
SRT Parser — Parse and rebuild .srt / .txt files.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SrtEntry:
    index: int
    start: str  # "00:00:01,000"
    end: str    # "00:00:03,500"
    text: str

    def to_srt_block(self) -> str:
        return f"{self.index}\n{self.start} --> {self.end}\n{self.text}\n"


def parse_srt(content: str) -> List[SrtEntry]:
    """Parse SRT content string into a list of SrtEntry objects."""
    entries = []
    # Split by blank lines (handles \r\n and \n)
    blocks = re.split(r'\n\s*\n', content.strip())

    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue

        try:
            index = int(lines[0].strip())
        except ValueError:
            continue

        # Parse timestamp line
        ts_match = re.match(
            r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})',
            lines[1].strip()
        )
        if not ts_match:
            continue

        start = ts_match.group(1)
        end = ts_match.group(2)
        text = '\n'.join(lines[2:]).strip()

        entries.append(SrtEntry(index=index, start=start, end=end, text=text))

    return entries


def rebuild_srt(entries: List[SrtEntry]) -> str:
    """Rebuild SRT content string from a list of SrtEntry objects."""
    blocks = []
    for i, entry in enumerate(entries, 1):
        entry.index = i
        blocks.append(entry.to_srt_block())
    return '\n'.join(blocks)


def parse_txt(content: str) -> List[SrtEntry]:
    """Parse plain text file (each line = 1 entry) into SrtEntry-like objects."""
    entries = []
    lines = content.strip().split('\n')
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if line:
            entries.append(SrtEntry(index=i, start="", end="", text=line))
    return entries


def rebuild_txt(entries: List[SrtEntry]) -> str:
    """Rebuild plain text from entries."""
    return '\n'.join(entry.text for entry in entries)


def chunk_entries(entries: List[SrtEntry], chunk_size: int = 15) -> List[List[SrtEntry]]:
    """
    Group SRT entries into chunks for batch translation.
    Each chunk contains up to `chunk_size` entries to give LLM enough context.
    """
    chunks = []
    for i in range(0, len(entries), chunk_size):
        chunks.append(entries[i:i + chunk_size])
    return chunks
