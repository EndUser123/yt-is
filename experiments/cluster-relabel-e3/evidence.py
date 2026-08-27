"""E3 shared evidence layer: deterministic per-cluster inputs for all arms.

Everything derives from the frozen snapshot + pre-existing Qdrant vectors.
No reclustering; membership untouched; read-only everywhere except the
vector cache database under the experiment's private .data dir.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
import threading
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

import e3lib as L

VEC_DB = L.EF_DATA / "vectors-cache.sqlite"
QDRANT_URL = "http://127.0.0.1:6390"
COLLECTION = "evidence_chunks__gen1"
POOL_SIZE = 300          # max member docs in the vector/representation pool
DISPLAY_N = 24           # titles shown to Arm C and to reviewers
REP_DOCS = 8             # KeyBERTInspired-style docs for term scoring
CAND_TOP = 40            # c-TF-IDF candidate terms per cluster

_TOKEN_RE = re.compile(r"[a-z][a-z\-']+")


# --------------------------------------------------------------------------
# text corpora
# --------------------------------------------------------------------------

def chunk_weighted_titles(c: L.FrozenCluster, drop: set[str] | None = None):
    """Titles repeated at CHUNK multiplicity — reproduces the production
    extract_top_terms input exactly (it iterates per-chunk payloads)."""
    per_video = defaultdict(list)
    for vid, pid in sorted(c.point_ids, key=lambda p: p[1]):
        per_video[vid].append(pid)
    out = []
    for vid in sorted(per_video):
        if drop and vid in drop:
            continue
        v = next(x for x in c.videos if x.video_id == vid)
        for _ in per_video[vid]:
            out.append(v.title.lower())
    return out


def arm_a_terms(weighted_titles: list[str], top_n: int = 10) -> list[str]:
    """Verbatim port of ef/clustering.py extract_top_terms."""
    word_counts: Counter = Counter()
    for title in weighted_titles:
        for word in title.split():
            w = word.strip(".,!?()[]{}:;\"'|-").lower()
            if len(w) > 2 and w not in L.CLUSTERING_STOPWORDS:
                word_counts[w] += 1
    return [w for w, _ in word_counts.most_common(top_n)]


def arm_a_label(top_terms: list[str]) -> str:
    """Verbatim port of ef/clustering.py generate_cluster_label."""
    if not top_terms:
        return "Unknown Topic"
    return " ".join(top_terms[:4]).title()


def ctfidf_candidates(
    corpora: dict[int, list[str]], top_n: int = CAND_TOP
) -> dict[int, list[str]]:
    """BERTopic-style class-based TF-IDF: one pseudo-document per cluster,
    sklearn TF-IDF (idf over classes ≈ c-TF-IDF scaling). Adapted donor,
    disclosed in RECEIPT.md. Returns top terms per cluster id."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    class_docs = [" ".join(corpora[cid]) or "(empty)" for cid in sorted(corpora)]
    vec = TfidfVectorizer(
        stop_words="english", token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z\-']+\b",
        min_df=2, sublinear_tf=True)
    X = vec.fit_transform(class_docs)
    vocab = np.array(vec.get_feature_names_out())
    out = {}
    for i, cid in enumerate(sorted(corpora)):
        row = X[i].toarray().ravel()
        order = np.argsort(-row)
        terms = []
        for j in order:
            if row[j] <= 0:
                break
            terms.append(str(vocab[j]))
            if len(terms) >= top_n:
                break
        out[cid] = terms
    return out


# --------------------------------------------------------------------------
# deterministic document pool + display selection
# --------------------------------------------------------------------------

def perturbation_drop(c: L.FrozenCluster, frac: float = 0.20) -> set[str]:
    """Preregistered stability perturbation: PRNG sha256('pert|CID')."""
    rng = random.Random(int(hashlib.sha256(f"pert|{c.cluster_id}".encode()).hexdigest()[:16], 16))
    vids = sorted({v.video_id for v in c.videos})
    k = int(len(vids) * frac)
    return set(rng.sample(vids, k)) if k else set()


def doc_pool(c: L.FrozenCluster, drop: set[str]) -> list[str]:
    """Up to POOL_SIZE member docs, chosen by preregistration: distinct
    videos ordered by ascending hex(min point_id), stride-sampled."""
    min_pid: dict[str, int] = {}
    for vid, pid in c.point_ids:
        if vid in drop:
            continue
        cur = min_pid.get(vid)
        if cur is None or pid < cur:
            min_pid[vid] = pid
    ordered = sorted(min_pid, key=lambda v: format(min_pid[v], "x"))
    if len(ordered) <= POOL_SIZE:
        return ordered
    step = len(ordered) / POOL_SIZE
    return [ordered[int(i * step)] for i in range(POOL_SIZE)]


def titles_by_id(c: L.FrozenCluster) -> dict[str, str]:
    return {v.video_id: v.title for v in c.videos}


class VectorStore:
    """Qdrant dense-vector retrieval keyed by frozen point_id, disk-cached.
    Handles are per-thread (sqlite3/qdrant clients are thread-bound); the
    cache table is shared safely via INSERT OR REPLACE."""

    def __init__(self):
        self._local = threading.local()

    def _db(self):
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(str(VEC_DB), timeout=60)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS vec (
                point_id TEXT PRIMARY KEY, v BLOB NOT NULL)""")
            db.commit()
            self._local.db = db
        return db

    def client(self):
        c = getattr(self._local, "client", None)
        if c is None:
            from qdrant_client import QdrantClient
            c = QdrantClient(url=QDRANT_URL, timeout=60)
            self._local.client = c
        return c

    def get(self, point_ids: list[int]) -> tuple[np.ndarray, list[int]]:
        """Returns (matrix rows aligned to found ids, found ids)."""
        want = list(dict.fromkeys(point_ids))
        found: dict[int, np.ndarray] = {}
        remaining = want
        if want:
            chunk = [want[i:i + 900] for i in range(0, len(want), 900)]
            for batch in chunk:
                marks = ",".join("?" * len(batch))
                rows = self._db().execute(
                    f"SELECT point_id, v FROM vec WHERE point_id IN ({marks})",
                    [str(p) for p in batch]).fetchall()
                for pid, blob in rows:
                    found[int(pid)] = np.frombuffer(blob, dtype=np.float32)
            missing = [p for p in batch if p not in found]
            if missing:
                res = self.client().retrieve(
                    collection_name=COLLECTION, ids=missing,
                    with_payload=False, with_vectors=True)
                ins = []
                for rec in res:
                    vec = (rec.vector or {}).get("dense")
                    if vec is None:
                        continue
                    arr = np.asarray(vec, dtype=np.float32)
                    arr /= (np.linalg.norm(arr) + 1e-10)
                    found[rec.id] = arr
                    ins.append((str(rec.id), arr.tobytes()))
                if ins:
                    self._db().executemany(
                        "INSERT OR REPLACE INTO vec VALUES (?,?)", ins)
                    self._db().commit()
        ids = [p for p in want if p in found]
        mat = np.stack([found[p] for p in ids]) if ids else np.zeros((0, 1024), np.float32)
        return mat, ids


def doc_vectors(store: VectorStore, c: L.FrozenCluster, pool: list[str]):
    """Mean dense vector per pooled doc from its in-cluster chunk vectors.
    Prereg-v6 fallback (pre-aggregation): docs whose point_ids vanished
    from the live Qdrant index get bge-m3(title) instead — same embedding
    space as the candidate terms; membership itself untouched."""
    per_video = defaultdict(list)
    for vid, pid in c.point_ids:
        if vid in pool:
            per_video[vid].append(pid)
    flat = sorted({pid for ids in per_video.values() for pid in ids})
    mat, found = store.get(flat)
    have = set(found)
    pos_of = {p: i for i, p in enumerate(found)}
    titles = titles_by_id(c)
    out_ids, rows = [], []
    missing_keys, missing_texts = [], []
    for vid in pool:
        idxs = [pos_of[p] for p in dict.fromkeys(per_video.get(vid, []))
                if p in have]
        m = mat[idxs].mean(axis=0) if idxs else None
        if m is None or np.linalg.norm(m) == 0:
            missing_keys.append(vid)
            missing_texts.append(titles.get(vid) or "(untitled)")
            continue
        n = np.linalg.norm(m)
        out_ids.append(vid)
        rows.append((m / n).astype(np.float32))
    if missing_texts:
        dense = get_embed_server().encode_dense(missing_texts, max_length=128)
        for vid, vec in zip(missing_keys, dense):
            n = np.linalg.norm(vec)
            out_ids.append(vid)
            rows.append((vec / n).astype(np.float32) if n > 0
                        else vec.astype(np.float32))
    # restore deterministic pool order
    pairs = sorted(zip(out_ids, rows), key=lambda vr: pool.index(vr[0]))
    out_ids = [v for v, _ in pairs]
    rows = [r for _, r in pairs]
    if not rows:
        return [], np.zeros((0, 1024), np.float32)
    return out_ids, np.stack(rows)


# --------------------------------------------------------------------------
# shared representation inputs
# --------------------------------------------------------------------------

def pool_evidence(c: L.FrozenCluster, drop: set[str], store: VectorStore):
    """Deterministic bundle shared by B/C/reviewers: doc pool, centroid,
    centroid-proximity ordering, DISPLAY_N decile titles."""
    pool = doc_pool(c, drop)
    vids, vecs = doc_vectors(store, c, pool)
    titles = titles_by_id(c)
    if len(vecs) == 0:
        return {"pool": [], "display": [], "rep_order": [], "cent": None}
    cent = vecs.mean(axis=0)
    n = np.linalg.norm(cent)
    cent = cent / n if n > 0 else cent
    sims = vecs @ cent
    proximal_rank = np.argsort(-sims)         # most central first
    ordered_vids = [vids[i] for i in proximal_rank]
    pick_positions = np.linspace(0, max(len(ordered_vids) - 1, 0),
                                 num=min(DISPLAY_N, len(ordered_vids)))
    picks = [ordered_vids[int(round(p))] for p in pick_positions]
    seen, display = set(), []
    for v in picks:
        if v not in seen:
            seen.add(v)
            display.append({"video_id": v, "title": titles[v]})
    return {"pool": ordered_vids, "display": display,
            "rep_order": ordered_vids[:REP_DOCS], "cent": cent,
            "pool_vids": vids, "pool_vecs": vecs}


def term_embeddings(texts: list[str]) -> tuple[list[str], np.ndarray]:
    """BAAI/bge-m3 embeddings for candidate terms (GPU fp16, deduped)."""
    uniq = list(dict.fromkeys(texts))
    model = _bgem3()
    dense, _ = model.encode(uniq, batch_size=64, max_length=32)
    return uniq, dense


_EMBED_SERVER = None
_EMBED_ONCE = threading.Once() if hasattr(threading, "Once") else None


class EmbedServer:
    """Single background thread owns bge-m3; torch crashes under
    concurrent encodes, so every caller submits here."""

    def __init__(self):
        import queue
        self.q = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        import sys
        import torch
        repo_root = str(Path(__file__).resolve().parents[2])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from ef.embedding import BGEM3Dual
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = BGEM3Dual(device=device)
        import concurrent.futures as cf
        while True:
            fn, fut = self.q.get()
            try:
                fut.set_result(fn(model))
            except Exception as e:
                fut.set_exception(e)

    def encode_dense(self, texts, batch_size=64, max_length=32):
        import concurrent.futures as cf
        fut = cf.Future()
        self.q.put((lambda m: m.encode(texts, batch_size=batch_size,
                                       max_length=max_length)[0], fut))
        return fut.result()


def get_embed_server() -> EmbedServer:
    global _EMBED_SERVER
    if _EMBED_SERVER is None:
        with threading.Lock():
            if _EMBED_SERVER is None:
                _EMBED_SERVER = EmbedServer()
    return _EMBED_SERVER


def term_embeddings(texts: list[str]) -> tuple[list[str], np.ndarray]:
    """BAAI/bge-m3 embeddings for candidate terms (deduped; routed to the
    single-owner encoder thread)."""
    uniq = list(dict.fromkeys(texts))
    dense = get_embed_server().encode_dense(uniq)
    return uniq, dense


def arm_b_label(candidates: list[str], rep_vecs: np.ndarray) -> tuple[str, list[str]]:
    """KeyBERTInspired-adapted representation: embed candidate terms, score
    by mean cosine to the REP_DOCS most-proximal document embeddings, take
    top-4 terms joined Title-case (same surface convention as Arm A)."""
    if len(rep_vecs) == 0 or not candidates:
        return "", []
    uniq, emb = term_embeddings(candidates)
    pos = {t: i for i, t in enumerate(uniq)}
    scored = []
    for t in candidates[:CAND_TOP]:
        if t not in pos:
            continue
        sim = float(np.mean(emb[pos[t]] @ rep_vecs.T))
        scored.append((t, sim))
    scored.sort(key=lambda x: -x[1])
    top = [t for t, _ in scored[:4]]
    label = " ".join(top).title() if top else ""
    return label, [t for t, _ in scored]
