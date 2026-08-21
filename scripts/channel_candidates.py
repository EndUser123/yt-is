#!/usr/bin/env python
"""channel_candidates — corpus-breadth candidate list (D4 corpus lever).

Computes domain coverage of the indexed corpus (keyword classification over
channel titles), merges it with the hand-curated candidate seed below, and
writes P:/.data/yt-is/ef/channel-candidates.json. The warm-service home page
renders that file so the operator always sees what to add next.

Candidate lifecycle: candidate (unvalidated, research-sourced) → approved
(operator edit of the JSON: "status": "approved") → subscribed (the channel
sync picks it up; move to "status": "subscribed" when added).

Rerun anytime: python scripts/channel_candidates.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

OUT = Path("P:/.data/yt-is/ef/channel-candidates.json")

DOMAINS = {
    "health_medicine": r"health|dr\.|md\b|medical|nutri|fitness|diet|longev|wellness|clinic|disease|supplement",
    "ai_ml_agents": r"\bai\b|artificial|llm|gpt|claude|machine learning|neural|agent",
    "software_eng": r"code|coding|dev\b|software|programming|typescript|python|csharp|dotnet|javascript|react|engineer",
    "infra_devops_sre": r"devops|sre\b|cloud|kubernetes|aws|azure|infrastruct|site reliability|incident|docker|platform",
    "security": r"security|hacker|cyber|exploit|malware|ctf",
    "trading_finance": r"trad|invest|stock|market|financ|crypto|bitcoin|options",
}

# Candidate seed — researched 2026-08-21 via web search (sources cited).
# ALL entries are UNVALIDATED until the operator approves; presence here is
# a research lead, not a recommendation of quality over time.
SEED = {
    "infra_devops_sre": [
        {"name": "TechWorld with Nana", "why": "top-ranked DevOps/K8s/CI-CD tutorials", "source": "https://medium.com/@sre-devops-interview/best-youtube-channels-to-learn-devops-2025-edition-606cee75e3fc"},
        {"name": "Abhishek.Veeramalla", "why": "DevOps zero-to-hero + SRE roadmap content", "source": "https://www.youtube.com/abhishekveeramalla"},
        {"name": "DevOps Directive", "why": "cloud-native tooling and infrastructure", "source": "https://videos.feedspot.com/devops_youtube_channels/"},
        {"name": "DevOps Shack", "why": "2026-ranked DevOps channel", "source": "https://videos.feedspot.com/devops_youtube_channels/"},
        {"name": "Bret Fisher", "why": "Docker/K8s practice, incident-hardening patterns", "source": "https://videos.feedspot.com/devops_youtube_channels/"},
        {"name": "CNCF (Cloud Native Computing Foundation)", "why": "cloud-native talks; reliability engineering deep-dives", "source": "cncf.io / YouTube"},
        {"name": "Google Cloud Tech", "why": "SRE fundamentals sessions (official)", "source": "https://www.youtube.com/watch?v=OnX45XBbc4I"},
    ],
    "security": [
        {"name": "LiveOverflow", "why": "binary exploitation, RE, security deep-dives", "source": "community-consensus"},
        {"name": "The Cyber Mentor", "why": "web-app hacking, practical offensive security", "source": "https://www.linkedin.com/posts/danielmakelley_introducing-44-cybersecurity-youtube-channels-activity-7325199011974455298-nItD"},
        {"name": "John Hammond", "why": "CTF walkthroughs, malware analysis", "source": "https://www.reddit.com/r/cybersecurity/comments/1bdrma8/cyber_security_youtubers/"},
        {"name": "HackerSploit", "why": "structured offensive-security tutorials", "source": "https://learnwithpath.com/blog/best-youtube-channels-for-cybersecurity-2026"},
        {"name": "Black Hills Information Security", "why": "practical security engineering webcasts", "source": "https://www.linkedin.com/posts/danielmakelley_introducing-44-cybersecurity-youtube-channels-activity-7325199011974455298-nItD"},
        {"name": "Computerphile", "why": "CS fundamentals incl. security concepts", "source": "community-consensus"},
    ],
    "software_eng": [
        {"name": "ThePrimeagen", "why": "backend/systems engineering practice; failure-mode talk", "source": "https://marcgg.com/blog/2025/02/12/dev-youtube/"},
        {"name": "Dave Farley (Continuous Delivery)", "why": "software delivery architecture and practice", "source": "https://marcgg.com/blog/2025/02/12/dev-youtube/"},
        {"name": "ByteByteGo", "why": "system design — large-scale architecture reasoning", "source": "https://learnwithpath.com/blog/best-youtube-channels-for-system-design-2026"},
        {"name": "Gaurav Sen", "why": "system design and backend architecture", "source": "https://learnwithpath.com/blog/best-youtube-channels-for-system-design-2026"},
        {"name": "Fireship", "why": "high-signal tech explainers (breadth)", "source": "https://medium.com/swlh/top-10-youtube-channels-developers-should-follow-2025-0719e4dadb34"},
    ],
}


def coverage() -> dict:
    from ef.catalog import CATALOG_DB

    con = sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select channel_title, count(distinct video_id) from eu group by 1"
        ).fetchall()
    finally:
        con.close()
    dom = defaultdict(lambda: {"videos": 0, "channels": 0})
    for ch, n in rows:
        c = (ch or "").lower()
        for d, pat in DOMAINS.items():
            if re.search(pat, c):
                dom[d]["videos"] += n
                dom[d]["channels"] += 1
                break
        else:
            dom["other"]["videos"] += n
            dom["other"]["channels"] += 1
    return {k: v for k, v in sorted(dom.items(), key=lambda x: -x[1]["videos"])}


def main() -> int:
    cov = coverage()
    prev = {}
    if OUT.exists():
        try:
            prev = {c["name"]: c for c in json.loads(OUT.read_text(encoding="utf-8")).get("candidates", [])}
        except Exception:
            prev = {}
    candidates = []
    for domain, seed in SEED.items():
        for s in seed:
            # preserve operator decisions across regenerations
            old = prev.get(s["name"], {})
            candidates.append({
                "name": s["name"],
                "domain": domain,
                "why": s["why"],
                "source": s["source"],
                "status": old.get("status", "candidate"),
                "note": old.get("note", ""),
            })
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "coverage": cov,
        "method": (
            "coverage = keyword classification of indexed channel titles; "
            "candidates = web-researched 2026-08-21, UNVALIDATED until the "
            "operator sets status=approved; gaps sized by the wired EF "
            "consumers' query domains (infra/security near-zero)"
        ),
        "candidates": candidates,
    }
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT} — {len(candidates)} candidates")
    for d, v in cov.items():
        print(f"  {d:18s} {v['videos']:7,d} videos  {v['channels']:5d} channels")
    return 0


if __name__ == "__main__":
    sys.exit(main())
