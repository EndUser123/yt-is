"""Retrieval routing (D-gate: intent-based, df-independent).

Intent classes (df NEVER determines intent — D-gate core principle):
  EXACT_STRICT   explicit exact=true or quoted literal: literal matches
                 only, no semantic fill.
  IDENTIFIER     whole-query identifier syntax at ANY df: literal
                 containment priority; when literal candidates >= K, rank
                 WITHIN the literal set by semantic order; else literal
                 first with semantic fill.
  SEMANTIC       everything else (incl. short natural queries):
                 dense + learned sparse, weighted fusion (D_weighted).

Fusion policies: A (defect baseline), B (exact only), C (pin+fill),
D (weighted), and I (identifier-priority: the D-gate production rule).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

FTS_DB = Path("P:/.data/yt-is/ef/fts5.sqlite")
W_EXACT = 3.0               # policy D exact-leg RRF weight
RRF_K = 60
# Ambiguous single-word class (plain alphabetic, only case structure):
# df decides — rare strings like hizoJc are identifiers; conventional
# words like YouTube/Google/Python are semantic (operator examples).
DF_WORD_ID_MAX = 1000

# Strong identifier structure: digits, punctuation joins, snake_case, CLI
# flags — identifier intent at ANY df (D-gate rule 2).
_STRONG_IDENT = re.compile(
    r"""^(?:
        --?[A-Za-z][A-Za-z0-9_-]*                    # CLI flags: --resume-worker
      | [A-Za-z0-9]+(?:[-._/:][A-Za-z0-9]+)+         # joined: gsd-map-codebase, 2.1.156, Class.method
      | [a-z]+(?:_[a-z0-9]+)+                        # snake_case
      | [A-Za-z]+-[0-9][A-Za-z0-9-]*                 # BF-16, GPT-4o
      | [A-Z]{2,}[A-Za-z0-9]*                        # ALLCAPS: RPC9, ERROR_RESOURCE_EXHAUSTED
      | [A-Za-z]+[0-9][A-Za-z0-9-]*                  # GR0000tn2, Qwen3-Reranker-4B
      | 0x[0-9a-fA-F]+                               # hex literals
    )$""", re.VERBOSE)

# Weak (ambiguous) shape: single alphabetic word with internal case shift
# only (hizoJc, YouTube, OpenAI) — df tiebreak applies.
_WEAK_IDENT = re.compile(r"^[A-Za-z]+[a-z][A-Z][A-Za-z0-9]*$")


@dataclass(frozen=True)
class Routing:
    intent: str            # "exact_strict" | "identifier" | "semantic"
    reason: str


def _strip_quotes(q: str) -> str:
    q = q.strip()
    if len(q) >= 2 and q[0] == q[-1] and q[0] in "\"'":
        return q[1:-1]
    return q


def identifier_shaped(query: str) -> bool:
    """True when the query is strong- or weak-shaped like an identifier."""
    t = _strip_quotes(query).strip()
    return bool(_STRONG_IDENT.match(t) or _WEAK_IDENT.match(t))


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


def is_quoted_literal(query: str) -> bool:
    q = query.strip()
    return len(q) >= 2 and q[0] == q[-1] and q[0] in "\"'"


def sanitize_fts_query(query: str) -> str:
    """FTS5 quoted-term match string; embedded double quotes stripped."""
    terms = [t.replace('"', "") for t in _strip_quotes(query).split()]
    terms = [t for t in terms if t]
    return " ".join(f'"{t}"' for t in terms)


def classify(query: str, exact: bool | None = None,
             df: int | None = None) -> Routing:
    """Intent classification. Strong structural shapes -> identifier at
    ANY df. Ambiguous single alphabetic words (only case structure) use a
    df tiebreak: rare => identifier, conventional => semantic (operator
    examples: hizoJc identifier; YouTube/Google/Python semantic)."""
    if exact is True:
        return Routing("exact_strict", "explicit exact mode")
    if is_quoted_literal(query):
        return Routing("exact_strict", "quoted literal")
    t = _strip_quotes(query).strip()
    if _STRONG_IDENT.match(t):
        return Routing("identifier", "strong identifier shape (any df)")
    if _WEAK_IDENT.match(t):
        d = document_frequency(query) if df is None else df
        if d <= DF_WORD_ID_MAX:
            return Routing("identifier", f"weak shape, df={d} rare")
        return Routing("semantic", f"conventional word, df={d}")
    return Routing("semantic", "natural language")


# ---------- fusion policies ----------

def fuse_equal_rrf(legs: list[list[str]], top: int,
                   exact_leg_idx: int = -1) -> list[str]:
    """Policy A: equal-weight RRF (defective baseline; uniform signature)."""
    score: dict[str, float] = {}
    for leg in legs:
        for rk, cid in enumerate(leg):
            score[cid] = score.get(cid, 0.0) + 1.0 / (RRF_K + rk + 1)
    return [c for c, _ in sorted(score.items(), key=lambda kv: -kv[1])[:top]]


def fuse_exact_only(legs: list[list[str]], top: int,
                    exact_leg_idx: int = -1) -> list[str]:
    return legs[exact_leg_idx][:top]


def fuse_containment_priority(legs: list[list[str]], top: int,
                              exact_leg_idx: int = -1) -> list[str]:
    """Policy C: literal hits pinned, semantic fills."""
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
    if weights is None:
        weights = [1.0] * len(legs)
        weights[exact_leg_idx] = W_EXACT
    score: dict[str, float] = {}
    for leg, w in zip(legs, weights):
        for rk, cid in enumerate(leg):
            score[cid] = score.get(cid, 0.0) + w / (RRF_K + rk + 1)
    return [c for c, _ in sorted(score.items(), key=lambda kv: -kv[1])[:top]]


def fuse_identifier_priority(literal_leg: list[str], semantic_leg: list[str],
                             top: int) -> list[str]:
    """Policy I (D-gate production rule for IDENTIFIER intent):
    - literal candidates only, CONTAINMENT guaranteed;
    - ranked WITHIN the literal set by semantic order (semantic rank as
      primary, lexical order as tiebreak for literals the semantic leg
      did not surface);
    - semantic FILL only when literal candidates < top."""
    literals = literal_leg[:max(top, len(literal_leg))]
    sem_rank = {cid: i for i, cid in enumerate(semantic_leg)}
    if len(literals) >= top:
        ranked = sorted(literals,
                        key=lambda c: (sem_rank.get(c, 1 << 30),
                                       literals.index(c)))
        return ranked[:top]
    out = sorted(literals, key=lambda c: (sem_rank.get(c, 1 << 30),
                                          literals.index(c)))
    seen = set(out)
    for cid in semantic_leg:
        if len(out) >= top:
            break
        if cid not in seen:
            out.append(cid)
            seen.add(cid)
    return out[:top]


POLICIES = {
    "A_equal_rrf": fuse_equal_rrf,
    "B_exact_only": fuse_exact_only,
    "C_containment_priority": fuse_containment_priority,
    "D_weighted": fuse_weighted,
}
