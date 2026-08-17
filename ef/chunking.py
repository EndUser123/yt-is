"""Chunker: split a transcript into overlapping, char-addressable chunks.

Provenance rule (D005): every chunk records [start_char, end_char) into the
authority transcript so any projection hit can be reopened exactly.
Strategy: target-size windows snapped to sentence boundaries with a small
overlap; deterministic given the same text and parameters (BuildSpec input).
"""

from __future__ import annotations

import re

from .contracts import ChunkRecord

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
WS = re.compile(r"\s+")
# ~4 chars/token heuristic is good enough for budget accounting
CHARS_PER_TOKEN = 4


def chunk_transcript(eu_id: str, text: str,
                     target_chars: int = 1100,
                     overlap_chars: int = 150,
                     min_chars: int = 200) -> list[ChunkRecord]:
    """Split text into chunks of ~target_chars at sentence boundaries.

    - Chunks never exceed target_chars + one sentence.
    - Tail shorter than min_chars is merged into the previous chunk.
    - Overlap is re-snapped to a sentence start when possible.
    """
    if target_chars <= min_chars:
        raise ValueError("target_chars must exceed min_chars")
    text = text.replace("\r\n", "\n")
    sentences = [s for s in SENTENCE_END.split(text) if s.strip()]
    if not sentences:
        sentences = [text] if text.strip() else []

    # Pack sentences into windows
    windows: list[tuple[int, int]] = []   # (start_char, end_char) in text
    pos = 0                                # char cursor into original text
    sent_spans: list[tuple[int, int, str]] = []
    for s in sentences:
        idx = text.find(s, pos)
        sent_spans.append((idx, idx + len(s), s))
        pos = idx + len(s)

    cur_start, cur_end = None, None
    for (s_start, s_end, s_text) in sent_spans:
        if cur_start is None:
            cur_start, cur_end = s_start, s_end
        elif s_end - cur_start <= target_chars:
            cur_end = s_end
        else:
            windows.append((cur_start, cur_end))
            # next window starts at overlap before cur_end, snapped to a
            # sentence start >= cur_end - overlap_chars
            overlap_from = cur_end - overlap_chars
            nxt = s_start
            for (ss, se, st) in sent_spans:
                if ss >= overlap_from and ss < s_end:
                    nxt = ss
                    break
            cur_start, cur_end = nxt, s_end
    if cur_start is not None:
        windows.append((cur_start, cur_end))

    # Merge degenerate tail
    if len(windows) >= 2 and (windows[-1][1] - windows[-1][0]) < min_chars:
        prev = windows[-2]
        windows[-2] = (prev[0], windows[-1][1])
        windows.pop()

    chunks: list[ChunkRecord] = []
    for ordinal, (start, end) in enumerate(windows):
        span = text[start:end]
        approx_tokens = max(1, len(WS.split(span)))
        chunks.append(ChunkRecord(
            chunk_id=f"{eu_id}#{ordinal:05d}",
            eu_id=eu_id,
            ordinal=ordinal,
            start_char=start,
            end_char=end,
            text=span,
            approx_tokens=approx_tokens,
        ).validate())
    return chunks
