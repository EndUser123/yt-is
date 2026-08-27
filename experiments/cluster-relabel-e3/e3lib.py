"""E3 shared library — frozen-snapshot access + mechanical strata functions."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

EF_DATA = Path("P:/.data/yt-is/ef/cluster-relabel-e3")
SNAPSHOT = EF_DATA / "membership-frozen.jsonl.gz"
FREEZE = json.loads((EF_DATA / "FREEZE.json").read_text(encoding="utf-8"))
FREEZE_SHA256 = FREEZE["sha256_canonical_jsonl"]
SEED_SAMPLE = "e3-sample-v1"

# clustering.py's verbatim stopword set (Arm A must match production exactly)
CLUSTERING_STOPWORDS = {
    "the", "a", "an", "to", "for", "of", "in", "on", "with", "and", "or",
    "is", "are", "how", "what", "why", "your", "you", "it", "this",
    "that", "from", "at", "by", "be", "as", "not", "but", "can", "will",
    "best", "new", "using", "use", "make", "build", "get", "part",
    "video", "tutorial", "guide", "course", "full", "complete", "learn",
}
# Preregistered generic vocabulary extension (prereg §strata)
GENERIC_VOCAB_EXTRA = {
    "top", "tips", "episode", "shorts", "mini", "max", "pro", "free",
    "live", "today", "update", "news", "world", "life", "time", "times",
    "day", "days", "year", "years", "hour", "hours", "min", "mins", "ep",
}
TOKEN_STRIP = re.compile(r"[^a-z]+")
CSTOPWORD_HEADERS = ("data:text/plain;base64,",)


@dataclass
class Video:
    video_id: str
    title: str
    source: str
    channel_id: str
    channel_title: str
    published_at: str


@dataclass
class FrozenCluster:
    cluster_id: int
    label: str
    top_terms: list[str]
    member_count_chunks: int
    video_count_stored: int
    videos: list[Video] = field(repr=False)
    point_ids: list[tuple[str, int]] = field(repr=False)

    @property
    def n_docs(self) -> int:
        return len(self.videos)


def load_freeze() -> dict[int, FrozenCluster]:
    clusters: dict[int, FrozenCluster] = {}
    with gzip.open(SNAPSHOT, "rt", encoding="utf-8") as fh:
        fh.readline()  # header line
        for line in fh:
            rec = json.loads(line)
            clusters[rec["cluster_id"]] = FrozenCluster(
                cluster_id=rec["cluster_id"],
                label=rec["label"],
                top_terms=rec["top_terms"],
                member_count_chunks=rec["member_count_chunks"],
                video_count_stored=rec["video_count_stored"],
                videos=[Video(**v) for v in rec["videos"]],
                point_ids=[(p[0], p[1]) for p in rec["point_ids"]],
            )
    return clusters


def publisher_identity(v: Video) -> str:
    if v.source == "discord":
        return f"guild:{v.channel_title}"
    if not v.channel_id or v.source in ("hackernews", "newsletter"):
        return "unknown"
    return v.channel_id


def diversity_counts(c: FrozenCluster) -> Counter:
    return Counter(publisher_identity(v) for v in c.videos)


def size_bucket(video_count: int) -> str:
    if video_count < 100:
        return "small"
    if video_count < 1000:
        return "medium"
    return "large"


YT_FAMILY_SOURCES = {"notebooklm", "ytdlp", "selenium", "whisper", "youtube", ""}


def source_families(c: FrozenCluster) -> set[str]:
    fams = set()
    for v in c.videos:
        fams.add(v.source if v.source not in YT_FAMILY_SOURCES else "youtube")
    return fams


def _label_tokens(label: str) -> list[str]:
    out = []
    for tok in label.split():
        t = TOKEN_STRIP.sub("", tok.casefold())
        if t:
            out.append(t)
    return out


def generic_flag(c: FrozenCluster) -> tuple[bool, str]:
    """G2 requires CASEFOLD_COUNTS to be populated first
    (build_label_dupe_index over the whole population)."""
    toks = _label_tokens(c.label)
    generic_core = CLUSTERING_STOPWORDS | GENERIC_VOCAB_EXTRA
    g1 = bool(toks) and all(
        (t in generic_core) or len(t) <= 2 or t.isdigit()
        for t in toks)
    cf = c.label.strip().casefold()
    g2 = CASEFOLD_COUNTS.get(cf, 0) >= 2
    if g1 and g2:
        return True, "G1+G2"
    if g1:
        return True, "G1"
    if g2:
        return True, "G2"
    return False, ""


CASEFOLD_COUNTS: Counter = Counter()


def build_label_dupe_index(clusters: dict[int, FrozenCluster]) -> None:
    CASEFOLD_COUNTS.clear()
    for c in clusters.values():
        CASEFOLD_COUNTS[c.label.strip().casefold()] += 1


import re as _re
import unicodedata as _unicodedata


def artifact_flag(c: FrozenCluster) -> tuple[bool, str]:
    """Prereg-v2 mechanical artifact-suspect rules on the CURRENT label."""
    lab = c.label
    hits = []
    if _unicodedata.normalize("NFKC", lab) != lab:
        hits.append("UNI")
    if any(ord(ch) > 0x2000 for ch in lab):
        hits.append("CJKJUNK")
    if _re.search(r"\d", lab):
        hits.append("DIGIT")
    if len(_label_tokens(lab)) <= 1:
        hits.append("SHORT")
    return (True, "+".join(hits)) if hits else (False, "")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def recency_stats(c: FrozenCluster) -> dict:
    dated = [v for v in c.videos if v.published_at]
    recent = [v for v in dated if v.published_at >= "2026-01-01"]
    ds = (len(dated) / len(c.videos)) if c.videos else 0.0
    rs = (len(recent) / len(dated)) if dated else 0.0
    heavy = bool(ds >= 0.2 and rs >= 0.5)
    return {"dated_share": round(ds, 4), "recent_share_of_dated": round(rs, 4),
            "recency_heavy": heavy}


def source_mix(c: FrozenCluster) -> dict[str, int]:
    mix = Counter(
        {"youtube" if v.source in
         ("notebooklm", "ytdlp", "selenium", "whisper", "youtube") else v.source
         or "unknown" for v in c.videos})
    return dict(mix.most_common())
