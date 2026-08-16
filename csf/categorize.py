"""LLM channel categorization with a provider chain.

Reads channel title + description, assigns a category from a fixed set.
Uses whichever provider is configured and alive, in order:

1. google-genai SDK with a GEMINI_API_KEY* key (no CLI, no OAuth)
2. OpenAI-compatible HTTP: OpenRouter, then Mistral (stdlib urllib only)

The original Gemini-CLI subprocess path was removed: the client's free
tier was discontinued ("migrate to Antigravity") and it now fails
authentication even with GEMINI_API_KEY set.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
from xml.etree import ElementTree

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CATEGORIES = [
    "AI/ML",
    "AI News",
    "Robotics",
    "Physics",
    "Mathematics",
    "Business",
    "Markets",
    "Entertainment",
    "True Crime",
    "Storytelling",
    "Education",
    "History",
    "Science",
    "Technology",
    "Software Engineering",
    "Gaming",
    "Music",
    "News",
    "Politics",
    "Finance",
    "Health",
    "Sports",
    "Lifestyle",
]

_CATEGORY_LIST = ", ".join(CATEGORIES)

# The prompt instructs the model to answer "Other" when it cannot determine a
# category. Storing that answer terminally (instead of returning None and
# retrying forever) is what lets repeated categorize passes converge: channels
# that are genuinely unclassifiable from title+description end here.
OTHER_CATEGORY = "Other"

# OpenAI-compatible fallback chain: (label, url, env key, model).
_HTTP_PROVIDERS = (
    (
        "zhipu",
        "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        ("ZAI_API_KEY", "ZHIPU_API_KEY"),
        "glm-4.5-flash",
        {"thinking": {"type": "disabled"}},
    ),
    (
        "z_ai_coding",
        "https://api.z.ai/api/coding/paas/v4/chat/completions",
        ("ZAI_CODING_KEY",),
        "glm-4.5-flash",
        {"thinking": {"type": "disabled"}},
    ),
    (
        "openrouter",
        "https://openrouter.ai/api/v1/chat/completions",
        ("OPENROUTER_API_KEY",),
        "meta-llama/llama-3.1-8b-instruct",
        {},
    ),
    (
        "nvidia",
        "https://integrate.api.nvidia.com/v1/chat/completions",
        ("NVIDIA_API_KEY",),
        "meta/llama-3.1-8b-instruct",
        {},
    ),
    (
        "mistral",
        "https://api.mistral.ai/v1/chat/completions",
        ("MISTRAL_API_KEY",),
        "mistral-small-latest",
        {},
    ),
)

# Seconds to wait before retrying a throttled request (tests zero these).
_HTTP_RETRY_DELAYS = (2.0, 8.0)

# Strict round-robin cursor over available providers: spreads sustained runs
# across providers instead of exhausting one rate limit at a time.
_rr_index = 0
_rr_lock = threading.Lock()


def _reset_provider_cache() -> None:
    """Test hook: reset the round-robin cursor and the gemini probe."""
    global _rr_index, _gemma_probe_done, _gemma_usable
    with _rr_lock:
        _rr_index = 0
    _gemma_probe_done = False
    _gemma_usable = False


def _available_providers() -> list[tuple[str, str, str, str, dict]]:
    entries = []
    for label, url, env_names, model, extra in _HTTP_PROVIDERS:
        for env_name in env_names:
            key = os.environ.get(env_name, "").strip()
            if key:
                entries.append((label, url, key, model, extra))
                break
    return entries


def _http_classify(
    url: str, key: str, model: str, prompt: str, timeout: float, extra: dict | None = None
) -> str | None:
    """One OpenAI-compatible chat completion with throttling backoff; raw text or None."""
    import time

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 24,
        "temperature": 0,
    }
    if extra:
        payload.update(extra)
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    delays = (0.0,) + _HTTP_RETRY_DELAYS
    for attempt, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read())
            message = data["choices"][0]["message"]
            content = str(message.get("content") or "").strip()
            if not content:
                # Thinking-style models can leave content empty when the
                # token budget is consumed by reasoning; retry with a larger
                # budget once rather than treating it as a hard failure.
                if "max_tokens" in payload and attempt == 0:
                    payload["max_tokens"] = 512
                    body = json.dumps(payload).encode("utf-8")
                    request = urllib.request.Request(
                        url,
                        data=body,
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                        },
                    )
                    continue
                return None
            return content
        except urllib.error.HTTPError as exc:
            # Throttle/server errors are worth another attempt; other 4xx are not.
            if exc.code not in (408, 425, 429) and not (500 <= exc.code < 600):
                return None
            if attempt == len(delays) - 1:
                return None
        except (urllib.error.URLError, KeyError, IndexError, ValueError, TimeoutError, OSError):
            return None
    return None


_gemma_probe_done = False
_gemma_usable = False


def _gemini_keys() -> list[str]:
    global _gemma_probe_done, _gemma_usable
    if _gemma_probe_done and not _gemma_usable:
        return []
    keys = []
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2"):
        value = os.environ.get(name, "").strip()
        if value:
            keys.append(value)
    return keys


def _gemini_classify(key: str, prompt: str, timeout: float) -> str | None:
    global _gemma_probe_done, _gemma_usable
    try:
        from google import genai
    except ImportError:
        return None
    try:
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model="gemini-2.0-flash", contents=prompt
        )
        _gemma_probe_done = True
        _gemma_usable = True
        return str(response.text or "").strip()
    except Exception:
        _gemma_probe_done = True
        _gemma_usable = False
        return None


def get_cached_video_titles(
    db_path, channel_url: str, max_age_s: float = 14 * 24 * 3600.0
) -> "list[str] | None":
    """Cached recent video titles for a channel, or None when absent/stale.

    Re-classification should be a DB read plus one LLM call - refetching RSS
    for titles already collected once is wasted work. Cache lives in
    channel_metadata.recent_video_titles (JSON) with video_titles_fetched_at.
    """
    import json as _json
    import sqlite3 as _sq
    from datetime import datetime, timezone as _tz

    try:
        conn = _sq.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT recent_video_titles, video_titles_fetched_at FROM channel_metadata "
            "WHERE channel_url = ?",
            (channel_url,),
        ).fetchone()
        conn.close()
    except Exception:
        return None
    if not row or not row[0] or not row[1]:
        return None
    try:
        fetched = datetime.fromisoformat(row[1])
        age = (datetime.now(_tz.utc) - fetched).total_seconds()
    except ValueError:
        return None
    if age > max_age_s:
        return None
    try:
        titles = _json.loads(row[0])
        return titles if isinstance(titles, list) else None
    except ValueError:
        return None


def store_video_titles(db_path, channel_url: str, titles: "list[str]") -> None:
    """Persist fetched video titles for future re-classification passes."""
    import json as _json
    import sqlite3 as _sq
    from datetime import datetime, timezone as _tz

    conn = _sq.connect(db_path)
    try:
        conn.execute(
            "UPDATE channel_metadata SET recent_video_titles = ?, "
            "video_titles_fetched_at = ? WHERE channel_url = ?",
            (
                _json.dumps([t[:200] for t in titles]),
                datetime.now(_tz.utc).isoformat(),
                channel_url,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_recent_video_titles(channel_url: str, limit: int = 12, timeout: float = 15.0) -> list[str]:
    """Recent video titles from the channel's RSS feed (free, no API quota)."""
    channel_id = channel_url.rstrip("/").rsplit("/", 1)[-1]
    if not channel_id.startswith("UC"):
        return []
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            tree = ElementTree.fromstring(response.read())
    except Exception:
        return []
    titles: list[str] = []
    for entry in tree.iter():
        if not (entry.tag == "entry" or entry.tag.endswith("}entry")):
            continue
        for child in entry:
            if child.tag == "title" or child.tag.endswith("}title"):
                text = (child.text or "").strip()[:120]
                if text:
                    titles.append(text)
                break
        if len(titles) >= limit:
            break
    return titles


def _classify_prompt(
    channel_title: str, channel_description: str, video_titles: list[str] | None = None
) -> str:
    titles_block = ""
    if video_titles:
        titles_block = (
            "Recent video titles:\n"
            + "\n".join(f"- {t}" for t in video_titles)
            + "\n\n"
        )
    return (
        "You are a channel classifier. Given a YouTube channel's title, description,"
        " and recent video titles,\n"
        "choose exactly ONE category from this list:\n"
        f"{_CATEGORY_LIST}\n\n"
        "Rules:\n"
        "- Pick the most specific category that fits\n"
        "- Politics when the FOCUS is politics (commentary, elections, policy, "
        "partisanship); News when the focus is general news reporting even if "
        "it covers politics\n"
        "- Markets when the FOCUS is making money from financial markets "
        "(stocks, futures, options, forex, crypto trading, algo/quant trading, "
        "market analysis); Business when the focus is running or building "
        "companies; Finance for personal finance and wealth management\n"
        "- AI News when the focus is COMMENTARY ABOUT the AI industry (daily "
        "AI news, model releases, industry drama, AI predictions) — same test "
        "as News but for AI; AI/ML when the focus is DOING AI (tutorials, "
        "tools, coding with AI, building AI systems, ML research)\n"
        "- True Crime when the focus is criminal cases, investigations, trials, "
        "or forensic analysis; Entertainment for comedy, reaction content, "
        "ASMR, compilations, and general spectacle\n"
        "- Storytelling for narrative fiction audio (HFY, sci-fi stories, "
        "audiobooks, creepypasta); Entertainment for non-narrative content\n"
        "- History for historical content (wars, civilizations, documentaries "
        "about the past); Education for teaching methods, how-to, and skill "
        "instruction\n"
        "- Software Engineering for coding, programming, software development, "
        "DevOps, and software architecture; Technology for hardware, gadgets, "
        "consumer tech reviews, and general tech commentary\n"
        "- The video titles are the strongest evidence of actual content\n"
        "- Return ONLY the category name, nothing else\n"
        "- If you cannot determine, return 'Other'\n\n"
        f"Channel title: {channel_title}\n"
        f"Channel description: {channel_description or '(none)'}\n\n"
        f"{titles_block}"
        "Return ONLY the category name."
    )


def _classify(
    channel_title: str,
    channel_description: str,
    timeout: float,
    video_titles: list[str] | None = None,
) -> str | None:
    """Round-robin across available HTTP providers, gemini SDK first if usable."""
    prompt = _classify_prompt(channel_title, channel_description, video_titles)

    for key in _gemini_keys():
        text = _gemini_classify(key, prompt, timeout)
        if text:
            return text

    global _rr_index
    providers = _available_providers()
    if not providers:
        return None
    with _rr_lock:
        start = _rr_index
    for offset in range(len(providers)):
        label, url, key, model, extra = providers[(start + offset) % len(providers)]
        with _rr_lock:
            _rr_index = (_rr_index + 1) % max(len(providers), 1)
        text = _http_classify(url, key, model, prompt, timeout, extra)
        if text:
            return text
    return None


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def categorize_batch(
    channels: list[dict[str, object]], timeout: float = 60.0
) -> dict[str, str | None]:
    """Classify a batch of channels in one LLM call.

    Each channel dict: {"url", "title", "description", "video_titles"}.
    Returns {url: category|None}. Batching N channels per call cuts LLM
    invocations by Nx — the dominant cost of bulk classification through
    free-tier providers.

    Output contract: one line per channel, "URL_SUFFIX|CATEGORY" — the
    short URL tail is unique per channel and unambiguous to parse.
    """
    if not channels:
        return {}
    lines = []
    for ch in channels:
        titles = ch.get("video_titles") or []
        titles_block = " | ".join(titles[:5]) if titles else ""
        lines.append(
            f"{str(ch.get('url', ''))[-12:]}|{str(ch.get('title', ''))[:60]}|"
            f"{str(ch.get('description', ''))[:200]}|{titles_block}"
        )
    prompt = (
        "You are a channel classifier. For each channel below, choose exactly ONE\n"
        f"category from this list:\n{_CATEGORY_LIST}\n\n"
        "Rules:\n"
        "- Pick the most specific category that fits\n"
        "- Politics when the FOCUS is politics; News for general news\n"
        "- Markets for trading/investing focus; Business for companies\n"
        "- Return ONLY lines of: URL_SUFFIX|CATEGORY — one per input channel,\n"
        "  same order, no extra text\n"
        "- If you cannot determine, use Other\n\n"
        "Channels (URL_SUFFIX|title|description|recent_video_titles):\n"
        + "\n".join(lines)
    )
    text = _classify("", "", timeout, None)  # reuse provider chain
    if not text:
        return {str(ch.get("url", "")): None for ch in channels}
    # Parse response lines
    result: dict[str, str | None] = {}
    for ch in channels:
        result[str(ch.get("url", ""))] = None  # default: unmatched
    for line in text.strip().split("\\n"):
        if "|" not in line:
            continue
        suffix, _, cat = line.rpartition("|")
        cat = cat.strip()
        if cat in CATEGORIES or cat == OTHER_CATEGORY:
            for ch in channels:
                if str(ch.get("url", ""))[-12:] == suffix.strip():
                    result[str(ch.get("url", ""))] = cat
                    break
    return result


def categorize_channel(
    channel_title: str,
    channel_description: str,
    timeout: float = 30.0,
    video_titles: list[str] | None = None,
) -> str | None:
    """Categorize a channel via the configured LLM provider chain.

    Args:
        channel_title: The channel's title.
        channel_description: The channel's description (can be empty).
        timeout: Per-request timeout in seconds.
        video_titles: Optional recent video titles (RSS) — the strongest
            content signal when title+description are thin.

    Returns:
        A category string from CATEGORIES, "Other" when the model cannot
        determine one (stored terminally), or None on failure.
    """
    if not channel_title:
        return None

    text = _classify(channel_title, channel_description, timeout, video_titles)
    if not text:
        return None

    # Validate against known categories (strip markdown fences / quotes).
    cleaned = re.sub(r"```(?:json)?\n?|```", "", text).strip()
    category = cleaned.split("\n")[0].strip().strip('"')
    if category in CATEGORIES:
        return category
    if category == OTHER_CATEGORY:
        return OTHER_CATEGORY
    # Fallback: fuzzy match
    for cat in CATEGORIES:
        if cat.lower() in category.lower():
            return cat
    return None
