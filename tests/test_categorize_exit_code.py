from __future__ import annotations

import sqlite3
from pathlib import Path
import sys

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

import pytest


def _load_csf_source():
    repo_root = Path(__file__).resolve().parents[1]
    loader = SourceFileLoader("csf_source_categorize_exit_test", str(repo_root / "bin" / "csf-source"))
    spec = spec_from_loader(loader.name, loader)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_csf_source()


@pytest.fixture()
def seeded_db(tmp_path):
    from csf.batch_status import _BatchStatusStorage

    db = tmp_path / "batch.sqlite"
    storage = _BatchStatusStorage(db_path=db)  # creates the full schema
    conn = storage._get_conn()
    conn.executemany(
        "INSERT INTO channel_metadata (channel_url, channel_id, last_checked,"
        " channel_title, description, category) VALUES (?, ?, ?, ?, ?, NULL)",
        [
            (f"https://www.youtube.com/channel/UC{i:020d}", f"UC{i:020d}", "2026-01-01", f"Channel {i}", "desc")
            for i in range(3)
        ],
    )
    conn.commit()
    conn.close()
    return db


def test_categorize_zero_success_exits_nonzero(seeded_db, monkeypatch, capsys):
    from csf.batch_status import _BatchStatusStorage

    real_storage = _BatchStatusStorage(db_path=seeded_db)
    import csf.batch_status as batch_status_mod
    monkeypatch.setattr(batch_status_mod, "_get_batch_status_storage", lambda: real_storage)
    import csf.categorize as categorize_mod
    monkeypatch.setattr(categorize_mod, "categorize_channel", lambda title, desc, video_titles=None: None)
    with pytest.raises(SystemExit) as caught:
        mod.cmd_categorize()
    assert caught.value.code == 1
    assert "no channel could be categorized" in capsys.readouterr().err


def test_categorize_partial_success_exits_clean(seeded_db, monkeypatch, capsys):
    from csf.batch_status import _BatchStatusStorage

    real_storage = _BatchStatusStorage(db_path=seeded_db)
    import csf.batch_status as batch_status_mod
    monkeypatch.setattr(batch_status_mod, "_get_batch_status_storage", lambda: real_storage)
    import csf.categorize as categorize_mod
    monkeypatch.setattr(categorize_mod, "categorize_channel", lambda title, desc, video_titles=None: "Technology")
    mod.cmd_categorize()  # must not raise
    assert "Categorized 3/3" in capsys.readouterr().out
