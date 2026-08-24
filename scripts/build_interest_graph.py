"""v2: LLM inference layer — evidence packets → typed interest/goal graph.

The operator's contract (personal-intelligence-system design):
  Given evidence clusters containing semantic topics, entities,
  representative documents, temporal statistics, source diversity:
  infer interests, goals, information needs, questions. Not topics.

Key principles:
  - Do NOT rename clusters. Infer what the user is trying to
    learn/accomplish/decide/monitor/prevent.
  - Merge clusters better explained by one latent goal.
  - Frequency alone is not an interest.
  - Cite supplied evidence for every inference.
  - Record counterevidence.
  - Label adjacent-inferred vs observed.

Usage:
    python scripts/build_interest_graph.py --dry-run    # show packets
    python scripts/build_interest_graph.py              # run inference
    python scripts/build_interest_graph.py --provider agy  # override
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from csf.paths import load_workspace_env  # noqa: E402

from ef.evidence_clusters import cached_clusters  # noqa: E402

MAX_CLUSTERS = 25          # packets sent per inference call
MAX_REPS_PER_CLUSTER = 4   # representative docs per packet

PROMPT_TEMPLATE = """You are analyzing a personal corpus of {n_clusters} evidence clusters from a multi-source intelligence system (YouTube, Discord, Reddit, RSS, HN, GitHub, podcasts). Your task is NOT to classify topics — it is to infer what this person is actually trying to understand, accomplish, decide, monitor, improve, build, prevent, or understand.

For each evidence cluster below, determine whether it supports a genuine user information interest. Do not merely rename the cluster. Infer, when supported:
- domain (broad area)
- topic (specific subject within the domain)
- underlying goal/problem (what outcome the person appears to care about)
- information need (what they are repeatedly trying to learn)

Distinguish:
- observed subject matter (what the documents are about)
- inferred durable interest (long-term pattern)
- current project/decision (active, recent, decision-shaped)
- curiosity/entertainment (broad consumption, no project signal)
- inferred adjacent interest (implied by goals but not directly observed)

Merge clusters when they are better explained by one latent goal (e.g., sleep + exercise + ApoB + Alzheimer's → "preserve cognition and extend healthy lifespan"). Keep them separate when shared vocabulary is superficial.

For every inference, cite the cluster_id. Record counterevidence where present.

Return ONLY valid JSON matching this schema:
{{
  "inferred_interests": [
    {{
      "name": "string — canonical name",
      "kind": "domain|topic|subtopic|method|monitor",
      "parent": "name of parent interest or null",
      "temporal_state": "durable|active|current_problem|episodic|emerging|dormant",
      "stance": "curiosity|learning|project|monitoring|entertainment",
      "confidence": 0.0-1.0,
      "observed_vs_inferred": "observed|inferred|inferred_adjacent",
      "goal": "string — the underlying goal/problem, or null",
      "cluster_ids": [int],
      "evidence_summary": "string — what evidence supports this",
      "counterevidence": "string — what argues against this, or null",
      "related_to": ["name of related interests"]
    }}
  ],
  "questions": [
    {{
      "text": "string — an open question the person appears to be investigating",
      "interest": "name of the parent interest",
      "status": "open"
    }}
  ],
  "regret_candidates": [
    {{
      "topic": "string — adjacent topic poorly represented but strongly implied",
      "why": "string — why this matters given demonstrated goals",
      "label": "inferred_adjacent"
    }}
  ]
}}

EVIDENCE CLUSTERS:

{packets}
"""


def build_packets(clusters: list[dict]) -> str:
    """Format evidence clusters into the LLM packet text."""
    parts = []
    for c in clusters[:MAX_CLUSTERS]:
        reps = "\n".join(
            f"    - \"{r['title']}\" ({r['month']}, {r['source']})"
            for r in c["representative"][:MAX_REPS_PER_CLUSTER])
        ents = ", ".join(e["entity"] for e in c["entities"][:8])
        parts.append(f"""Cluster {c['cluster_id']}: {c['label']}
  Terms: {', '.join(c['terms'][:8])}
  Entities: {ents or '(none passing specificity)'}
  Stats: {c['channels']} channels, {c['documents']} docs,
         {c['active_months']} active months ({c['first_month']} to {c['last_month']}),
         sources: {dict(c['sources'])}
  Phase: {c.get('phase') or 'steady'}
  Representative documents:
{reps}""")
    return "\n\n".join(parts)


def run_inference(provider: str = "codex") -> dict:
    """Send packets to the provider chain; parse and validate JSON."""
    import subprocess
    clusters, coverage = cached_clusters()
    packets = build_packets(clusters)
    prompt = PROMPT_TEMPLATE.format(
        n_clusters=min(len(clusters), MAX_CLUSTERS), packets=packets)

    env_extra = {"AGENT_GIT_CONTEXT_ACK": ""}
    prompt_file = Path("P:/tmp/interest-inference-prompt.txt")
    prompt_file.write_text(prompt, encoding="utf-8")
    result_file = Path("P:/tmp/interest-inference-result.json")

    print(f"[inference] {len(clusters)} clusters, "
          f"prompt {len(prompt):,} chars -> {provider}")
    if provider == "codex":
        import shutil
        codex = shutil.which("codex")
        if not codex:
            raise FileNotFoundError("codex not found on PATH")
        cmd = [codex, "exec", "--json", "--ephemeral", "-s", "read-only",
               "-m", "gpt-5.6-luna", "-c",
               "model_reasoning_effort=medium", "-C", "P:/",
               f"Read {prompt_file} and return ONLY the JSON. "
               "No prose, no markdown fences."]
    elif provider == "agy":
        cmd = ["agy", "-p", "--no-session", "--no-tools",
               "--model", "gemini/gemini-2.5-pro",
               prompt[:100000]]
    else:
        raise ValueError(f"unknown provider: {provider}")

    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=580, cwd="P:/")
    raw = r.stdout.strip()

    # extract JSON from possible wrapper text
    for opener, closer in (("{", "}"), ("```json", "```")):
        start = raw.find(opener)
        if start >= 0:
            end = raw.rfind(closer)
            if end > start:
                candidate = raw[start:end + len(closer)]
                if opener == "```json":
                    candidate = candidate[8:-4]
                try:
                    parsed = json.loads(candidate)
                    result_file.write_text(
                        json.dumps(parsed, indent=2, ensure_ascii=False),
                        encoding="utf-8")
                    print(f"[inference] parsed {len(parsed.get('inferred_interests', []))} "
                          f"interests -> {result_file}")
                    return parsed
                except json.JSONDecodeError:
                    continue
    raise ValueError(f"could not parse JSON from {provider} output: "
                     f"{raw[:200]}")


def store(parsed: dict) -> int:
    """Write inferred interests to the typed graph tables."""
    from ef.personal_graph import connect
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    stored = 0
    try:
        for i, interest in enumerate(parsed.get("inferred_interests", [])):
            iid = f"int_{now.replace('-','').replace(':','').replace('T','')}_{i}"
            conn.execute(
                """INSERT OR REPLACE INTO interests
                   (interest_id, name, kind, parent_id, temporal_state,
                    stance, confidence, observed_vs_inferred, goal_id,
                    evidence_json, exclusions_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (iid, interest["name"], interest.get("kind", "topic"),
                 None, interest.get("temporal_state", "durable"),
                 interest.get("stance", "curiosity"),
                 interest.get("confidence", 0.5),
                 interest.get("observed_vs_inferred", "inferred"),
                 None,
                 json.dumps({
                     "cluster_ids": interest.get("cluster_ids", []),
                     "evidence_summary":
                         interest.get("evidence_summary", ""),
                     "related_to": interest.get("related_to", [])},
                     ensure_ascii=False),
                 json.dumps({"counterevidence":
                             interest.get("counterevidence", "")}),
                 now))
            stored += 1
        for q in parsed.get("questions", []):
            conn.execute(
                "INSERT OR REPLACE INTO questions "
                "(question_id, text, status, interest_id, opened_at) "
                "VALUES (?, ?, ?, NULL, ?)",
                (f"q_{now}_{q['text'][:20]}", q["text"],
                 q.get("status", "open"), now))
        conn.commit()
    finally:
        conn.close()
    return stored


def main(argv=None) -> int:
    load_workspace_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--provider", default="codex")
    ap.add_argument("--store", action="store_true",
                    help="store results in the typed tables")
    a = ap.parse_args(argv)

    if a.dry_run:
        clusters, coverage = cached_clusters()
        packets = build_packets(clusters)
        print(packets[:3000])
        print(f"\n[dry-run] {len(clusters)} clusters, "
              f"{len(packets):,} chars of packets")
        return 0

    parsed = run_inference(a.provider)
    if a.store:
        n = store(parsed)
        print(f"[stored] {n} interests written to typed tables")
    else:
        print(json.dumps(parsed, indent=2, ensure_ascii=False)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
