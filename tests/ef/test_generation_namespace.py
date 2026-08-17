"""A" section 4 tests: generation namespace isolation fails closed."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ef import catalog  # noqa: E402
from ef.contracts import EvidenceUnit, MEDIA_TRANSCRIPT  # noqa: E402


def _eu(n: str = "vid1") -> EvidenceUnit:
    return EvidenceUnit(
        eu_id=f"{n}:transcript", media_kind=MEDIA_TRANSCRIPT, video_id=n,
        channel_id="ch1", channel_title="Ch", title="T", lang="en",
        source="test", authority_ref=f"{n}:en:test", content_hash="x" * 64,
        captured_at="2026-08-17T00:00:00Z", published_at="2026-01-01",
        duration_s=10, char_length=500).validate()


def test_production_claim_is_exclusive(tmp_path):
    conn = catalog.connect(tmp_path / "a.sqlite")
    catalog.claim_production_generation(conn, 1, "generation/gen1-abc", "abc")
    with pytest.raises(catalog.NamespaceError):
        catalog.claim_production_generation(conn, 1, "generation/gen1-other", "xyz")
    # same claim is idempotent
    catalog.claim_production_generation(conn, 1, "generation/gen1-abc", "abc")


def test_store_requires_matching_production_claim(tmp_path):
    conn = catalog.connect(tmp_path / "b.sqlite")
    with pytest.raises(catalog.NamespaceError):
        catalog.store_eus(conn, [_eu()], generation=1,
                          build_id="generation/gen1-unclaimed")
    catalog.claim_production_generation(conn, 1, "generation/gen1-abc", "abc")
    assert catalog.store_eus(conn, [_eu()], generation=1,
                             build_id="generation/gen1-abc") == 1
    with pytest.raises(catalog.NamespaceError):
        catalog.store_eus(conn, [_eu("vid2")], generation=1,
                          build_id="generation/gen1-other")


def test_smoke_never_claims_production_generation(tmp_path):
    conn = catalog.connect(tmp_path / "c.sqlite")
    catalog.claim_smoke_build(conn, "smoke/run-1")
    with pytest.raises(catalog.NamespaceError):
        catalog.claim_smoke_build(conn, "generation/gen1-abc")
    assert catalog.store_eus(conn, [_eu()], generation=0,
                             build_id="smoke/run-1") == 1


def test_unregistered_smoke_rejected(tmp_path):
    conn = catalog.connect(tmp_path / "d.sqlite")
    with pytest.raises(catalog.NamespaceError):
        catalog.store_eus(conn, [_eu()], generation=0, build_id="smoke/ghost")


def test_missing_build_id_rejected(tmp_path):
    conn = catalog.connect(tmp_path / "e.sqlite")
    with pytest.raises(catalog.NamespaceError):
        catalog.store_eus(conn, [_eu()], generation=1)
