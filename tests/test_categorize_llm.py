from __future__ import annotations

import json

import pytest

import csf.categorize as mod


@pytest.fixture(autouse=True)
def _clean_env_and_cache(monkeypatch):
    monkeypatch.setattr(mod, "_HTTP_RETRY_DELAYS", (0.0, 0.0))
    mod._reset_provider_cache()
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2"):
        monkeypatch.delenv(name, raising=False)
    yield
    mod._reset_provider_cache()


class _Resp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _mock_http(monkeypatch, responses: list[str | Exception]):
    calls: list[str] = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        outcome = responses.pop(0) if responses else "Education"
        if isinstance(outcome, Exception):
            raise outcome
        body = {"choices": [{"message": {"content": outcome}}]}
        return _Resp(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    return calls


def test_exact_category_returned(monkeypatch):
    calls = _mock_http(monkeypatch, ["Technology"])
    assert mod.categorize_channel("Some Tech Channel", "gadget reviews") == "Technology"
    assert calls  # hit the HTTP chain


def test_round_robin_spreads_calls_across_providers(monkeypatch):
    calls = _mock_http(monkeypatch, ["Technology", "News"])
    assert mod.categorize_channel("Tech Channel", "") == "Technology"
    assert mod.categorize_channel("News Channel", "") == "News"
    assert len(calls) == 2 and calls[0] != calls[1]


def test_fuzzy_match_falls_back_to_known_category(monkeypatch):
    _mock_http(monkeypatch, ["The category is AI/ML for sure"])
    assert mod.categorize_channel("ML Talk", "machine learning") == "AI/ML"


def test_invalid_answer_returns_none(monkeypatch):
    _mock_http(monkeypatch, ["banana"] * 9)
    assert mod.categorize_channel("Some Channel", "") is None


def test_provider_falls_through_on_http_error(monkeypatch):
    import urllib.error

    calls = _mock_http(
        monkeypatch,
        [urllib.error.HTTPError("url", 429, "rate limited", None, None)] * 3 + ["News"],
    )
    assert mod.categorize_channel("Daily Wire", "") == "News"
    # first provider throttled through its retries, second answered
    assert len(calls) == 4


def test_no_providers_configured_returns_none(monkeypatch):
    for _, _, env_names, _, _ in mod._HTTP_PROVIDERS:
        for env_name in env_names:
            monkeypatch.delenv(env_name, raising=False)
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2"):
        monkeypatch.delenv(name, raising=False)
    assert mod.categorize_channel("Some Channel", "") is None


def test_empty_title_returns_none_without_calls(monkeypatch):
    def fail(req, timeout=None):
        raise AssertionError("should not make a network call")

    monkeypatch.setattr(mod.urllib.request, "urlopen", fail)
    assert mod.categorize_channel("", "") is None


def test_other_answer_is_returned_as_terminal(monkeypatch):
    _mock_http(monkeypatch, ["Other"])
    assert mod.categorize_channel("Dylan Davis", "personal channel") == "Other"


def test_junk_answer_still_returns_none(monkeypatch):
    _mock_http(monkeypatch, ["banana"] * 9)
    assert mod.categorize_channel("Some Channel", "") is None


def test_fetch_recent_video_titles_parses_rss(monkeypatch):
    xml = (
        b'<feed xmlns="http://www.w3.org/2005/Atom">'
        b"<entry><title>Build an AI agent in n8n</title></entry>"
        b"<entry><title>Claude vs GPT workflow</title></entry>"
        b"<entry><title>Weekend project</title></entry>"
        b"</feed>"
    )

    class _Resp:
        def read(self):
            return xml

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        mod.urllib.request, "urlopen", lambda req, timeout=None: _Resp()
    )
    titles = mod.fetch_recent_video_titles(
        "https://www.youtube.com/channel/UCabc123", limit=2
    )
    assert titles == ["Build an AI agent in n8n", "Claude vs GPT workflow"]


def test_fetch_recent_video_titles_handles_failure(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    assert mod.fetch_recent_video_titles("https://www.youtube.com/channel/UCabc") == []


def test_prompt_includes_video_titles():
    prompt = mod._classify_prompt(
        "Thin Channel", "", video_titles=["AI agents tutorial", "LLM basics"]
    )
    assert "Recent video titles:" in prompt
    assert "- AI agents tutorial" in prompt
    assert "strongest evidence" in prompt
