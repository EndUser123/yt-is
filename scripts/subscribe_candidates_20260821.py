"""Subscribe the 18 approved candidates (operator directive 2026-08-21).

For each candidate: csf-source add with a best-guess @handle — add resolves
via API; wrong guesses FAIL LOUDLY and are skipped+logged, never silently
subscribed. Succeeded adds get status=subscribed in channel-candidates.json.
Then runs check on the new sources so the next fetch catches their backlogs.
Log: P:/.data/yt-is/subscribe-18-20260821.log
"""
import json, subprocess, sys, time
from pathlib import Path

REPO = Path("P:/packages/yt-is")
LOG = Path("P:/.data/yt-is/subscribe-18-20260821.log")
CJ = Path("P:/.data/yt-is/ef/channel-candidates.json")

GUESSES = {
    "TechWorld with Nana": "TechWorldwithNana",
    "Abhishek.Veeramalla": "AbhishekVeeramalla",
    "DevOps Directive": "DevOpsDirective",
    "DevOps Shack": "DevOpsShack",
    "Bret Fisher": "BretFisher",
    "CNCF (Cloud Native Computing Foundation)": "cncf",
    "Google Cloud Tech": "GoogleCloudTech",
    "LiveOverflow": "LiveOverflow",
    "The Cyber Mentor": "TCMSecurityAcademy",
    "John Hammond": "JohnHammond010",
    "HackerSploit": "HackerSploit",
    "Black Hills Information Security": "BlackHillsInformationSecurity",
    "Computerphile": "Computerphile",
    "ThePrimeagen": "ThePrimeagen",
    "Dave Farley (Continuous Delivery)": "DaveFarley10",
    "ByteByteGo": "ByteByteGo",
    "Gaurav Sen": "gkcs",
    "Fireship": "Fireship",
}

def log(msg):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

data = json.loads(CJ.read_text(encoding="utf-8"))
ok, failed = [], []
for c in data["candidates"]:
    name = c["name"]
    handle = GUESSES.get(name)
    if not handle:
        log(f"SKIP (no handle guess): {name}"); continue
    url = f"https://www.youtube.com/@{handle}"
    log(f"ADD try: {name} -> {url}")
    r = subprocess.run([sys.executable, str(REPO / "bin" / "csf-source"), "--allow-spend", "--allow-spend", "add", url],
                       cwd=str(REPO), capture_output=True, text=True, timeout=1800)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0 and "error" not in out.lower()[:400]:
        ok.append(name); c["status"] = "subscribed"
        c["note"] = f"subscribed via @{handle} 2026-08-21"
        log(f"  OK: {name}")
    else:
        failed.append(name)
        log(f"  FAIL ({r.returncode}): {out[-1500:]}")
    time.sleep(5)  # pacing between API-heavy adds

CJ.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
log(f"SUMMARY: subscribed={len(ok)} failed={len(failed)}")
log(f"failed list: {failed}")
# sync pass over the new sources so pending videos exist for the next fetch
if ok:
    r = subprocess.run([sys.executable, str(REPO / "bin" / "csf-source"), "check-all"],
                       cwd=str(REPO), capture_output=True, text=True, timeout=7200)
    log(f"check-all exit {r.returncode}: {(r.stdout or '')[-500:]}")
log("DONE")
