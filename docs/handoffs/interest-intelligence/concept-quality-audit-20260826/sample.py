"""Deterministic frozen sampling for the Concept/KG extraction quality audit.

Policy (preregistered before any semantic review):
- Universe A (entity concepts): kg_nodes kind='entity' (388 nodes), joined to
  entities/entity_corpus where possible. Strata: mentioned_in degree tertile
  (low/mid/high) x publisher-diversity tertile (distinct channels of incident
  eu nodes). Take n=60 via stride selection: sort by node_id, index i,
  pick i % stride == rank offset, evenly across strata in round-robin.
- Universe B (cluster concepts): topic_clusters is_series=0. Strata:
  member_count buckets (<100, 100-999, >=1000). n=30.
- Universe R (relationships): mentioned_in edges of sampled entity nodes,
  stratified by edge weight (2, 3-5, >5). n=60.
- Universe T (trend topics): trend_alerts distinct topics. n=15, stride.
Deterministic: only sha256 of node/cluster/edge identifiers and fixed bucket
edges; no random seeds. Manifest written before review; contents hashed.
"""
import sqlite3, json, hashlib, statistics
from collections import defaultdict
from pathlib import Path

DB = r'file:P:/.data/yt-is/ef/catalog.sqlite?mode=ro'
OUT = Path(r'P:/tmp/burst-audit-concept')
con = sqlite3.connect(DB, uri=True)
con.row_factory = sqlite3.Row

def tertile(vals):
    s = sorted(vals)
    def rank(v):
        if v <= s[len(s)//3]: return 'low'
        if v <= s[2*len(s)//3]: return 'mid'
        return 'high'
    return rank

# --- Universe A: entity nodes ---
ents = list(con.execute("select node_id, label, weight, meta_json from kg_nodes where kind='entity'"))
eu_nb = defaultdict(list)   # entity node -> eu neighbors via mentioned_in
chan_of = {}                # eu node -> channel
eu_title = {}
for r in con.execute(
        "select k.node_id, e.channel_id, e.channel_title, e.title, e.source, e.published_at "
        "from kg_nodes k join eu e on 'eu:'||e.eu_id = k.node_id where k.kind='eu'"):
    eu_title[r['node_id']] = r['title'] or ''
    chan_of[r['node_id']] = f"{r['source']}:{r['channel_title']}"
for r in con.execute("select src_id, dst_id, relation, weight from kg_edges where relation='mentioned_in'"):
    # direction: entity -> eu per inventory
    eu_nb[r['src_id']].append((r['dst_id'], r['weight']))
ent_meta = {}
for r in con.execute("select entity, label, cluster_id, mentions from entities"):
    ent_meta.setdefault(r['label'], []).append(r)
degrees = {e['node_id']: len(eu_nb.get(e['node_id'], [])) for e in ents}
pubs = {e['node_id']: len({chan_of.get(d, '') for d, _ in eu_nb.get(e['node_id'], [])}) for e in ents}
d_rank = tertile(list(degrees.values()))
p_rank = tertile(list(pubs.values()))
strata = defaultdict(list)
for e in sorted(ents, key=lambda x: x['node_id']):
    strata[(d_rank(degrees[e['node_id']]), p_rank(pubs[e['node_id']]))].append(e['node_id'])
SAMPLE_A_N = 60
cells = sorted(strata.items())
pickA = []
per_cell = max(1, SAMPLE_A_N // len(cells))
for (dk, pk), ids in cells:
    stride = max(1, len(ids) // per_cell)
    pickA.extend(ids[::stride][:per_cell])
pickA = sorted(set(pickA))[:SAMPLE_A_N]

# --- Universe B: cluster concepts ---
clusters = list(con.execute("select cluster_id, label, member_count, video_count, top_terms, is_series, created_at from topic_clusters where is_series=0 order by cluster_id"))
def mcbucket(m):
    return 'lt100' if m < 100 else ('100-999' if m < 1000 else 'ge1000')
cstrata = defaultdict(list)
for c in clusters:
    cstrata[mcbucket(c['member_count'])].append(c['cluster_id'])
SAMPLE_B_N = 30
ccells = sorted(cstrata.items())
pickB = []
per = max(1, SAMPLE_B_N // len(ccells))
for b, ids in ccells:
    stride = max(1, len(ids) // per)
    pickB.extend([int(i) for i in ids[::stride][:per]])
pickB = sorted(set(pickB))[:SAMPLE_B_N]

# --- Universe R: relationship sample ---
edges = []
for nid in pickA:
    for dst, w in eu_nb.get(nid, []):
        edges.append((nid, dst, w))
edges.sort()
def wbucket(w):
    return 'w2' if w == 2 else ('w3-5' if w <= 5 else 'wgt5')
estrata = defaultdict(list)
for e in edges:
    estrata[wbucket(e[2])].append(e)
SAMPLE_R_N = 60
rcells = sorted(estrata.items())
pickR = []
per = max(1, SAMPLE_R_N // len(rcells))
for b, es in rcells:
    stride = max(1, len(es) // per)
    pickR.extend(es[::stride][:per])
pickR = sorted(pickR)[:SAMPLE_R_N]

# --- Universe T: trend topics ---
topics = sorted({r['topic'] for r in con.execute('select distinct topic from trend_alerts')})
stride = max(1, len(topics) // 15)
pickT = topics[::stride][:15]

# --- blinded packets ---
label_by_node = {e['node_id']: e['label'] for e in ents}
node_by_label = {e['label']: e['node_id'] for e in ents}
packets = []
for i, nid in enumerate(pickA):
    ev = eu_nb.get(nid, [])
    chans = sorted({chan_of.get(d, '') for d, _ in ev})
    titles = []
    for d, w in sorted(ev, key=lambda x: -x[1])[:6]:
        t = eu_title.get(d, '')
        titles.append(t[:160] if t else '(no title)')
    packets.append({'id': f'A{i+1:03d}', 'kind': 'entity_concept', 'label': label_by_node[nid],
                    'type_hint': 'entity (LLM-extracted)', 'evidence_count': len(ev),
                    'publisher_channels': len(chans), 'evidence_titles': titles,
                    'channel_ids_sample': chans[:6]})
cluster_by_id = {c['cluster_id']: c for c in clusters}
for i, cid in enumerate(pickB):
    c = cluster_by_id[cid]
    packets.append({'id': f'B{i+1:03d}', 'kind': 'cluster_concept', 'label': c['label'],
                    'type_hint': 'topic_cluster (mechanical top-4 title terms)',
                    'evidence_count': c['member_count'], 'publisher_channels': c['video_count'],
                    'evidence_titles': [t.strip() for t in (c['top_terms'] or '').split(',') if t.strip()][:8],
                    'created_at': c['created_at']})
rel_packets = []
for i, (src, dst, w) in enumerate(pickR):
    rel_packets.append({'id': f'R{i+1:03d}', 'relation': 'mentioned_in',
                        'entity_label': label_by_node.get(src, '?'), 'evidence_label': (eu_title.get(dst) or '?')[:160],
                        'edge_weight': w, 'channel': chan_of.get(dst, '')})
trend_packets = [{'id': f'T{i+1:03d}', 'topic': t} for i, t in enumerate(pickT)]

policy_text = Path(__file__).read_text(encoding='utf-8')
policy_hash = hashlib.sha256(policy_text.encode()).hexdigest()[:16]

manifest = {
    'frozen_at': '2026-08-26T13:0x pre-review',
    'policy_path': 'P:/tmp/burst-audit-concept/sample.py',
    'policy_hash': policy_hash,
    'universe_counts': {'kg_entity_nodes': len(ents), 'topic_clusters_noseries': len(clusters),
                        'mentioned_in_edges': sum(len(v) for v in eu_nb.values()),
                        'trend_topics': len(topics)},
    'sample_sizes': {'A_entity': len(pickA), 'B_cluster': len(pickB), 'R_edges': len(pickR), 'T_trend': len(pickT)},
    'strata_A': {f'{k[0]}-deg/{k[1]}-pub': len(v) for k, v in strata.items()},
    'strata_B': {k: len(v) for k, v in cstrata.items()},
    'strata_R': {k: len(v) for k, v in estrata.items()},
    'hidden_fields': ['trend_alert membership', 'temporal-emergence labels', 'holdout status',
                      'burst v1/v2 posteriors', 'lifecycle state', 'recommendation outcomes'],
}
(OUT / 'sample-manifest.json').write_text(json.dumps(manifest, indent=1))
(OUT / 'packets-A-B.json').write_text(json.dumps(packets, indent=1))
(OUT / 'packets-R.json').write_text(json.dumps(rel_packets, indent=1))
(OUT / 'packets-T.json').write_text(json.dumps(trend_packets, indent=1))
print(json.dumps(manifest['universe_counts'] | manifest['sample_sizes'], indent=1))
