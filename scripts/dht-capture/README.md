# DHT capture automation

Automated Discord message capture via Discord History Tracker (DHT).
The desktop app (`P:/tools/dht/DiscordHistoryTracker.exe`, external
binary) + a logged-in playwright Chrome profile visit the channels you
enable on the capture page, and the DHT tracking script saves scrolled
history into the app's open archive. `run_dht_ingest.py` (nightly
chain, step 3) pulls the archive into the knowledge base.

## Channel selection page

`http://127.0.0.1:6393/dht` — every server you belong to with all its
channels; click to enable/disable capture per channel or per server
(`http://127.0.0.1:6391/dht` after the next warm-service restart).
Selection lives in `P:/.data/yt-is/dht-capture-selection.json`;
`capture.py` reads it (legacy `channels.txt` is the fallback).
Refresh the server/channel catalog with
`python scripts/dht-capture/enumerate_dht.py` (uses the logged-in
capture profile; token grabbed off the wire — Discord deletes
localStorage from the page world).

## One-time operator setup (already DONE 2026-08-22)

1. `python scripts/dht-capture/capture.py login` — capture browser
   authenticated (profile remembers).
2. `python scripts/dht-capture/setup_tracking.py` — tracking script
   saved (gitignored; contains the app token).
3. Enable channels on the page above (or channels.txt).

## Digest webhook

The page's "[digest webhook]" button (webhook-eligible servers only)
creates a `ytis-digest` webhook via the BOT token and saves its URL to
`P:/.env` as `DISCORD_DIGEST_WEBHOOK_URL`. User tokens get 405 on
writes, so the bot must be invited once to the target server with
Manage Webhooks:
https://discord.com/oauth2/authorize?client_id=1523794851727015967&scope=bot&permissions=536870912

Test the chain: `scripts/dht-capture/nightly.cmd` (the same chain the
03:00 YtisDhtCapture task runs). Verify `.logs/dht_capture.log` and
that `run_dht_ingest.py` picked up whatever the app wrote.

## Files

| File | What | Tracked? |
|---|---|---|
| `capture.py` | login/capture browser automation | yes |
| `enumerate_dht.py` | server/channel catalog + webhook creator | yes |
| `dht_page_server.py` | standalone :6393 selection page (until :6391 restart) | yes |
| `setup_tracking.py` | one-time clipboard capture of the tracking script | yes |
| `nightly.cmd` | 03:00 chain: app → capture → ingest | yes |
| `channels.txt` | legacy channel list (fallback) | yes |
| `tracking-script.js` | DHT script (token) | **no** |
| `profile/` | Chrome profile (Discord session) | **no** |
