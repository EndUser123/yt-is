# DHT capture automation

Automated Discord message capture via Discord History Tracker (DHT).
The desktop app (`P:/tools/dht/DiscordHistoryTracker.exe`, external
binary) + a logged-in playwright Chrome profile visit the channels in
`channels.txt`, and the DHT tracking script saves scrolled history into
the app's open archive. `run_dht_ingest.py` (nightly chain, step 3)
pulls the archive into the knowledge base.

## One-time operator setup

1. **Log the capture browser into Discord** (~1 min, once):
   `python scripts/dht-capture/capture.py login`
   A Chrome window opens; log into Discord; it detects the login,
   saves it to `profile/`, and closes itself.
2. **Save the tracking script** (~1 min, once):
   Open the DHT app → Tracking tab → "Copy Tracking Script", then:
   `python scripts/dht-capture/setup_tracking.py` → press Enter.
   Saves `tracking-script.js` (gitignored — contains the app token).
3. **List channels**: put one Discord channel URL per line in
   `channels.txt` (right-click channel in Discord → Copy Link).

Then test manually: `scripts/dht-capture/nightly.cmd` (the same chain
the 03:00 YtisDhtCapture task runs). Verify `.logs/dht_capture.log`
and that `run_dht_ingest.py` picked up whatever the app wrote.

## Files

| File | What | Tracked? |
|---|---|---|
| `capture.py` | login/capture browser automation | yes |
| `setup_tracking.py` | one-time clipboard capture of the tracking script | yes |
| `nightly.cmd` | 03:00 chain: app → capture → ingest | yes |
| `channels.txt` | operator's channel list | yes |
| `tracking-script.js` | DHT script (token) | **no** |
| `profile/` | Chrome profile (Discord session) | **no** |
