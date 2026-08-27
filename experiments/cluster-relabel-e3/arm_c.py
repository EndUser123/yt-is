"""Arm C — generative short-label representation via the local go-llm-proxy.

Preregistered configuration: model Hy3 (codex-opencode-zen-hy3-free),
temperature=0, strict short-label contract. Client: existing
packages/go-llm-proxy-integration/scripts/zen.py `call()` (reused, not
adapted beyond adding the temperature field and retry handling).
"""
from __future__ import annotations

import importlib.util
import json
import re
import time
from pathlib import Path

ZEN_PATH = Path("P:/packages/go-llm-proxy-integration/scripts/zen.py")
MODEL = "codex-opencode-zen-hy3-free"

_spec = importlib.util.spec_from_file_location("e3_zen", ZEN_PATH)
zen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(zen)

PROMPT_TEMPLATE = """You name topic clusters for an information system.

Evidence for the cluster:
{titles_block}

Top discriminative keywords:
{keywords_block}

Produce ONE short topic label for this cluster.

Hard rules:
- at most 6 words
- it names the shared dominant subject of the documents
- not a sentence; no punctuation except hyphens/apostrophes inside words
- no meta/UI words: video, podcast, episode, tutorial, guide, course,
  full, best, top, new, part, shorts, tips, review, hour, minute
- no channel names, no person names unless they ARE the subject,
  no numbers, dates, emojis, or decorated unicode characters
- stay strictly within the scope of the evidence; do not generalize

Reply with ONLY the label text."""


def build_prompt(display_titles: list[str], keywords: list[str]) -> str:
    tl = "\n".join(f"- {t}" for t in display_titles)
    kl = ", ".join(keywords[:20]) if keywords else "(none)"
    return PROMPT_TEMPLATE.format(titles_block=tl, keywords_block=kl)


def clean_output(text: str) -> str:
    s = text.strip().strip("\"'` ")
    s = re.sub(r"^(label|topic label)\s*:\s*", "", s, flags=re.I).strip()
    s = s.splitlines()[0].strip() if s else ""
    words = s.split()
    if len(words) > 8:          # allow slight overflow then hard-trim contract breach flag upstream
        s = " ".join(words[:8])
    return s


def generate(prompt: str, attempts: int = 5):
    """Returns (status, label|None). Retries transport/429/5xx/empty.
    Budget escalation here because zen.py's own escalation only fires on
    response.completed-with-reasoning-only; a response.incomplete event
    (reasoning exceeded budget mid-stream) returns status=None untouched,
    which is this workload's dominant failure."""
    delay = 2.0
    last_err = None
    budget = 16000
    for _ in range(attempts):
        try:
            out = zen.call(
                MODEL,
                [{"type": "input_text", "text": prompt}],
                max_tokens=budget, timeout=300)
        except SystemExit as e:                      # zen.call exits on HTTP errors
            last_err = f"SystemExit code={e.code}"
            time.sleep(delay if e.code == 2 else min(delay, 10))
            delay *= 1.7
            continue
        except Exception as e:                       # transport-level
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(min(delay, 20))
            delay *= 1.7
            continue
        # zen.call returns 3-tuple (older tree) or (status, text, calls,
        # empty_reason) after the reasoning-budget-escalation change.
        status, text = out[0], out[1]
        empty_reason = out[3] if len(out) > 3 else ""
        if status == 0 or (status == "completed" and text.strip()):
            label = clean_output(text)
            if not label:
                last_err = "completed-empty-after-clean"
                time.sleep(1.5)
                continue
            return 0, label
        last_err = (f"status={status} body={text[:120]} "
                    f"empty_reason={empty_reason} budget={budget}")
        budget = min(budget * 2, 128000)             # escalate: incomplete==token ceiling
        time.sleep(min(delay, 15))
        delay *= 1.5
    return 1, f"{last_err}"
