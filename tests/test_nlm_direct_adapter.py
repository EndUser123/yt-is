from __future__ import annotations

import json

from csf import nlm_batch


class _Namespace:
    def __init__(self, values):
        self.values = values

    def list(self, *args):
        return ("list", args)

    def create(self, **kwargs):
        return ("create", kwargs)

    def delete(self, *args):
        return ("delete", args)

    def get_fulltext(self, *args, **kwargs):
        return ("content", args, kwargs)


class _FakeClient:
    def __init__(self):
        self.notebooks = _Namespace([])
        self.sources = _Namespace([])
        self.calls = []

    def run(self, operation):
        self.calls.append(operation)
        if operation[0] == "list" and len(operation[1]) == 0:
            return []
        if operation[0] == "list":
            return [{"id": "source-1", "title": "Video", "url": "https://youtube/watch?v=vid1"}]
        if operation[0] == "create":
            return {"id": "nb-1", "title": operation[1]["title"]}
        if operation[0] == "content":
            return type("Fulltext", (), {"content": "transcript text"})()
        return None

    def close(self):
        return None


def test_active_command_adapter_uses_typed_client(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setenv("YTIS_NLM_ACCOUNT_PROFILE", "a.hominidae")
    monkeypatch.setenv("YTIS_NLM_WORKER_ID", "worker-01")
    monkeypatch.setattr(
        "csf.nlm_client.get_sync_client",
        lambda **kwargs: fake,
    )
    ingestor = nlm_batch.NLMBatchIngestor()
    ingestor._nb_id = "nb-1"

    listing = ingestor._execute_direct_command(["source", "list", "nb-1", "--json"])
    content = ingestor._execute_direct_command(["source", "content", "source-1", "--json"])

    assert listing.returncode == 0
    assert json.loads(listing.stdout)["sources"][0]["id"] == "source-1"
    assert json.loads(content.stdout)["value"]["content"] == "transcript text"
    assert all(call[0] != "cli" for call in fake.calls)


def test_missing_account_identity_is_rejected_before_direct_client(monkeypatch):
    monkeypatch.delenv("YTIS_NLM_ACCOUNT_PROFILE", raising=False)
    ingestor = nlm_batch.NLMBatchIngestor()

    result = ingestor._execute_direct_command(["notebook", "list", "--json"])

    assert result.returncode == 1
    assert "account_profile" in result.stderr.lower()
