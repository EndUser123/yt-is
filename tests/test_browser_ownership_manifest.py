from __future__ import annotations

from pathlib import Path

from csf.browser_ownership_manifest import (
    BrowserOwnershipManifest,
    build_browser_ownership_manifest,
    load_browser_ownership_manifest,
    write_browser_ownership_manifest,
)


def test_browser_ownership_manifest_round_trip(tmp_path):
    manifest_path = tmp_path / "browser_ownership.json"
    manifest = build_browser_ownership_manifest(
        run_root=tmp_path / "run",
        run_environment_label="hotel_wifi",
        default_browser_profile_root=Path(r"C:\Users\brsth\.notebooklm-mcp-cli\chrome-profile"),
        owned_browser_roots=[
            {
                "lane": "pro",
                "browser_profile_root": r"P:\\\\.data\yt-is\browser\notebooklm-pro",
                "browser_profile_directory": "",
                "browser_profile_namespace": r"P:\\\\.data\yt-is\browser\notebooklm-pro",
            },
            {
                "lane": "free",
                "browser_profile_root": r"P:\\\\.data\yt-is\browser\notebooklm-free",
                "browser_profile_directory": "Profile 1",
                "browser_profile_namespace": r"P:\\\\.data\yt-is\browser\notebooklm-free\Profile 1",
            },
        ],
    )

    written_path = write_browser_ownership_manifest(manifest_path, manifest)
    loaded = load_browser_ownership_manifest(written_path)

    assert written_path == manifest_path
    assert isinstance(loaded, BrowserOwnershipManifest)
    assert loaded.manifest_version == 1
    assert loaded.run_root == str(tmp_path / "run")
    assert loaded.run_environment_label == "hotel_wifi"
    assert loaded.default_browser_profile_root == r"C:\Users\brsth\.notebooklm-mcp-cli\chrome-profile"
    assert loaded.allowed_browser_roots == (
        r"P:\\\\.data\yt-is\browser\notebooklm-free",
        r"P:\\\\.data\yt-is\browser\notebooklm-pro",
    )
    assert [record.lane for record in loaded.owned_browser_roots] == ["pro", "free"]
