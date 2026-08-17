"""Retrieval routing (A" sections 7, 9, 10).

Three intents:
  EXACT     the query IS an identifier (whole query matches identifier
            syntax, or explicit exact=true / quoted literal) AND its
            literal document frequency is low -> exact lane authoritative.
  SEMANTIC  everything else, including short natural queries ("cook rice")
            and common lexical terms ("YouTube") — semantic retrieval is
            never disabled by query length alone (A" 7.2 explicit rule).
  A common lexical term (high df) routes SEMANTIC: thousands of literal
  matches exist, so no single occurrence deserves authority (A" 7.3).

Policies (A" 9):
  A equal-RRF (the defective baseline, kept for comparison)
  B exact-only when EXACT intent fires
  C containment-priority: exact-literal hits pinned ahead, semantic fills
  D weighted fusion: exact leg weight W_EXACT (justified alternative)

Explicit exact mode (A" 10): exact=True or a quoted query ("literal")
forces EXACT regardless of shape or df.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

FTS_DB = Path("P:/.data/yt-is/ef/fts5.sqlite")
DF_EXACT_MAX = 100          # rare-literal ceiling (dev builder's cut: 61)
W_EXACT = 3.0               # policy D exact-leg RRF weight
RRF_K = 60

# Whole-query identifier syntax: the entire query (sans surrounding
# quotes/whitespace) must be ONE identifier token.
_IDENT_TOKEN = re.compile(
    r"""^(?:
        --?[A-Za-z][A-Za-z0-9_-]*                    # CLI flags: --resume-worker
      | [A-Za-z][A-Za-z0-9]*(?:[._/:][A-Za-z0-9]+)+  # dots/paths: ClassName.method, a.b.com
      | [a-z]+(?:_[a-z0-9]+)+                        # snake_case
      | [A-Za-z]+[a-z][A-Z][A-Za-z0-9]*              # camelCase: hizoJc, OpenAI
      | [A-Za-z]+-[0-9][A-Za-z0-9-]*                 # letter-hyphen-digit: BF-16, GPT-4o
      | [A-Z]{2,}[A-Za-z0-9]*                        # ALLCAPS: RPC9, ERROR_RESOURCE_EXHAUSTED
      | [A-Za-z]+[0-9][A-Za-z0-9-]*                  # GR0000tn2, Qwen3-Reranker-4B
      | 0x[0-9a-fA-F]+                               # hex literals
    )$""", re.VERBOSE)


@dataclass(frozen=True)
class Routing:
    intent: str            # "exact" | "semantic"
    reason: str


def _strip_quotes(q: str) -> str:
    q = q.strip()
    if len(q) >= 2 and q[0] == q[-1] and q[0] in "\"'":
        return q[1:-1]
    return q


def identifier_shaped(query: str) -> bool:
    """True when the WHOLE query is a single identifier token."""
    return bool(_IDENT_TOKEN.match(_strip_quotes(query).strip()))


def is_quoted_literal(query: str) -> bool:
    q = query.strip()
    return len(q) >= 2 and q[0] == q[-1] and q[0] in "\"'"


def document_frequency(query: str, fts_db: Path = FTS_DB) -> int:
    """Chunks containing the literal query token (FTS5 quoted match)."""
    tok = sanitize_fts_query(query)
    if not tok:
        return 0
    try:
        conn = sqlite3.connect(f"file:{fts_db}?mode=ro", uri=True)
        try:
            return conn.execute(
                "select count(*) from chunks where chunks match ?",
                (tok,)).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def sanitize_fts_query(query: str) -> str:
    """FTS5 quoted-term match string; embedded double quotes stripped."""
    terms = [t.replace('"', "") for t in _strip_quotes(query).split()]
    terms = [t for t in terms if t]
    return " ".join(f'"{t}"' for t in terms)


def classify(query: str, exact: bool | None = None,
             df: int | None = None, fts_db: Path = FTS_DB) -> Routing:
    """Route a query. exact=True forces EXACT (explicit mode). df may be
    supplied by callers that already know it; otherwise looked up only for
    identifier-shaped queries (single FTS5 count, ~ms on the exact lane)."""
    if exact is True:
        return Routing("exact", "explicit exact mode")
    if is_quoted_literal(query):
        return Routing("exact", "quoted literal")
    if not identifier_shaped(query):
        return Routing("semantic", "natural language (length-independent)")
    if df is None:
        df = document_frequency(query, fts_db)
    if df <= DF_EXACT_MAX:
        return Routing("exact", f"identifier-shaped, df={df}<= {DF_EXACT_MAX}")
    return Routing("semantic", f"identifier-shaped but common, df={df}")


# ---------- fusion policies ----------

def fuse_equal_rrf(legs: list[list[str]], top: int,
                   exact_leg_idx: int = -1) -> list[str]:
    """Policy A: equal-weight RRF over chunk-id legs (defective baseline;
    exact_leg_idx accepted-and-ignored for uniform policy invocation)."""
    score: dict[str, float] = {}
    for leg in legs:
        for rk, cid in enumerate(leg):
            score[cid] = score.get(cid, 0.0) + 1.0 / (RRF_K + rk + 1)
    return [c for c, _ in sorted(score.items(), key=lambda kv: -kv[1])[:top]]


def fuse_exact_only(legs: list[list[str]], top: int,
                    exact_leg_idx: int = -1) -> list[str]:
    """Policy B: exact leg only."""
    return legs[exact_leg_idx][:top]


def fuse_containment_priority(legs: list[list[str]], top: int,
                              exact_leg_idx: int = -1) -> list[str]:
    """Policy C: exact-literal hits pinned first (in their lexical order),
    semantic fills the remaining slots. Deduplicates."""
    exact_leg = legs[exact_leg_idx]
    out = list(exact_leg[:top])
    seen = set(out)
    for leg in legs[:exact_leg_idx] + legs[exact_leg_idx + 1:]:
        for cid in leg:
            if len(out) >= top:
                break
            if cid not in seen:
                out.append(cid)
                seen.add(cid)
    return out[:top]


def fuse_weighted(legs: list[list[str]], top: int,
                  exact_leg_idx: int = -1,
                  weights: list[float] | None = None) -> list[str]:
    """Policy D: weighted RRF; exact leg carries W_EXACT (default 3.0),
    others 1.0."""
    if weights is None:
        weights = [1.0] * len(legs)
        weights[exact_leg_idx] = W_EXACT
    score: dict[str, float] = {}
    for leg, w in zip(legs, weights):
        for rk, cid in enumerate(leg):
            score[cid] = score.get(cid, 0.0) + w / (RRF_K + rk + 1)
    return [c for c, _ in sorted(score.items(), key=lambda kv: -kv[1])[:top]]


POLICIES = {
    "A_equal_rrf": fuse_equal_rrf,
    "B_exact_only": fuse_exact_only,
    "C_containment_priority": fuse_containment_priority,
    "D_weighted": fuse_weighted,
}
