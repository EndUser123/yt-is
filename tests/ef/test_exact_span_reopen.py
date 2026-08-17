"""A" section 5: exact-span reopenability. authority[start:end] must equal
the canonical chunk text, across seven case classes. Uses a synthetic
authority DB (reopen_span accepts a db path)."""

import sqlite3
import sys
import unicodedata
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ef import authority, chunking  # noqa: E402


def _make_authority(tmp_path, texts: dict[str, str]) -> Path:
    db = tmp_path / "auth.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("create table transcript_cache (cache_key text primary key,"
                 " video_id text, lang text, source text, transcript text,"
                 " metadata_json text default '{}', cached_at text,"
                 " terminal_id text default 'run')")
    for vid, txt in texts.items():
        conn.execute("insert into transcript_cache values (?,?,?,?,?,?,?,?)",
                     (f"{vid}:en:t", vid, "en", "t", txt, "{}",
                      "2026-08-17T00:00:00Z", "run"))
    conn.commit()
    conn.close()
    return db


def _check_all_chunks(db: Path, vid: str, text: str):
    chunks = chunking.chunk_transcript(f"{vid}:transcript", text)
    assert chunks, vid
    for ch in chunks:
        reopened = authority.reopen_span(
            vid, ch.start_char, ch.end_char, transcripts_db=db)
        assert reopened == ch.text, f"{ch.chunk_id}: span mismatch"


def test_normal_and_boundary_cases(tmp_path):
    text = ("This is a normal transcript. " * 40) + \
           "It ends with a final sentence about results."
    db = _make_authority(tmp_path, {"v_normal": text})
    _check_all_chunks(db, "v_normal", text)


def test_beginning_and_end_of_transcript(tmp_path):
    text = "Starts immediately. " + ("Middle filler sentence here. " * 30) + \
           "Ends exactly at the last word."
    db = _make_authority(tmp_path, {"v_bounds": text})
    _check_all_chunks(db, "v_bounds", text)
    # first chunk starts at 0; last chunk ends at len(text)
    chunks = chunking.chunk_transcript("v_bounds:transcript", text)
    assert chunks[0].start_char == 0
    assert chunks[-1].end_char == len(text)


def test_overlap_boundaries_are_exact(tmp_path):
    text = "Sentence one here. Sentence two there. " * 25
    db = _make_authority(tmp_path, {"v_overlap": text})
    _check_all_chunks(db, "v_overlap", text)
    # consecutive chunks overlap; both must reopen exactly
    chunks = chunking.chunk_transcript("v_overlap:transcript", text)
    for a, b in zip(chunks, chunks[1:]):
        assert b.start_char <= a.end_char  # overlap exists
        assert authority.reopen_span("v_overlap", b.start_char, b.end_char,
                                     transcripts_db=db) == b.text


def test_short_chunks(tmp_path):
    text = "One short transcript with three sentences. Second. Third."
    db = _make_authority(tmp_path, {"v_short": text})
    _check_all_chunks(db, "v_short", text)


def test_unicode_non_ascii(tmp_path):
    text = ("Résumé naïve — em-dash, 中文 mixed with emoji 🚀 and quotes "
            "«guillemets». Ünïcödé sentence continues here. ") * 8
    # normalize to NFC like real data would vary; keep as-is for exactness
    db = _make_authority(tmp_path, {"v_uni": text})
    _check_all_chunks(db, "v_uni", text)


def test_metadata_incomplete_transcript(tmp_path):
    # Case-A rows reopen the same way; metadata completeness is irrelevant
    # to span exactness
    text = "Transcript with missing title but full text. " * 20
    db = _make_authority(tmp_path, {"--incomplete1": text})
    _check_all_chunks(db, "--incomplete1", text)


def test_hash_route_equivalence(tmp_path):
    # canonical hash of the reopened slice equals the chunk's canonical text
    import hashlib
    text = "Hash equivalence sentence. " * 20
    db = _make_authority(tmp_path, {"v_hash": text})
    for ch in chunking.chunk_transcript("v_hash:transcript", text):
        reopened = authority.reopen_span("v_hash", ch.start_char, ch.end_char,
                                         transcripts_db=db)
        assert hashlib.sha256(reopened.encode()).hexdigest() == \
            hashlib.sha256(ch.text.encode()).hexdigest()
