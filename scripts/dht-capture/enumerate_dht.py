"""Enumerate the logged-in user's Discord guilds/channels for DHT capture.

Uses the capture browser profile (logged in via `capture.py login`) to
grab the session token — NOT from localStorage (Discord's client
deletes it from the main world after boot) but by intercepting the
Authorization header of the client's own API traffic. All enumeration
then runs from Python with that bearer token. Read-only except the
optional --create-webhook mode.

Output: P:/.data/yt-is/dht-capture-catalog.json
    {captured_at, me: {id, name}, guilds: [{id, name, owner,
     webhook_eligible, approx_members, channels: [{id, name, type,
     type_name, capturable, thread_count, threads: [{id, name}]}]}]}

Usage:
    python enumerate_dht.py                     # full catalog refresh
    python enumerate_dht.py --create-webhook <guild_id> <channel_id>
        # ensure a "ytis-digest" webhook on that channel; saves URL to
        # P:/.env as DISCORD_DIGEST_WEBHOOK_URL (reuses existing)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
PROFILE_DIR = HERE / "profile"
CATALOG = Path("P:/.data/yt-is/dht-capture-catalog.json")

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
API = "https://discord.com/api/v10"
MANAGE_WEBHOOKS = 1 << 29
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36")

TYPE_NAMES = {0: "text", 2: "voice", 4: "category", 5: "announcement",
              13: "stage", 15: "forum", 16: "media"}
CAPTURABLE = {0, 5, 15, 16}   # DHT tracks text-like channels


def capture_token() -> str:
    """Open the logged-in profile; read the bearer token off the wire."""
    token = {"v": None}
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel="chrome", executable_path=CHROME,
            headless=False, viewport={"width": 1280, "height": 900})

        def on_request(req):
            if token["v"]:
                return
            auth = req.headers.get("authorization", "")
            if auth.startswith("MT") or (auth and "discord.com" in req.url
                                         and "/api" in req.url):
                token["v"] = auth

        ctx.on("request", on_request)
        page = ctx.new_page()
        page.goto("https://discord.com/channels/@me",
                  wait_until="domcontentloaded", timeout=60000)
        deadline = time.time() + 90
        while not token["v"] and time.time() < deadline:
            page.wait_for_timeout(2000)
        ctx.close()
    if not token["v"]:
        sys.exit("no Discord API traffic with an Authorization header — "
                 "profile is logged out; run `capture.py login` first")
    return token["v"]


def call(token: str, method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": token, "User-Agent": UA,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def build_catalog(token: str) -> dict:
    status, me = call(token, "GET", "/users/@me")
    if status != 200:
        sys.exit(f"session invalid ({status}) — re-run capture.py login")

    guilds, after = [], None
    while True:
        q = "/users/@me/guilds?with_counts=true&limit=200"
        q += f"&after={after}" if after else ""
        status, batch = call(token, "GET", q)
        if status != 200:
            sys.exit(f"guild list failed: {status} {batch}")
        batch = batch or []
        guilds.extend(batch)
        if len(batch) < 200:
            break
        after = batch[-1]["id"]

    out = {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "me": {"id": me["id"], "name": me.get("username", "")},
           "guilds": []}
    for g in sorted(guilds, key=lambda x: x["name"].lower()):
        perms = int(g.get("permissions", "0") or 0)
        entry = {"id": g["id"], "name": g["name"],
                 "owner": bool(g.get("owner")),
                 "webhook_eligible": bool(g.get("owner")) or
                 bool(perms & MANAGE_WEBHOOKS),
                 "approx_members": g.get("approximate_member_count"),
                 "channels": []}
        _, chans = call(token, "GET", f"/guilds/{g['id']}/channels")
        _, threads = call(token, "GET", f"/guilds/{g['id']}/threads/active")
        by_parent: dict[str, list] = {}
        for t in ((threads or {}).get("threads") or []):
            by_parent.setdefault(t.get("parent_id"), []).append(
                {"id": t["id"], "name": t.get("name", "")})
        for c in sorted(chans or [], key=lambda c: (c.get("position", 0),
                                                    c.get("name", ""))):
            ctype = c.get("type", 0)
            entry["channels"].append({
                "id": c["id"], "name": c.get("name", ""),
                "type": ctype,
                "type_name": TYPE_NAMES.get(ctype, str(ctype)),
                "capturable": ctype in CAPTURABLE,
                "thread_count": len(by_parent.get(c["id"], [])),
                "threads": by_parent.get(c["id"], [])[:25]})
        out["guilds"].append(entry)
        print(f"  {g['name']}: {len(entry['channels'])} channels", flush=True)
        time.sleep(0.6)

    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    total = sum(len(g["channels"]) for g in out["guilds"])
    print(f"catalog: {len(out['guilds'])} guilds, {total} channels "
          f"-> {CATALOG}")
    return out


def ensure_webhook(user_token: str, guild_id: str, channel_id: str) -> str:
    """Webhook creation needs the BOT token (user tokens get 405 on
    writes). The bot must be in the target guild — invite it once with
    Manage Webhooks via:
    https://discord.com/oauth2/authorize?client_id=1523794851727015967&scope=bot&permissions=536870912
    """
    env = Path("P:/.env").read_text(encoding="utf-8")
    bot = next((l.split("=", 1)[1].strip().strip('"')
                for l in env.splitlines()
                if l.startswith("DISCORD_BOT_TOKEN=")), "")
    if not bot:
        sys.exit("no DISCORD_BOT_TOKEN in P:/.env")
    status, gs = call(f"Bot {bot}", "GET", "/users/@me/guilds")
    if status != 200 or not any(g.get("id") == guild_id for g in gs or []):
        sys.exit("the bot has not been invited to that guild — open the "
                 "invite URL in this function's docstring, authorize in "
                 "the target server, then retry")
    status, hooks = call(f"Bot {bot}", "GET",
                         f"/channels/{channel_id}/webhooks")
    if status == 200:
        for w in hooks or []:
            if w.get("name") == "ytis-digest" and w.get("url"):
                return w["url"]
    status, w = call(f"Bot {bot}", "POST", f"/guilds/{guild_id}/webhooks",
                     {"name": "ytis-digest", "channel_id": channel_id})
    if status not in (200, 201) or not (w or {}).get("url"):
        sys.exit(f"webhook create failed: {status} {w}")
    return w["url"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--create-webhook", nargs=2, metavar=("GUILD", "CHANNEL"))
    a = ap.parse_args(argv)
    if a.create_webhook:
        guild, channel = a.create_webhook
        url = ensure_webhook("", guild, channel)
        env = Path("P:/.env")
        lines = [l for l in env.read_text(encoding="utf-8").splitlines()
                 if not l.startswith("DISCORD_DIGEST_WEBHOOK_URL=")]
        lines.append(f"DISCORD_DIGEST_WEBHOOK_URL={url}")
        env.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("webhook ready; DISCORD_DIGEST_WEBHOOK_URL written to P:/.env")
        return 0
    build_catalog(capture_token())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
