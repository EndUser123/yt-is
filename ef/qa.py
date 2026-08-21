"""Natural-language Q&A over the yt-is corpus, with citations.

Retrieves the most relevant chunks via the warm query service, then asks
a provider to answer strictly from that context. Provider chain
(operator-approved D8, multi-provider):
  1. agy CLI (local Gemini CLI; operator's existing quota)
  2. Gemini API key (GEMINI_API_KEY)
  3. OpenRouter key (OPENROUTER_API_KEY — any model)

Answers carry numbered citations; each source is a real corpus chunk with
its title, URL, and snippet. If retrieval finds nothing relevant, we say
so instead of guessing.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

RETRIEVE_TOP_K = 8
MAX_CONTEXT_CHARS = 24000

SYSTEM_PROMPT = (
    "You answer questions using ONLY the provided context excerpts from a "
    "personal knowledge base of transcripts, posts, and articles. Cite "
    "claims with [n] referring to excerpt numbers. If the context is "
    "insufficient, say exactly what's missing. Be concise and specific."
)


def retrieve(question: str, top_k: int = RETRIEVE_TOP_K):
    """Top-k corpus chunks for the question via the warm service."""
    import urllib.parse
    params = urllib.parse.urlencode(
        {"q": question, "top_k": top_k, "format": "json"})
    with urllib.request.urlopen(
            f"http://127.0.0.1:6391/query?{params}", timeout=120) as r:
        data = json.loads(r.read())
    return data.get("results", [])


def build_context(results: list[dict]) -> str:
    parts, used = [], 0
    for i, r in enumerate(results, 1):
        snippet = (r.get("snippet") or "")[:1200]
        block = (f"[{i}] {r.get('title') or r.get('video_id', 'untitled')}\n"
                 f"{snippet}")
        if used + len(block) > MAX_CONTEXT_CHARS:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def _real_answer(text: str) -> bool:
    """Quality gate for agy output (same failure class as gemini_extract:
    agy sometimes meta-answers about its own CLI instead of the prompt)."""
    lowered = text.lower()
    if "error" in lowered[:200] or "how can i help" in lowered[:60]:
        return False
    meta_markers = ("--print-timeout", "--print", "--dangerously-skip",
                    "antigravity cli", "argparse", "click", "cli flag")
    return not any(m in lowered[:300] for m in meta_markers)


def _ask_agy(question: str, context: str) -> str | None:
    """Same invocation contract as csf/visual/gemini_extract.py (proven):
    prompt on stdin, --print with explicit timeout, no positional arg."""
    import shutil
    from pathlib import Path as _Path
    agy = shutil.which("agy")
    if not agy:
        return None
    try:
        proc = subprocess.run(
            [agy, "--print", "--print-timeout", "3m"],
            input=f"{SYSTEM_PROMPT}\n\nCONTEXT:\n{context}\n\n"
                  f"QUESTION: {question}",
            capture_output=True, text=True, timeout=240,
            cwd=str(_Path.home()))
        out = (proc.stdout or "").strip()
        if proc.returncode == 0 and len(out) > 20 and _real_answer(out):
            return out
    except Exception:
        pass
    return None


def _ask_gemini_api(question: str, context: str) -> str | None:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return None
    try:
        body = json.dumps({
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"parts": [{"text":
                f"CONTEXT:\n{context}\n\nQUESTION: {question}"}]}],
            "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.2},
        }).encode()
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash:generateContent?key=" + key,
            data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        text = "".join(p.get("text", "") for p in
                       data.get("candidates", [{}])[0]
                       .get("content", {}).get("parts", []))
        return text.strip() or None
    except Exception:
        return None


def _ask_openrouter(question: str, context: str) -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return None
    try:
        body = json.dumps({
            "model": "google/gemini-2.5-flash",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content":
                    f"CONTEXT:\n{context}\n\nQUESTION: {question}"},
            ],
        }).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json",
                                "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"].strip() or None
    except Exception:
        return None


def _ask_codex(question: str, context: str) -> str | None:
    """OpenAI Codex CLI (rides the operator's login). Needs node on PATH —
    it lives in a per-user dir the service shell doesn't inherit."""
    import shutil
    codex = shutil.which("codex")
    if not codex:
        return None
    env = dict(os.environ)
    node_dir = Path(r"C:/Users/brsth/AppData/Local/Programs/nodejs")
    npm_dir = Path(r"C:/Users/brsth/AppData/Roaming/npm")
    for d in (node_dir, npm_dir):
        env["PATH"] = str(d) + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.run(
            [codex, "exec", "--skip-git-repo-check", "-"],
            input=f"{SYSTEM_PROMPT}\n\nCONTEXT:\n{context}\n\n"
                  f"QUESTION: {question}\n\nReply with only the answer.",
            capture_output=True, text=True, timeout=240, env=env)
        out = (proc.stdout or "").strip()
        if proc.returncode == 0 and out:
            lines = [l for l in out.splitlines() if l.strip()]
            # drop codex banner lines (bare token counters)
            lines = [l for l in lines
                     if not l.strip().replace(",", "").replace(" ", "").isdigit()]
            out = "\n".join(lines).strip()
        if out and len(out) > 20 and _real_answer(out):
            return out
    except Exception:
        pass
    return None


_ALL_PROVIDERS = {"codex": _ask_codex, "agy": _ask_agy,
                  "openrouter": _ask_openrouter, "gemini": _ask_gemini_api}
# Order is operator-configurable via YTIS_QA_PROVIDERS; default = local
# authenticated CLIs first (zero incremental cost), then key-based APIs.
_DEFAULT_ORDER = "codex,agy,openrouter,gemini"


def _provider_chain():
    order = os.environ.get("YTIS_QA_PROVIDERS", _DEFAULT_ORDER)
    chain = []
    for name in order.split(","):
        name = name.strip().lower()
        if name in _ALL_PROVIDERS:
            chain.append((name, _ALL_PROVIDERS[name]))
    return chain or [("openrouter", _ask_openrouter)]


def answer(question: str) -> dict:
    results = retrieve(question)
    if not results:
        return {"answer": "No relevant content found in the corpus for "
                          "that question.",
                "sources": [], "provider": None}
    context = build_context(results)
    for name, fn in _provider_chain():
        text = fn(question, context)
        if text:
            return {"answer": text,
                    "sources": [{"title": r.get("title") or "",
                                 "url": r.get("url") or "",
                                 "snippet": (r.get("snippet") or "")[:200]}
                                for r in results],
                    "provider": name}
    return {"answer": "All providers failed — check agy / API keys "
                      "(GEMINI_API_KEY, OPENROUTER_API_KEY).",
            "sources": [{"title": r.get("title") or "", "url": r.get("url") or "",
                         "snippet": (r.get("snippet") or "")[:200]}
                        for r in results],
            "provider": "none"}
