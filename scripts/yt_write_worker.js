// yt_write_worker.js — parameterized YouTube write worker for page-context
// injection. Companion to P:/.agents/skills/yt-write/SKILL.md.
//
// Injection contract (the skill owns the mechanics; browser-use, ZCode host):
//   1. Inject config first, in chunks if large:
//        window.__ytw_cfg = <json>            (one expression per evaluate)
//   2. Inject this file's contents as ONE trailing-expression-free IIFE:
//        (async () => { ... })()              — see SKILL.md "expression rules"
//   3. Poll progress with tiny evaluates:
//        JSON.stringify({ job: window.__ytw, st: JSON.parse(localStorage.getItem('ytw-v1') || '{}') })
//
// Config (window.__ytw_cfg):
//   op:          "collect-wl" | "create-playlists" | "add" | "remove" | "purge-wl" | "verify" | "delete"
//   expect:      substring the active account handle must match (default "hominidae")
//   stateKey:    localStorage key for THIS run (default "ytw-v1"). Pass a fresh
//                key per logical run — resume works by reusing the same key;
//                sharing a key across unrelated ops resumes stale offsets.
//   plan:        [{ title, videoIds: [id...] }]                     (create-playlists)
//   playlistId:  "PL..." or "WL"                                    (add / remove / verify / delete)
//   playlistIds: [id...]                                            (verify, multi)
//   videoIds:    [id...]                                            (add)
//   setItems:    [{v,s}] pre-collected items                        (remove, optional)
//   batchSize:   100 (max 100)   seedSize: 50   paceMs: 1300   jitterMs: 500
//   privacy:     "PRIVATE" | "UNLISTED" | "PUBLIC"
//
// Durable state: localStorage["ytw-v1"] — crash-resumable; clear the key to
// re-run from scratch. Verification is part of every mutating op (final browse
// re-read); the skill must report the verified numbers, not the worker's intent.
//
// Recipe provenance: P:/.data/wiki/concepts/youtube-playlist-write-page-context-innertube-recipe-2026.md
// Runtime receipts: 2026-09-04 (18 playlists / 6,821 videos; WL purge 3,404 items, zero failed batches).
// agent: zcode  host: zcode (IAB)

(async () => {
  if (window.__ytw && window.__ytw.running) return;
  const cfg = window.__ytw_cfg || {};
  const OP = cfg.op;
  const LS = cfg.stateKey || "ytw-v1";
  const load = function () {
    try { return JSON.parse(localStorage.getItem(LS)) || {}; } catch (e) { return {}; }
  };
  const st = load();
  const save = function () { localStorage.setItem(LS, JSON.stringify(st)); };
  const job = window.__ytw = { running: true, op: OP, phase: "identity", writes: 0 };
  const BATCH = Math.min(cfg.batchSize || 100, 100);
  const SEED = Math.min(cfg.seedSize || 50, BATCH);
  const PACE = cfg.paceMs || 1300;
  const JIT = cfg.jitterMs || 500;

  const ytcfg = function (k) { return window.ytcfg && window.ytcfg.get && window.ytcfg.get(k); };
  const jar = {};
  document.cookie.split(";").map(function (s) { return s.trim(); }).forEach(function (c) {
    const i = c.indexOf("="); if (i > 0) jar[c.slice(0, i)] = c.slice(i + 1);
  });
  const sid = jar["SAPISID"] || jar["__Secure-3PAPISID"] || jar["__Secure-1PAPISID"];

  const auth = async function () {
    const ts = Math.floor(Date.now() / 1000);
    const raw = ts + " " + sid + " " + location.origin;  // timestamp first — verified order
    const buf = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(raw));
    let hex = "";
    new Uint8Array(buf).forEach(function (b) { hex += ("0" + b.toString(16)).slice(-2); });
    return "SAPISIDHASH " + ts + "_" + hex;
  };

  const call = async function (endpoint, body) {
    const headers = {
      "Content-Type": "application/json",
      "Authorization": await auth(),
      "X-Origin": location.origin,
      "X-Goog-AuthUser": String(ytcfg("SESSION_INDEX") || 0),
      "X-Youtube-Client-Name": String(ytcfg("INNERTUBE_CONTEXT_CLIENT_NAME") || 1),
      "X-Youtube-Client-Version": ytcfg("INNERTUBE_CONTEXT_CLIENT_VERSION") || "2.0",
    };
    const pageId = ytcfg("DELEGATED_SESSION_ID");
    if (pageId) headers["X-Goog-PageId"] = pageId;
    const url = location.origin + "/youtubei/v1/" + endpoint +
      "?key=" + encodeURIComponent(ytcfg("INNERTUBE_API_KEY")) + "&prettyPrint=false";
    const res = await fetch(url, {
      method: "POST", credentials: "include", headers,
      body: JSON.stringify({ context: ytcfg("INNERTUBE_CONTEXT"), ...body }),
    });
    let j = null; try { j = await res.json(); } catch (e) {}
    job.writes++;
    return { status: res.status, json: j };
  };

  const sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  const J = function (ms) { return ms + Math.floor(Math.random() * JIT); };
  const walk = function (node, fn) {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) { node.forEach(function (x) { walk(x, fn); }); return; }
    fn(node);
    for (const k in node) walk(node[k], fn);
  };
  const ok = function (r) { return r.status === 200 && r.json && r.json.status === "STATUS_SUCCEEDED"; };

  // Identity gate — abort before any write unless the active account matches.
  const identity = async function () {
    const acct = await call("account/account_menu", {});
    let handle = null, name = null;
    walk(acct.json, function (n) {
      const a = n.activeAccountHeaderRenderer;
      if (!a || handle) return;
      name = (a.accountName && (a.accountName.simpleText || (a.accountName.runs && a.accountName.runs[0] && a.accountName.runs[0].text))) || null;
      handle = (a.channelHandle && (a.channelHandle.simpleText || (a.channelHandle.runs && a.channelHandle.runs[0] && a.channelHandle.runs[0].text))) || null;
    });
    const expect = cfg.expect || "hominidae";
    return { handle: handle, name: name, matched: !!(handle && handle.toLowerCase().indexOf(expect.toLowerCase()) >= 0) };
  };

  // Mid-run account guard inputs, captured after the identity gate.
  let sid0 = null, del0 = null;
  const accountStable = function () {
    return String(ytcfg("SESSION_INDEX") || 0) === sid0 &&
           String(ytcfg("DELEGATED_SESSION_ID") || "") === del0;
  };

  // Paginate a playlist (VL-prefixed browseId), collecting {v, s, t, c}:
  // videoId, setVideoId, title, channel. Resume via st.token.
  const collectItems = async function (browseId, store) {
    if (!store.items) store.items = [];
    const seen = {}; store.items.forEach(function (x) { seen[x.s || x.v] = 1; });
    let token = store.token || null;
    for (let page = 0; page < 60; page++) {
      job.phase = "collect " + browseId + " page " + (page + 1) + " (" + store.items.length + ")";
      const body = { browseId: browseId };
      if (token) body.continuation = token;
      const br = await call("browse", body);
      if (br.status !== 200) { job.error = "browse " + br.status; return false; }
      let next = null;
      walk(br.json, function (n) {
        const pvr = n.playlistVideoRenderer;
        if (pvr && pvr.videoId) {
          const key = pvr.setVideoId || pvr.videoId;
          if (!seen[key]) {
            seen[key] = 1;
            const t = pvr.title && (pvr.title.runs && pvr.title.runs[0] && pvr.title.runs[0].text || pvr.title.simpleText);
            const c = (pvr.shortBylineText && pvr.shortBylineText.runs && pvr.shortBylineText.runs[0] && pvr.shortBylineText.runs[0].text) ||
                      (pvr.ownerText && pvr.ownerText.runs && pvr.ownerText.runs[0] && pvr.ownerText.runs[0].text) || null;
            store.items.push({ v: pvr.videoId, s: pvr.setVideoId || null, t: t || null, c: c || null });
          }
        }
        if (n.continuationCommand && n.continuationCommand.token) next = n.continuationCommand.token;
      });
      if (!next || next === token) { store.token = null; return true; }
      token = next; store.token = token; save();
      await sleep(J(500));
    }
    return true;
  };

  // Batched ACTION_ADD_VIDEO.
  const addBatches = async function (playlistId, videoIds, doneCount) {
    let i = doneCount || 0;
    if (!st.failed) st.failed = [];
    while (i < videoIds.length) {
      if (!accountStable()) { job.aborted = "account changed mid-run"; return i; }
      job.phase = "add " + playlistId + " at " + i;
      const batch = videoIds.slice(i, i + BATCH);
      st.opVideos = videoIds;
      const ed = await call("browse/edit_playlist", {
        playlistId: playlistId,
        actions: batch.map(function (v) { return { action: "ACTION_ADD_VIDEO", addedVideoId: v }; }),
      });
      if (ok(ed)) st.added = i + batch.length;
      else st.failed.push({ kind: "add", at: i, n: batch.length, playlistId: playlistId, status: ed.status, body: JSON.stringify(ed.json).slice(0, 120) });
      job.added = st.added; save();
      i += BATCH;
      await sleep(J(PACE));
    }
    return st.added;
  };

  // Batched ACTION_REMOVE_VIDEO by setVideoId; failed batches get one retry
  // that falls back to ACTION_REMOVE_VIDEO_BY_VIDEO_ID (experimental).
  const removeBatches = async function (playlistId, items, doneCount) {
    let i = doneCount || 0;
    if (!st.failed) st.failed = [];
    while (i < items.length) {
      if (!accountStable()) { job.aborted = "account changed mid-run"; return i; }
      job.phase = "remove " + playlistId + " at " + i;
      const batch = items.slice(i, i + BATCH);
      let ed = await call("browse/edit_playlist", {
        playlistId: playlistId,
        actions: batch.filter(function (x) { return x.s; }).map(function (x) { return { action: "ACTION_REMOVE_VIDEO", setVideoId: x.s }; }),
      });
      if (!ok(ed) && batch.some(function (x) { return !x.s; })) {
        // items without setVideoId: try the by-videoId action (untested axis)
        ed = await call("browse/edit_playlist", {
          playlistId: playlistId,
          actions: batch.map(function (x) { return { action: "ACTION_REMOVE_VIDEO_BY_VIDEO_ID", videoId: x.v }; }),
        });
      }
      if (ok(ed)) st.removed = i + batch.length;
      else st.failed.push({ kind: "remove", at: i, n: batch.length, playlistId: playlistId, status: ed.status, body: JSON.stringify(ed.json).slice(0, 120) });
      job.removed = st.removed; save();
      i += BATCH;
      await sleep(J(PACE));
    }
    return st.removed;
  };

  const retryFailed = async function () {
    for (const f of (st.failed || []).slice()) {
      job.phase = "retry " + f.kind + " at " + f.at;
      if (f.kind === "add") {
        const batch = (st.opVideos && st.opVideos.slice(f.at, f.at + f.n)) || [];
        if (!batch.length) continue;
        const ed = await call("browse/edit_playlist", {
          playlistId: f.playlistId,
          actions: batch.map(function (v) { return { action: "ACTION_ADD_VIDEO", addedVideoId: v }; }),
        });
        if (ok(ed)) st.failed = st.failed.filter(function (x) { return x !== f; });
      } else {
        const batch = ((st.collect && st.collect.items) || st.items || []).slice(f.at, f.at + f.n);
        if (!batch.length) continue;
        const ed = await call("browse/edit_playlist", {
          playlistId: f.playlistId,
          actions: batch.map(function (x) { return { action: "ACTION_REMOVE_VIDEO", setVideoId: x.s }; }),
        });
        if (ok(ed)) st.failed = st.failed.filter(function (x) { return x !== f; });
      }
      save();
      await sleep(J(PACE));
    }
  };

  // Server-side verification: header "N videos" text + first-page item count.
  const verify = async function (playlistId) {
    const br = await call("browse", { browseId: "VL" + playlistId.replace(/^VL/, "") });
    const txt = JSON.stringify(br.json || {});
    const m = txt.match(/"(\d[\d,]*) videos"/);
    let firstPage = 0;
    walk(br.json, function (n) {
      const pvr = n.playlistVideoRenderer;
      if (pvr && (pvr.setVideoId || pvr.videoId)) firstPage++;
    });
    return { playlistId: playlistId, http: br.status, headerCount: m ? parseInt(m[1].replace(/,/g, ""), 10) : null, firstPage: firstPage };
  };

  try {
    if (!OP) { job.error = "no op in __ytw_cfg"; return; }
    const id = await identity();
    job.identity = { handle: id.handle, matched: id.matched };
    if (!id.matched) { job.aborted = "identity gate: " + id.handle + " does not match " + (cfg.expect || "hominidae"); return; }
    sid0 = String(ytcfg("SESSION_INDEX") || 0);
    del0 = String(ytcfg("DELEGATED_SESSION_ID") || "");

    if (OP === "collect-wl") {
      if (!st.collect) st.collect = {};
      await collectItems("VLWL", st.collect);
      job.collected = st.collect.items.length;
      st.done = !job.error; save();

    } else if (OP === "collect") {
      const pid = cfg.playlistId;
      if (!pid) { job.error = "collect needs playlistId"; return; }
      if (!st.collect) st.collect = {};
      const prefix = pid === "WL" ? "VLWL" : "VL" + pid.replace(/^VL/, "");
      await collectItems(prefix, st.collect);
      job.collected = st.collect.items.length;
      st.done = !job.error; save();

    } else if (OP === "purge-wl") {
      if (!st.collect) st.collect = {};
      if (!st.collect.items || st.phase !== "remove") {
        await collectItems("VLWL", st.collect);
      }
      st.phase = "remove"; save();
      await removeBatches("WL", st.collect.items, st.removed || 0);
      await retryFailed();
      st.verify = await verify("WL");
      job.verify = st.verify;
      st.done = (st.failed || []).length === 0; save();

    } else if (OP === "create-playlists") {
      if (!cfg.plan || !cfg.plan.length) { job.error = "empty plan"; return; }
      if (!st.created) st.created = {};
      if (!st.failed) st.failed = [];
      for (const dom of cfg.plan) {
        if (!accountStable()) { job.aborted = "account changed mid-run"; return; }
        const rec = st.created[dom.title] = st.created[dom.title] ||
          { total: dom.videoIds.length, added: 0, playlistId: null, done: false };
        if (rec.done) continue;
        job.phase = dom.title;
        if (!rec.playlistId) {
          const seed = dom.videoIds.slice(0, SEED);
          const cr = await call("playlist/create", {
            title: dom.title, privacyStatus: cfg.privacy || "PRIVATE", videoIds: seed,
          });
          const pid = cr.json && (cr.json.playlistId ||
            (cr.json.actions && cr.json.actions[0] && cr.json.actions[0].createPlaylistAction &&
             cr.json.actions[0].createPlaylistAction.playlistId));
          if (!pid) {
            (st.failed = st.failed || []).push({ kind: "create", title: dom.title, status: cr.status, body: JSON.stringify(cr.json).slice(0, 150) });
            save(); continue;
          }
          rec.playlistId = pid; rec.added = seed.length; save();
          await sleep(J(PACE));
        }
        st.opVideos = dom.videoIds;
        await addBatches(rec.playlistId, dom.videoIds, rec.added);
        rec.added = st.added || rec.added;
        rec.done = rec.added >= rec.total && !(st.failed || []).some(function (f) { return f.playlistId === rec.playlistId || f.title === dom.title; });
        save();
      }
      job.created = Object.keys(st.created).map(function (k) { return k + "=" + st.created[k].playlistId; });
      st.done = (st.failed || []).length === 0; save();

    } else if (OP === "add") {
      const pid = cfg.playlistId;
      if (!pid || !cfg.videoIds) { job.error = "add needs playlistId + videoIds"; return; }
      st.added = st.added || 0;
      await addBatches(pid, cfg.videoIds, st.added);
      await retryFailed();
      st.verify = await verify(pid);
      job.verify = st.verify;
      st.done = (st.failed || []).length === 0; save();

    } else if (OP === "remove") {
      const pid = cfg.playlistId;
      if (!pid) { job.error = "remove needs playlistId"; return; }
      if (cfg.setItems) {
        st.items = cfg.setItems;
      } else {
        if (!st.collect) st.collect = {};
        const prefix = pid === "WL" ? "VLWL" : "VL" + pid.replace(/^VL/, "");
        await collectItems(prefix, st.collect);
        st.items = st.collect.items;
      }
      await removeBatches(pid, st.items, st.removed || 0);
      await retryFailed();
      st.verify = await verify(pid);
      job.verify = st.verify;
      st.done = (st.failed || []).length === 0; save();

    } else if (OP === "delete") {
      const ids = cfg.playlistIds || (cfg.playlistId ? [cfg.playlistId] : []);
      if (!ids.length) { job.error = "delete needs playlistId(s)"; return; }
      if (!st.failed) st.failed = [];
      st.deleted = st.deleted || [];
      for (const pid of ids) {
        job.phase = "delete " + pid;
        const del = await call("playlist/delete", { playlistId: pid.replace(/^VL/, "") });
        if (del.status === 200) st.deleted.push(pid);
        else st.failed.push({ kind: "delete", playlistId: pid, status: del.status, body: JSON.stringify(del.json).slice(0, 120) });
        save();
        await sleep(J(PACE));
      }
      job.deleted = st.deleted;
      st.done = (st.failed || []).length === 0; save();

    } else if (OP === "verify") {
      const ids = cfg.playlistIds || (cfg.playlistId ? [cfg.playlistId] : ["WL"]);
      st.verifications = [];
      for (const pid of ids) {
        st.verifications.push(await verify(pid));
        save();
        await sleep(J(600));
      }
      job.verifications = st.verifications;
      st.done = true; save();

    } else {
      job.error = "unknown op: " + OP;
    }
    job.phase = "complete";
  } catch (e) {
    job.error = String(e && e.message || e).slice(0, 200);
    job.phase = "exception";
  }
  job.running = false;
  save();
})()
