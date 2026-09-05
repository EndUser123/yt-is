#!/usr/bin/env python3
"""Classify YouTube video items into topic domains and build playlist plans.

Ported from the one-off P:/tmp/sess_74977c1f-youtube-topic-groups/classify.py (2026-09-04
run, 6,821 videos into 18 domains) so the domain sort is reusable by the
yt-write skill's sync flow.

Inputs (JSON lists of {id, title, channel?}):
  --wl <file> --history <file>   merge with per-item source tags WL/H
  --items <file>                 single merged list, source tag defaults "X"
Outputs:
  --assignments <file>  per-video records {id, title, channel, sources, domain}
  --plan <file>         [{title: "pl-<slug>", videoIds: [...]}] for yt-write
  --report <file>       human-readable markdown grouped report

Classification is keyword-based on title + channel, first match wins;
CHANNEL_OVERRIDES run before keywords. Edit DOMAINS to change the sort.
agent: zcode
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

# (domain, [keywords]) — ordered; first match wins. Verified sort of the
# 2026-09-04 WL+history corpus; keep the order stable when editing.
DOMAINS: list[tuple[str, list[str]]] = [
    (
        "Canada & US politics and trade",
        [
            "canada", "canadian", "canadians", "carney", "trump", "tariff",
            "ottawa", "cbc", "quebec", "alberta", "b.c.", "parliament",
            "conservative", "liberal party", "u.s.-canada", "rubio", "g20",
            "biden", "democratic", "republican", "islam", "muslim",
            "zionist", "immigration", "senate", "politics", "political",
        ],
    ),
    (
        "Dance, K-pop & TikTok compilations",
        [
            "dance", "dancing", "tiktok", "tik-tok", "kpop", "k-pop",
            "fancam", "compilation", "kbj", "korean", "bigbank",
            "golaniyule0", "ahegao", "热舞", "美女", "직캠", "afreeca",
            "아헤가오", "跳舞", "bj김", "미야오", "캐치", "여우림",
        ],
    ),
    (
        "Dating, relationships & culture",
        [
            "redpill", "narcissist", "gaslighting", "dating", "storytime",
            "relationship", "masculinity", "approaching", "husband",
            "wife", "marriage", "feminism", "culture war",
        ],
    ),
    (
        "ASMR & relaxation",
        ["asmr", "whisper"],
    ),
    (
        "ADHD, learning & thinking",
        [
            "adhd", "autistic", "autism", "neurodivergent",
            "executive dysfunction", "memory hack",
        ],
    ),
    (
        "Geopolitics, war & defence",
        [
            "ukraine", "russia", "russian", "nato", "war", "military",
            "missile", "drone", "israel", "gaza", "taiwan", "iran",
            "geopolit", "shipyard", "navy", "army", "nuclear", "putin",
            "xi ", "china", "chinese", "border", "warships", "fighter jets",
        ],
    ),
    (
        "Markets, options & investing",
        [
            "option", "trading", "trader", "stock", "market", "invest",
            "vix", "portfolio", "tsla", "dividend", "crypto", "bitcoin",
            "value bet", "premium", "volatility", "etf", "recession",
            "economy", "economic", "bonds", "gold", "earnings",
            "delta ", "expiration", "iron condor", "freeroll", "money",
            "bank",
        ],
    ),
    (
        "AI coding tools & agents",
        [
            "claude code", "claude cowor", "codex", "cursor", "copilot",
            "agent", "glm", "harness", "mcp", "langgraph", "pydantic ai",
            "openclaw", "aider", "windsurf", "cline", "gemini cli",
            "sub-agent", "rag ", "prompt engineering", "skill.md",
            "ai workflow", "ai coding", "coding model", "vibe coding",
            "cloude", "claude", "hermes desktop", "grok bot", "opus",
            "second brain", "obsidian", "notebooklm", "kortex", "icm",
        ],
    ),
    (
        "Local AI & hardware",
        [
            "llama.cpp", "quantiz", "ollama", "local llm", "local ai",
            "gpu", " cpu", "ram", "inference", "vram", "fine-tune",
            "finetune", "gguf", "distill", "1-bit", "4-bit", "compressed",
            "freetoken", "webgpu", "kernel", "nvidia", "data centre",
            "data center", "clusters",
        ],
    ),
    (
        "AI models & industry news",
        [
            "gpt", "chatgpt", "openai", "gemini", "grok", "qwen", "llama",
            "muse spark", "musespark", "deepseek", "kimi", "anthropic",
            "ai model", "benchmark", "arc-agi", "agi", "llm", "sora",
            "veo", "astra", "fable", "hy4", "doj", "ai is here",
            "most dangerous ai", "overhyped", "ai news", "artificial intelligence",
            "minimax", "comfyui", "nemotron", "diffusion", "gemma",
            "groq", "midjourney", "stable diffusion", "hugging face",
            "generative", "ai video", "ai image", "seedance", "memomind",
        ],
    ),
    (
        "Science & space",
        [
            "spacex", "starlink", "rocket", "nasa", "physicist",
            "scientist", "hossenfelder", "universe", "quantum",
            "telescope", "planet", "matter", "research", "study finds",
        ],
    ),
    (
        "Pets & animals",
        ["dog", "puppy", "german shepherd", "cat ", "kitten"],
    ),
    (
        "Software engineering & platforms",
        [
            "git ", "git worktree", "github", "repo", "python",
            "javascript", "typescript", "programming", "developer",
            "docker", "kubernetes", "linux", "home server", "self-host",
            "open source", "open-source", "api ", "seo", "diagram",
            "archify", "browser", "extension", "tool", "app ",
        ],
    ),
    (
        "AI business, career & productivity",
        [
            "ai engineer", "make money", "money with ai", "business",
            "freelance", "career", "learn ai", "polymath", "teacher",
            "productivity", "voice agent", "automation agency",
            "subscribers", "views", "content creation", "monetiz",
        ],
    ),
    (
        "Health, fitness & nutrition",
        [
            "health", "diet", "carnivore", "fat", "blood pressure",
            "nitric oxide", "sleep", "huberman", "nutrition", "biohac",
            "exercise", "fasting", "glucose", "supplement", "longevity",
            "testosterone", "gym", "workout", "muscle", "protein",
            "inflammation", "vaccine", "insulin", "metabolic",
            "fibromyalgia", "vitamin", "alzheimer", "dementia", "heart",
            "doctor", "anxiety", "depression", "gut ", "ozempic",
            "stretch", "pain",
        ],
    ),
    (
        "Entertainment, music & sci-fi",
        [
            "movie", "film", "anime", "music", "edm", "electro house",
            "trailer", "gaming", "game ", "robotech", "macross", "sci-fi",
            "star trek", "twice", "song", "album", "ep extra", "gamer",
            "hfy", "r&b", "tungevaag", "asmr story",
        ],
    ),
    (
        "Learning, memory & cognition",
        [
            "logical thinking", "logical", "learning", "learn ", "study",
            "focus ", "brain", "remember", "thinking", "philosophy",
            "ontology", "memory",
        ],
    ),
]

CHANNEL_OVERRIDES: list[tuple[str, str]] = [
    ("value-investing", "Markets, options & investing"),
    ("cbc", "Canada & US politics and trade"),
    ("predicting alpha", "Markets, options & investing"),
    ("market maker method", "Markets, options & investing"),
    ("hubermanlab", "Health, fitness & nutrition"),
]

# Explicit playlist titles for the domains, in the order the operator runs.
# New domains fall back to an auto-slug ("pl-" + kebab-case label).
TITLE_MAP: dict[str, str] = {
    "AI coding tools & agents": "pl-ai-coding-agents",
    "Health, fitness & nutrition": "pl-health-fitness",
    "Canada & US politics and trade": "pl-canada-us-politics",
    "Local AI & hardware": "pl-local-ai-hardware",
    "Geopolitics, war & defence": "pl-geopolitics",
    "AI business, career & productivity": "pl-ai-business",
    "AI models & industry news": "pl-ai-models-news",
    "Entertainment, music & sci-fi": "pl-entertainment-music",
    "Other": "pl-other",
    "Software engineering & platforms": "pl-software-engineering",
    "Markets, options & investing": "pl-markets-investing",
    "Learning, memory & cognition": "pl-learning-cognition",
    "Science & space": "pl-science-space",
    "ADHD, learning & thinking": "pl-adhd",
    "ASMR & relaxation": "pl-asmr",
    "Pets & animals": "pl-pets",
    "Dance, K-pop & TikTok compilations": "pl-dance-kpop-tiktok",
    "Dating, relationships & culture": "pl-dating",
}


def slugify(label: str) -> str:
    s = label.lower().replace("&", " and ")
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_/,":
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def playlist_title(domain: str, prefix: str = "pl-") -> str:
    if domain in TITLE_MAP:
        return TITLE_MAP[domain]
    return prefix + slugify(domain)


def classify(title: str, channel: str | None) -> str:
    ch = (channel or "").lower()
    for key, domain in CHANNEL_OVERRIDES:
        if key in ch:
            return domain
    text = f"{title} {channel or ''}".lower()
    for domain, keys in DOMAINS:
        for k in keys:
            if k in text:
                return domain
    return "Other"


def load_list(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wl", type=Path, help="raw watch-later JSON (id/title/channel)")
    ap.add_argument("--history", type=Path, help="raw history JSON")
    ap.add_argument("--items", type=Path, help="pre-merged items JSON")
    ap.add_argument("--assignments", type=Path, help="output: per-video domain assignments")
    ap.add_argument("--plan", type=Path, help="output: yt-write create-playlists plan")
    ap.add_argument("--report", type=Path, help="output: markdown grouped report")
    ap.add_argument("--prefix", default="pl-", help="playlist title prefix")
    args = ap.parse_args()

    if not any([args.wl, args.history, args.items]):
        ap.error("need --items, or --wl and/or --history")

    wl = load_list(args.wl)
    hist = load_list(args.history)
    items = load_list(args.items)

    merged: dict[str, dict] = {}
    for item, src in ([(i, "WL") for i in wl] + [(i, "H") for i in hist]
                      + [(i, "X") for i in items]):
        vid = item["id"]
        if vid in merged:
            if src not in merged[vid]["sources"]:
                merged[vid]["sources"].append(src)
        else:
            merged[vid] = {
                "id": vid,
                "title": item["title"],
                "channel": item.get("channel"),
                "sources": [src],
            }

    for v in merged.values():
        v["domain"] = classify(v["title"], v["channel"])

    if args.assignments:
        args.assignments.write_text(
            json.dumps(list(merged.values()), indent=1), encoding="utf-8")

    if args.plan:
        by_domain: dict[str, list[str]] = defaultdict(list)
        for v in merged.values():
            by_domain[v["domain"]].append(v["id"])
        plan = [{"domain": d, "title": playlist_title(d, args.prefix),
                 "videoIds": ids} for d, ids in by_domain.items()]
        args.plan.write_text(json.dumps(plan, indent=1), encoding="utf-8")

    if args.report:
        domains: dict[str, list[dict]] = defaultdict(list)
        for v in merged.values():
            domains[v["domain"]].append(v)
        ordered = sorted(domains.items(), key=lambda kv: -len(kv[1]))
        lines = ["# YouTube videos — topic domains", "",
                 f"- Unique videos: {len(merged)}", "",
                 "| # | Topic domain | Videos |", "|---|---|---|"]
        for n, (dom, its) in enumerate(ordered, 1):
            lines.append(f"| {n} | {dom} | {len(its)} |")
        lines.append("")
        for n, (dom, its) in enumerate(ordered, 1):
            lines.append(f"## {n}. {dom} ({len(its)} videos)")
            lines.append("")
            for v in sorted(its, key=lambda x: ((x["channel"] or "").lower(),
                                                x["title"].lower())):
                src = "+".join(v["sources"])
                ch = f" — {v['channel']}" if v.get("channel") else ""
                lines.append(
                    f"- [{v['title']}](https://www.youtube.com/watch?v={v['id']})"
                    f"{ch} `[{src}]`")
            lines.append("")
        args.report.write_text("\n".join(lines), encoding="utf-8")

    counts: dict[str, int] = defaultdict(int)
    for v in merged.values():
        counts[v["domain"]] += 1
    print(f"unique={len(merged)}")
    for dom, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}  {dom}")


if __name__ == "__main__":
    main()
