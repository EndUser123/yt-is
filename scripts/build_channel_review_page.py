#!/usr/bin/env python3
"""Build a self-contained channel-classification review page.

Writes .logs/channel_review/review.html with the full channel list embedded
(no server needed — open the file directly). Features:

- Channel names are links opening the YouTube channel in a new tab.
- "Other" channels surfaced first with a review banner (the durable
  ask-the-operator mechanism for unclassifiable channels).
- One column per category; click a cell to set that channel's category
  (single category per channel — the data model and the sync-exclusion
  decision only need one).
- Click a column header to toggle that category as excluded from sync.
- Subscriber and video-count columns to judge a channel's weight.
- A per-channel Block toggle (✕) independent of categories — the apply
  script routes it through the existing soft-blocklist.
- All edits autosave to localStorage (survives an accidental reload);
  a Reset button clears them. Exporting the decisions file is still the
  authoritative save.

Export review_decisions.json for scripts/apply_channel_review.py.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.categorize import CATEGORIES, OTHER_CATEGORY
from csf.paths import get_batch_db_path, get_ytis_log_root

ALL_CATEGORIES = CATEGORIES + [OTHER_CATEGORY]

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>yt-is channel classification review</title>
<style>
  :root {
    --bg: #ffffff; --fg: #1a1a1a; --border: #ddd; --muted: #555; --stat: #444;
    --input-bg: #fff; --input-fg: #1a1a1a; --head-hover: #eef; --filter-bg: #e8f0fe;
    --other-bg: #fff8e1;
    --link: #0b57d0;
    --sel-bg: #0b57d0; --sel-fg: #ffffff;
    --banner-review-bg: #fff3cd; --banner-review-fg: #5c4400;
    --banner-done-bg: #d4edda;  --banner-done-fg: #14522a;
    --banner-dirty-bg: #fde2e2; --banner-dirty-fg: #8a1111;
    --excl-bg: #fdd;            --excl-fg: #7a1010;
    --touched-bg: #fff3e0;      --flash-bg: #ffe0b2;
  }
  body.dark {
    --bg: #16181d; --fg: #e6e8ea; --border: #3a3f47; --muted: #9aa1ab; --stat: #aeb4bc;
    --input-bg: #22252b; --input-fg: #e6e8ea; --head-hover: #262b34; --filter-bg: #24304a;
    --other-bg: #33301d;
    --link: #8ab4f8;
    --sel-bg: #8ab4f8; --sel-fg: #10143a;
    --banner-review-bg: #4a3f10; --banner-review-fg: #ffe08a;
    --banner-done-bg: #173d24;  --banner-done-fg: #93e2a9;
    --banner-dirty-bg: #4a1616; --banner-dirty-fg: #ffabab;
    --excl-bg: #4a1414;         --excl-fg: #ffabab;
    --touched-bg: #33270f;      --flash-bg: #5c4318;
  }
  body { font-family: Segoe UI, Arial, sans-serif; margin: 16px; color: var(--fg); background: var(--bg); }
  h1 { font-size: 18px; margin: 0 0 4px; }
  #banner { padding: 8px 12px; border-radius: 6px; margin: 8px 0; font-weight: 600; }
  #banner.review { background: var(--banner-review-bg); color: var(--banner-review-fg); border: 1px solid var(--banner-review-fg); }
  #banner.done { background: var(--banner-done-bg); color: var(--banner-done-fg); border: 1px solid var(--banner-done-fg); }
  #banner.dirty { background: var(--banner-dirty-bg); color: var(--banner-dirty-fg); border: 1px solid var(--banner-dirty-fg); }
  #controls { margin: 8px 0; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  input[type=text], select { padding: 6px; border: 1px solid var(--border); border-radius: 4px; background: var(--input-bg); color: var(--input-fg); }
  button { padding: 8px 14px; border: 0; border-radius: 4px; background: #0b57d0; color: #fff; cursor: pointer; }
  button.secondary { background: #5f6368; }
  table { border-collapse: collapse; width: 100%; margin-top: 8px; }
  th, td { border: 1px solid var(--border); padding: 4px 6px; font-size: 13px; }
  th.cat { cursor: pointer; writing-mode: vertical-rl; transform: rotate(180deg); height: 130px;
           font-size: 12px; min-width: 42px; padding: 4px 8px; text-align: left; vertical-align: bottom; }
  /* Headers are VIEW-ONLY tri-state: blue = only this, red = hide this, none = all */
  th.cat.focus { background: var(--sel-bg); color: var(--sel-fg); }
  th.cat.hidden { background: var(--excl-bg); color: var(--excl-fg); opacity: 0.7; }
  th.cat:hover { background: var(--head-hover); }
  td.name { width: var(--name-w, 320px); max-width: var(--name-w, 320px);
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  th.draggable-col { position: relative; }
  .resizer { position: absolute; right: -7px; top: 0; width: 14px; height: 100%;
             cursor: col-resize; user-select: none; z-index: 2;
             display: flex; align-items: center; justify-content: center; }
  .resizer::after { content: '⋮'; font-size: 15px; font-weight: 700;
                    color: var(--muted); letter-spacing: -1px; }
  .resizer:hover { background: var(--sel-bg); }
  .resizer:hover::after { color: var(--sel-bg); filter: invert(1) grayscale(1) brightness(1.6); }
  td.name a { color: var(--link); text-decoration: none; }
  td.name a:hover { text-decoration: underline; }
  td.cell { cursor: pointer; min-width: 26px; text-align: center; }
  td.cell.set { background: var(--sel-bg); color: var(--sel-fg); font-weight: 700; }
  td.num { text-align: right; color: var(--muted); white-space: nowrap; }
  td.blockcell { cursor: pointer; text-align: center; font-weight: 700; }
  td.blockcell.on { background: #d00; color: #fff; }
  td.blockcell.viaexcluded { outline: 2px dashed #d00; outline-offset: -2px; }
  td.blockcell.exempt.on { background: #1e8e3e; color: #fff; }
  td.blockcell.reclass.on { background: #7b1fa2; color: #fff; }
  tr.other td.name { background: var(--other-bg); font-weight: 600; }
  tr.wasblocked td { opacity: 0.55; }
  tr.dead td.name a { text-decoration: line-through; }
  tr.catexcluded td { opacity: 0.55; }
  tr.catexcluded td.name { background: var(--excl-bg); color: var(--excl-fg); }
  tr.catexcluded.exemptrow td { opacity: 1; }  /* stars override the dim */
  #pager { margin: 10px 0; display: flex; gap: 6px; align-items: center; }
  #stats { margin-top: 10px; font-size: 12px; color: var(--stat); }
  .changed { outline: 2px solid #e37400; }
  tr.touched td { background: var(--touched-bg); }
  tr.touched td.name { background: var(--touched-bg); }  /* overrides Other tint */
  tr.touched td:first-child { box-shadow: inset 5px 0 0 #e37400; }  /* used-row bar */
  tr.touched td.cell.set { background: var(--sel-bg); }  /* selected cell stays readable */
  tr.flash td { background: var(--flash-bg) !important; transition: background 0.15s; }
  td.cell { transition: background 0.15s; }
  .chip { padding: 3px 10px; border-radius: 12px; border: 1px solid var(--border);
          cursor: pointer; font-size: 12px; background: var(--input-bg); color: var(--fg); }
  .chip.on { background: #d00; color: #fff; border-color: #d00; }
  #theme { font-size: 16px; padding: 6px 10px; }
  th.cat.filtered { background: var(--sel-bg); color: var(--sel-fg); outline: 2px solid var(--sel-bg); }
  th.sortactive { background: var(--filter-bg); }
</style>
</head>
<body>
<h1 style="display:inline">yt-is channel classification review</h1>
<span id="builtat" style="float:right; margin:6px 14px 0 0; font-size:12px; color:var(--stat)"></span>
<button id="refresh" class="secondary" style="float:right; margin-left:6px" title="Reload the page with the latest applied data (the apply step regenerates this file)">↻ Refresh data</button>
<button id="theme" class="secondary" style="float:right" title="Toggle day/night theme">🌙</button>
<div id="banner"></div>
<div id="controls">
  <input type="text" id="search" placeholder="Search channel name..." size="30">
  <select id="filter">
    <option value="">All categories</option>
    __FILTER_OPTIONS__
  </select>
  <span>Click a cell to set a category (<b>✓</b> = selected; orange outline = your
  change) &middot; click a column header to <b>include</b> that category in the view — click
  again to remove it; no headers clicked = show all &middot; ✕ blocks one channel &middot;
  names open the channel.</span>
</div>
<div id="excludedchips" style="margin:6px 0; display:flex; gap:6px; flex-wrap:wrap; align-items:center">
  <b style="font-size:13px">Exclude from sync:</b><span id="chips"></span>
</div>
<div style="margin:2px 0 8px; font-size:12px; color:var(--stat)">
  A red chip excludes the <b>entire category</b> — one click, no row-by-row selecting.
  &middot; ✓ sets a channel's category &middot; ✕ blocks one channel &middot;
  <b style="color:#1e8e3e">★ keeps one channel in sync even though its category is excluded</b>
  (stars survive every future exclusion; click the ★ again to remove the exception —
  the channel rejoins its category's fate at the next apply).
</div>
<div id="pager">
  <button class="secondary" id="prev">&larr; Prev</button>
  <span id="pageinfo"></span>
  <button class="secondary" id="next">Next &rarr;</button>
  <button id="bulkexclude" style="background:#b3261e" title="Exclude every channel in the current view from sync">Exclude all shown</button>
  <button class="secondary" id="clearfilters" title="Remove all category filters">Clear filters (<span id="filtern">0</span>)</button>
  <button class="secondary" id="provauto" title="Show only auto-categorised channels (llm-derived, re-derivable)">Auto (<span id="provaution">0</span>)</button>
  <button class="secondary" id="provmanual" title="Show only manually classified channels (sticky, locked from auto-reclassification)">Manual (<span id="provmanualn">0</span>)</button>
  <button class="secondary" id="lockshown" style="background:#5f6368" title="Lock every shown channel's classification (sticky: auto reclassify never touches them)">Lock all shown</button>
  <button class="secondary" id="autocatshown" style="background:#7b1fa2" title="Mark every shown channel for automatic reclassification with full evidence">Auto-categorize all shown</button>
</div>
<table>
  <thead><tr id="headrow"></tr></thead>
  <tbody id="tbody"></tbody>
</table>
<div id="stats"></div>
<div style="margin-top:14px; display:flex; gap:8px">
  <button id="export">Download review_decisions.json</button>
  <button class="secondary" id="copy">Copy JSON to clipboard</button>
  <button class="secondary" id="reset">Reset local changes</button>
  <button class="secondary" id="revertcats" title="Undo accidental category reassignments (keeps blocks, stars, exclusions)">Revert category edits (<span id="revertn">0</span>)</button>
</div>
<script>
window.addEventListener('error', (e) => {
  const d = document.createElement('div');
  d.id = 'jserr';
  d.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#d00;color:#fff;'
    + 'padding:6px 10px;font:12px monospace;z-index:9999;white-space:pre-wrap';
  d.textContent = 'JS ERROR: ' + (e.message || e.error) + ' @ ' + (e.filename || '') + ':' + (e.lineno || '');
  document.body.appendChild(d);
});
window.addEventListener('unhandledrejection', (e) => {
  const d = document.createElement('div');
  d.id = 'jsrej';
  d.style.cssText = 'position:fixed;bottom:22px;left:0;right:0;background:#b3261e;color:#fff;'
    + 'padding:6px 10px;font:12px monospace;z-index:9999;white-space:pre-wrap';
  d.textContent = 'REJECTION: ' + (e.reason && e.reason.message ? e.reason.message : e.reason);
  document.body.appendChild(d);
});
const BUILT_AT = __BUILT_AT__;
const DATA = __DATA__;
const CATS = __CATS__;
const INIT_BLOCKED = new Set(__BLOCKED__);
const INIT_EXEMPT = new Set(__EXEMPT__);
const INIT_LOCKED = new Set(__LOCKED__);  // category_source='manual' (sticky)
const STORAGE_KEY = 'ytis_channel_review_v1';
const PAGE_SIZE = 100;
let excluded = new Set(__EXCLUDED__);
let assignments = {};   // url -> category (operator changes only)
let blocked = {};       // url -> true/false (operator overrides of INIT_BLOCKED)
let touched = new Set(); // urls the operator clicked a cell on (review progress)
let exemptions = {};    // url -> true/false overrides of INIT_EXEMPT (★ exceptions)
let reclassify = {};    // url -> true (⟳ mark: re-classify with full evidence on apply)
let cleared = {};       // url -> true (category cleared to unclassified)
let locks = {};         // url -> true/false overrides of INIT_LOCKED (sticky)
// View-only filter state (not part of decisions/localStorage): an empty set
// shows ALL categories; each click includes/excludes one category from the
// view. First click always means include — exclusion is the chips' job.
// Tri-state per-category view filter: focus (blue, "only this") or
// hidden (red, "everything but this") or neutral (no effect).
// Click cycles: neutral → focus → hidden → neutral.
let focusFilters = new Set();   // blue: show ONLY these categories
let blockedFilter = '';          // '': all | 'blocked': only blocked | 'active': only unblocked
let hiddenFilters = new Set();  // red: hide these categories from view
// Provenance filter: 'auto' (llm-derived, re-derivable) vs 'manual'
// (human/agent-decided, sticky). Empty set = both shown.
let provFilters = new Set();
// Column sort: null = default order (Other-first, then name). Clicking a
// sortable header sets it (first click: numbers desc, names asc); clicking
// again flips direction; a third click returns to the default order.
let sortKey = null;   // 't' | 's' | 'v' | null
let sortDir = 1;
let page = 0;

function loadLocal() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    if (saved) {
      assignments = saved.assignments || {};
      excluded = new Set(saved.excluded || []);
      blocked = saved.blocked || {};
      exemptions = saved.exemptions || {};
      reclassify = saved.reclassify || {};
      cleared = saved.cleared || {};
      locks = saved.locks || {};
      touched = new Set(saved.touched || []);
    }
  } catch (e) { /* corrupt local state is ignorable */ }
}
function saveLocal() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    assignments, excluded: [...excluded], blocked, touched: [...touched], exemptions,
    reclassify, cleared, locks
  }));
  renderBanner(); renderStats();
}
function effectiveCategory(row) {
  if (cleared[row.u]) return '';  // cleared to unclassified (pending apply)
  return assignments[row.u] || row.c;
}
function isBlocked(row) {
  return blocked.hasOwnProperty(row.u) ? blocked[row.u] : INIT_BLOCKED.has(row.u);
}
function provenanceOf(row) {
  return isLocked(row) ? 'manual' : 'auto';
}
function isLocked(row) {
  return locks.hasOwnProperty(row.u) ? locks[row.u] : INIT_LOCKED.has(row.u);
}
function isExempt(row) {
  return exemptions.hasOwnProperty(row.u) ? exemptions[row.u] : INIT_EXEMPT.has(row.u);
}
function dirtyCount() {
  const changedCats = Object.keys(assignments).filter(u => {
    const row = DATA.find(r => r.u === u);
    return row && assignments[u] !== row.c;
  }).length;
  const changedBlocked = Object.keys(blocked).filter(u => blocked[u] !== INIT_BLOCKED.has(u)).length;
  return changedCats + changedBlocked;
}
function renderBanner() {
  const b = document.getElementById('banner');
  const marked = Object.keys(reclassify).filter(u => reclassify[u]).length;
  if (marked > 0) {
    b.className = 'review';
    b.textContent = marked + ' channel(s) marked ⟳ for reclassification — the categories change when you APPLY: '
      + 'click "Download review_decisions.json", then run apply_channel_review.py on it '
      + '(or tell your agent). Marking alone never reclassifies.';
    return;
  }
  const dirty = dirtyCount();
  if (dirty > 0) {
    b.className = 'dirty';
    b.textContent = dirty + ' unsaved local changes — autosaved in this browser only; use "Download review_decisions.json" to apply them.';
    return;
  }
  const otherCount = DATA.filter(r => effectiveCategory(r) === 'Other').length;
  if (otherCount > 0) {
    b.className = 'review';
    b.textContent = otherCount + ' channels are classified "Other" (highlighted, sorted first) — click a category cell to classify them manually.';
  } else {
    b.className = 'done';
    b.textContent = 'No "Other" channels remain.';
  }
}
function renderHead() {
  const tr = document.getElementById('headrow');
  tr.innerHTML = '';
  const sortDefs = [
    { key: 't', label: 'Channel', title: 'Sort by channel name' },
    { key: 's', label: 'Subs', title: 'Sort by subscriber count' },
    { key: 'v', label: 'Videos', title: 'Sort by video count' },
    { key: 'sh', label: 'Shorts', title: 'Sort by shorts count' },
    { key: 'pl', label: 'Lists', title: 'Sort by playlist count' },
  ];
  for (const def of sortDefs) {
    const th = document.createElement('th');
    const active = sortKey === def.key;
    th.textContent = def.label + (active ? (sortDir === 1 ? ' ▲' : ' ▼') : '');
    th.title = def.title + ' (click again to flip direction; third click = default order)';
    th.style.cursor = 'pointer';
    th.style.userSelect = 'none';
    if (active) th.className = 'sortactive';
    th.onclick = () => {
      if (sortKey !== def.key) {
        sortKey = def.key;
        sortDir = def.key === 't' ? 1 : -1;  // names A→Z first; numbers big→small first
      } else if (sortDir === (def.key === 't' ? 1 : -1)) {
        sortDir = -sortDir;
      } else {
        sortKey = null;  // third click: back to default (Other-first, name)
        sortDir = 1;
      }
      page = 0;
      renderHead(); renderBody();
    };
    tr.appendChild(th);
    if (def.key === 't') {
      // Resize handle must be appended AFTER the label assignment above —
      // setting textContent on the th wipes previously appended children.
      th.classList.add('draggable-col');
      const handle = document.createElement('div');
      handle.className = 'resizer';
      handle.title = 'Drag to make the Channel column narrower or wider';
      let dragging = false, startX = 0, startW = 0;
      const currentW = () => {
        const v = getComputedStyle(document.documentElement)
          .getPropertyValue('--name-w');
        const n = parseFloat(v);
        return isNaN(n) ? 320 : n;
      };
      handle.addEventListener('mousedown', (e) => {
        dragging = true; startX = e.clientX; startW = currentW();
        e.preventDefault(); e.stopPropagation();
        document.body.style.userSelect = 'none';
      });
      handle.addEventListener('click', (e) => e.stopPropagation());
      document.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        const w = Math.min(900, Math.max(120, startW + (e.clientX - startX)));
        document.documentElement.style.setProperty('--name-w', w + 'px');
      });
      document.addEventListener('mouseup', () => {
        if (!dragging) return;
        dragging = false;
        document.body.style.userSelect = '';
        localStorage.setItem('ytis_channel_review_namew', String(currentW()));
      });
      th.appendChild(handle);
    }
  }
  for (const cat of CATS) {
    const th = document.createElement('th');
    const state = focusFilters.has(cat) ? 'focus'
                : hiddenFilters.has(cat) ? 'hidden' : '';
    th.className = 'cat' + (state ? ' ' + state : '');
    th.textContent = cat;
    th.title = state === 'focus'
      ? 'Showing ONLY ' + cat + ' — click to HIDE ' + cat
      : state === 'hidden'
      ? cat + ' is HIDDEN — click to clear (show all)'
      : 'Click to show only ' + cat;
    th.onclick = () => {
      if (focusFilters.has(cat)) {
        focusFilters.delete(cat);
        hiddenFilters.add(cat);
      } else if (hiddenFilters.has(cat)) {
        hiddenFilters.delete(cat);
      } else {
        focusFilters.add(cat);
      }
      page = 0;
      renderHead(); renderBody(); renderStats();
    };
    tr.appendChild(th);
  }
  const blockTh = document.createElement('th');
  blockTh.textContent = '✕';
  const bState = blockedFilter;
  blockTh.className = bState === 'blocked' ? 'focus' : bState === 'active' ? 'hidden' : '';
  blockTh.style.cursor = 'pointer';
  blockTh.title = bState === 'blocked'
    ? 'Showing ONLY blocked channels — click to show only active'
    : bState === 'active'
    ? 'Showing only ACTIVE (unblocked) — click to show all'
    : 'Click to show only blocked/excluded channels';
  blockTh.onclick = () => {
    blockedFilter = blockedFilter === '' ? 'blocked' : blockedFilter === 'blocked' ? 'active' : '';
    page = 0;
    renderHead(); renderBody(); renderStats();
  };
  tr.appendChild(blockTh);
  const exemptTh = document.createElement('th');
  exemptTh.textContent = '★';
  exemptTh.title = 'Exception: keep this channel even if its category is excluded';
  tr.appendChild(exemptTh);
  const reTh = document.createElement('th');
  reTh.textContent = '⟳';
  reTh.title = 'Mark for automatic reclassification with full evidence (title + description + video titles)';
  tr.appendChild(reTh);
}
function visibleRows() {
  const q = document.getElementById('search').value.toLowerCase();
  const flt = document.getElementById('filter').value;
  let rows = DATA.filter(r => {
    const cat = effectiveCategory(r);
    if (focusFilters.size > 0 && !focusFilters.has(cat)) return false;
    if (hiddenFilters.has(cat)) return false;
    if (blockedFilter === 'blocked' && !isBlocked(r)) return false;
    if (blockedFilter === 'active' && isBlocked(r)) return false;
    if (provFilters.size && !provFilters.has(provenanceOf(r))) return false;
    if (q && !(r.t || '').toLowerCase().includes(q)) return false;
    return true;
  });
  // Anchor on the ORIGINAL category: manually-classified review rows keep
  // their place in the list instead of jumping out from under the cursor.
  if (sortKey === null) {
    rows.sort((a, b) => {
      const ea = a.c === 'Other' ? 0 : 1;
      const eb = b.c === 'Other' ? 0 : 1;
      if (ea !== eb) return ea - eb;
      return (a.t || '').localeCompare(b.t || '');
    });
  } else if (sortKey === 't') {
    rows.sort((a, b) => sortDir * (a.t || '').localeCompare(b.t || ''));
  } else {
    const key = sortKey;
    rows.sort((a, b) => {
      const av = a[key], bv = b[key];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;   // unknown counts last regardless of direction
      if (bv == null) return -1;
      return sortDir * (Number(av) - Number(bv));
    });
  }
  return rows;
}
function renderBody() {
  const rows = visibleRows();
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  if (page >= pages) page = pages - 1;
  const slice = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const tb = document.getElementById('tbody');
  tb.innerHTML = '';
  for (const row of slice) {
    const tr = document.createElement('tr');
    tr.id = 'row-' + row.u.slice(-16);
    if (row.c === 'Other') tr.className = 'other';
    if (isBlocked(row)) tr.classList.add('wasblocked');
    if (row.dead) {
      tr.classList.add('wasblocked');
      const deadTag = document.createElement('span');
      deadTag.textContent = ' [' + row.dead + ']';
      deadTag.title = 'Channel is ' + row.dead + ' — no longer fetchable; marked automatically';
      deadTag.style.color = 'var(--excl-fg)';
      tr.querySelector('td.name').appendChild(deadTag);
    }
    if (excluded.has(effectiveCategory(row))) {
      tr.classList.add('catexcluded');
      if (isExempt(row)) tr.classList.add('exemptrow');
    }
    if (assignments[row.u] && assignments[row.u] !== row.c) tr.classList.add('changed');
    if (touched.has(row.u)) tr.classList.add('touched');
    const td = document.createElement('td');
    td.className = 'name';
    const a = document.createElement('a');
    a.href = row.u;
    a.target = '_blank';
    a.rel = 'noopener';
    a.textContent = row.t || row.u;
    td.appendChild(a);
    td.title = (row.d || '') + ' — ' + row.u;
    tr.appendChild(td);
    // Per-column semantics: Shorts/Lists distinguish NULL ('…' = backfill
    // pending) from a real 0 (no such tab). Subs shows 0 only if measured.
    // Videos is enumeration-only: 0/NULL means sync has not counted yet.
    const fmt = {
      s: (x) => x == null ? '—' : Number(x).toLocaleString(),
      v: (x) => (x != null && Number(x) > 0) ? Number(x).toLocaleString() : '—',
      sh: (x) => x == null ? '…' : Number(x).toLocaleString(),
      pl: (x) => x == null ? '…' : Number(x).toLocaleString(),
    };
    for (const key of ['s', 'v', 'sh', 'pl']) {
      const td = document.createElement('td');
      td.className = 'num';
      td.textContent = fmt[key](row[key]);
      tr.appendChild(td);
    }
    for (const cat of CATS) {
      const c = document.createElement('td');
      c.className = 'cell' + (effectiveCategory(row) === cat ? ' set' : '');
      c.textContent = effectiveCategory(row) === cat ? '✓' : '';
      c.onclick = () => {
        if (effectiveCategory(row) === cat) {
          // Un-select: clicking the set cell reverses the decision AND
          // removes the orange touched highlight (review progress marker).
          if (assignments[row.u] === cat && row.c !== cat) {
            delete assignments[row.u];  // revert your pending override
          } else if (assignments[row.u] === cat || row.c === cat) {
            const okClear = confirm(
              'Clear this channel category to unclassified?\\n' +
              'It becomes pending and can be re-classified later (reclassify ' +
              'column or categorize --retry-other).');
            if (!okClear) return;
            delete assignments[row.u];
            cleared[row.u] = true;
          }
          touched.delete(row.u);  // clearing the decision clears the mark
          saveLocal(); renderBody(); renderStats();
          return;
        }
        if (cat === 'Other' && row.c && row.c !== 'Other'
            && effectiveCategory(row) !== 'Other') {
          // 'Other' sits beside the block column and catches stray clicks;
          // make the reassignment explicit and name the better tools.
          const ok = confirm(
            'Move this channel from ' + row.c + ' to Other?\\n' +
            'Other means unclassifiable — to stop syncing it, use the block ' +
            'column (exclude) or the category chips instead.');
          if (!ok) return;
        }
        assignments[row.u] = cat;
        touched.add(row.u);
        saveLocal(); renderBody(); renderPageInfo();
        const trEl = document.getElementById('row-' + row.u.slice(-16));
        if (trEl) {
          trEl.classList.add('flash');
          setTimeout(() => trEl.classList.remove('flash'), 350);
        }
      };
      tr.appendChild(c);
    }
    const bc = document.createElement('td');
    const catExcluded = excluded.has(effectiveCategory(row));
    const perChannelBlocked = isBlocked(row);
    const willBlock = perChannelBlocked || (catExcluded && !isExempt(row));
    bc.className = 'blockcell'
      + (willBlock ? ' on' : '')
      + (!perChannelBlocked && willBlock ? ' viaexcluded' : '');
    bc.textContent = willBlock ? '✕' : '';
    if (perChannelBlocked) {
      bc.title = 'Blocked specifically — click to unblock';
    } else if (willBlock) {
      bc.title = 'Blocked via the ' + effectiveCategory(row)
        + ' exclusion (dashed = inherited) — click ★ to keep this one channel';
    } else {
      bc.title = 'Toggle block for this channel';
    }
    bc.onclick = () => {
      if (!perChannelBlocked && catExcluded) {
        // The only per-channel escape from a category exclusion is the
        // star exception — clicking ✕ here grants it (what the user means).
        exemptions[row.u] = !isExempt(row);
      } else {
        blocked[row.u] = !isBlocked(row);
      }
      saveLocal(); renderBody(); renderPageInfo();
    };
    tr.appendChild(bc);
    const ec = document.createElement('td');
    ec.className = 'blockcell exempt' + (isExempt(row) ? ' on' : '');
    ec.textContent = isExempt(row) ? '★' : '';
    ec.title = isExempt(row)
      ? 'Exception active: this channel stays in sync even if its category is excluded'
      : 'Make an exception: keep this channel in sync even if its category is excluded';
    ec.onclick = () => {
      exemptions[row.u] = !isExempt(row);
      saveLocal(); renderBody(); renderPageInfo();
    };
    tr.appendChild(ec);
    const rc = document.createElement('td');
    const marked = !!reclassify[row.u];
    rc.className = 'blockcell reclass' + (marked ? ' on' : '');
    rc.textContent = marked ? '⟳' : '';
    rc.title = marked
      ? 'Marked: re-classified from full evidence at the next apply'
      : 'Mark for automatic reclassification (title, description, video titles)';
    rc.onclick = () => {
      reclassify[row.u] = !reclassify[row.u];
      saveLocal(); renderBody(); renderStats();
    };
    tr.appendChild(rc);
    tb.appendChild(tr);
  }
  document.getElementById('pageinfo').textContent =
    'Page ' + (page + 1) + ' / ' + pages + ' — ' + rows.length + ' channels';
}
function renderChips() {
  const holder = document.getElementById('chips');
  holder.innerHTML = '';
  for (const cat of CATS) {
    if (cat === 'Other') continue;  // 'Other' is not a real exclusion category
    const chip = document.createElement('button');
    const catCount = DATA.filter(r => effectiveCategory(r) === cat).length;
    chip.textContent = cat + ' · ' + catCount;
    chip.className = 'chip' + (excluded.has(cat) ? ' on' : '');
    const catRows = DATA.filter(r => effectiveCategory(r) === cat);
    const impact = catRows.length + ' channels, ~' +
      catRows.reduce((a, r) => a + (r.v || 0), 0).toLocaleString() + ' videos, ~' +
      catRows.reduce((a, r) => a + (r.sh || 0), 0).toLocaleString() + ' shorts';
    chip.title = (excluded.has(cat)
      ? cat + ' IS excluded from sync (' + impact + ') — click to include again'
      : 'Exclude ' + cat + ' from sync (' + impact + ')');
    chip.onclick = () => {
      if (excluded.has(cat)) excluded.delete(cat); else excluded.add(cat);
      saveLocal(); renderChips(); renderHead(); renderBody(); renderStats();
    };
    holder.appendChild(chip);
    holder.appendChild(document.createTextNode(' '));
  }
}
function dirtyAssignCount() {
  return Object.keys(assignments).filter(u => {
    const row = DATA.find(r => r.u === u);
    return row && assignments[u] !== row.c;
  }).length;
}
function renderStats() {
  const counts = {};
  for (const r of DATA) {
    const c = effectiveCategory(r);
    counts[c] = (counts[c] || 0) + 1;
  }
  const excludedRows = DATA.filter(r => excluded.has(effectiveCategory(r)));
  const excludedCount = excludedRows.length;
  const excludedVideos = excludedRows.reduce((a, r) => a + (r.v || 0), 0);
  const excludedShorts = excludedRows.reduce((a, r) => a + (r.sh || 0), 0);
  const blockedCount = DATA.filter(r => isBlocked(r)).length;
  const lines = CATS.map(c => c + ': ' + (counts[c] || 0) + (excluded.has(c) ? ' (excluded)' : ''));
  const pa = document.getElementById('provaution');
  const pm = document.getElementById('provmanualn');
  if (pa) {
    pa.textContent = String(DATA.filter(r => provenanceOf(r) === 'auto').length);
    document.getElementById('provauto').style.fontWeight = provFilters.has('auto') ? '700' : '';
  }
  if (pm) {
    pm.textContent = String(DATA.filter(r => provenanceOf(r) === 'manual').length);
    document.getElementById('provmanual').style.fontWeight = provFilters.has('manual') ? '700' : '';
  }
  const fn = document.getElementById('filtern');
  if (fn) fn.textContent = String(focusFilters.size + hiddenFilters.size);
  const rn = document.getElementById('revertn');
  if (rn) rn.textContent = String(dirtyAssignCount());
  document.getElementById('stats').innerHTML =
    lines.join(' &middot; ') + '<br><b>' + dirtyCount() + ' unsaved changes &middot; ' +
    excludedCount + ' channels in excluded categories &middot; ' + blockedCount +
    ' blocked &middot; ' + touched.size + ' rows you reviewed</b>' +
    (excluded.size ? '<br><b style="color:var(--excl-fg)">' + DATA.filter(isExempt).length +
      ' ★ exceptions keep their channels in sync despite exclusions &middot; Fetch impact: ~' +
      excludedVideos.toLocaleString() + ' videos + ~' + excludedShorts.toLocaleString() +
      ' shorts will NOT be fetched</b>' : '');
}
function renderPageInfo() {}
function decisions() {
  const changedAssign = Object.fromEntries(Object.entries(assignments).filter(([u, c]) => {
    const row = DATA.find(r => r.u === u);
    return !row || row.c !== c;
  }));
  const block_urls = Object.keys(blocked).filter(u => blocked[u] && !INIT_BLOCKED.has(u));
  const unblock_urls = Object.keys(blocked).filter(u => !blocked[u] && INIT_BLOCKED.has(u));
  const exception_urls = Object.keys(exemptions).filter(u => exemptions[u] && !INIT_EXEMPT.has(u));
  const unexception_urls = Object.keys(exemptions).filter(u => !exemptions[u] && INIT_EXEMPT.has(u));
  const reclassify_urls = Object.keys(reclassify).filter(u => reclassify[u]);
  const clear_category_urls = Object.keys(cleared).filter(u => cleared[u]);
  const lock_urls = Object.keys(locks).filter(u => locks[u] && !INIT_LOCKED.has(u));
  const unlock_urls = Object.keys(locks).filter(u => !locks[u] && INIT_LOCKED.has(u));
  return {
    exported_at: new Date().toISOString(),
    excluded_categories: [...excluded],
    assignments: changedAssign,
    block_urls,
    unblock_urls,
    exception_urls,
    unexception_urls,
    reclassify_urls,
    clear_category_urls,
    lock_urls,
    unlock_urls,
  };
}
function download() {
  const blob = new Blob([JSON.stringify(decisions(), null, 2)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'review_decisions.json';
  a.click();
}
document.getElementById('search').oninput = () => { page = 0; renderBody(); };
document.getElementById('filter').onchange = () => {
  const flt = document.getElementById('filter').value;
  focusFilters = flt ? new Set([flt]) : new Set();
  hiddenFilters = new Set();
  page = 0; renderHead(); renderBody();
};
document.getElementById('bulkexclude').onclick = () => {
  const rows = visibleRows();
  if (!rows.length) return;
  if (focusFilters.size > 0) {
    // Focused view (one or more categories blue): toggle the chip for
    // each focused category — the durable category-level mechanism.
    const names = [...focusFilters].filter(c => c !== 'Other');
    const msg = names.length === 1
      ? names[0]
      : names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1];
    const allExcluded = names.every(c => excluded.has(c));
    const ok = confirm((allExcluded ? 'UN-exclude' : 'Exclude')
      + ' ' + msg + ' from sync?'
      + (allExcluded ? '' : '\\n~' + rows.length + ' channels will not be fetched.'));
    if (!ok) return;
    for (const c of names) {
      if (allExcluded) excluded.delete(c); else excluded.add(c);
    }
  } else {
    // Unfiltered or search-based view: block every shown channel individually.
    if (!confirm('Block all ' + rows.length + ' shown channel(s) individually?')) return;
    for (const r of rows) blocked[r.u] = true;
  }
  saveLocal(); renderChips(); renderHead(); renderBody(); renderStats();
};
document.getElementById('provauto').onclick = () => {
  if (provFilters.has('auto')) provFilters.delete('auto'); else provFilters.add('auto');
  page = 0; renderHead(); renderBody(); renderStats();
};
document.getElementById('provmanual').onclick = () => {
  if (provFilters.has('manual')) provFilters.delete('manual'); else provFilters.add('manual');
  page = 0; renderHead(); renderBody(); renderStats();
};
document.getElementById('lockshown').onclick = () => {
  const rows = visibleRows();
  if (!rows.length) return;
  if (!confirm('Lock the classification of all ' + rows.length
      + ' shown channel(s)? Locked classifications are sticky — automatic'
      + ' reclassification will never overwrite them.')) return;
  for (const r of rows) locks[r.u] = true;
  saveLocal(); renderBody(); renderStats();
};
document.getElementById('autocatshown').onclick = () => {
  const rows = visibleRows();
  if (!rows.length) return;
  if (!confirm('Mark all ' + rows.length + ' shown channel(s) for automatic'
      + ' reclassification with full evidence (title + description + video'
      + ' titles)? Applied at the next apply; current categories are'
      + ' overwritten by the result.')) return;
  for (const r of rows) { reclassify[r.u] = true; locks[r.u] = false; }
  saveLocal(); renderBody(); renderStats();
};
document.getElementById('clearfilters').onclick = () => {
  focusFilters = new Set();
  hiddenFilters = new Set();
  blockedFilter = '';
  provFilters = new Set();
  document.getElementById('filter').value = '';
  page = 0; renderHead(); renderBody(); renderStats();
};
document.getElementById('prev').onclick = () => { if (page > 0) { page--; renderBody(); } };
document.getElementById('next').onclick = () => { page++; renderBody(); };
document.getElementById('export').onclick = download;
document.getElementById('copy').onclick = () => {
  navigator.clipboard.writeText(JSON.stringify(decisions(), null, 2));
};
window.addEventListener('beforeunload', (e) => {
  // NOTE: an unconditional-preventDefault variant of this guard made an
  // embedded webview swallow ALL click events on the page; the guard is
  // deliberately minimal and conditional. If clicks ever die page-wide,
  // suspect this listener first.
  if (dirtyCount() > 0) {
    e.returnValue = '';  // browser shows its own leave-with-changes prompt
  }
});
document.getElementById('revertcats').onclick = () => {
  const n = dirtyAssignCount();
  if (!n) { alert('No category edits to revert.'); return; }
  if (!confirm('Revert ' + n + ' category edit(s) back to the database values?\\n'
      + 'Blocks, stars, and exclusions are kept.')) return;
  assignments = {};
  saveLocal(); renderBanner(); renderHead(); renderBody(); renderStats();
};
document.getElementById('reset').onclick = () => {
  if (confirm('Discard all local review changes in this browser?')) {
    localStorage.removeItem(STORAGE_KEY);
    assignments = {}; blocked = {}; touched = new Set(); exemptions = {}; reclassify = {}; cleared = {}; locks = {};
    excluded = new Set(__EXCLUDED__);
    focusFilters = new Set(); hiddenFilters = new Set(); blockedFilter = ''; provFilters = new Set(); sortKey = null; sortDir = 1;
    renderBanner(); renderChips(); renderHead(); renderBody(); renderStats();
  }
};
const THEME_KEY = 'ytis_channel_review_theme';
function applyTheme(dark) {
  document.body.classList.toggle('dark', dark);
  document.getElementById('theme').textContent = dark ? '☀️' : '🌙';
}
applyTheme(
  localStorage.getItem(THEME_KEY) === 'dark'
  || (localStorage.getItem(THEME_KEY) === null
      && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
);
document.getElementById('builtat').textContent = 'data as of ' + BUILT_AT;
document.getElementById('refresh').onclick = () => location.reload();
document.getElementById('theme').onclick = () => {
  const dark = !document.body.classList.contains('dark');
  localStorage.setItem(THEME_KEY, dark ? 'dark' : 'day');
  applyTheme(dark);
};
const savedW = parseFloat(localStorage.getItem('ytis_channel_review_namew'));
if (!isNaN(savedW)) {
  document.documentElement.style.setProperty(
    '--name-w', Math.min(900, Math.max(120, savedW)) + 'px');
}
loadLocal();
renderBanner(); renderChips(); renderHead(); renderBody(); renderStats();
</script>
</body>
</html>
"""


def build_page(db_path: Path, output: Path, excluded: list[str]) -> dict[str, object]:
    from csf.batch_status import _BatchStatusStorage

    _BatchStatusStorage(db_path=db_path)  # runs column migrations first
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT channel_url, channel_title, description, category, "
        "subscriber_count, video_count_estimate, shorts_count, playlists_count, "
        "channel_status "
        "FROM channel_metadata"
    ).fetchall()
    blocked = {
        r[0]
        for r in conn.execute("SELECT channel_url FROM channel_blocklist").fetchall()
    }
    exempt = {
        r[0]
        for r in conn.execute(
            "SELECT channel_url FROM channel_metadata WHERE exempt_from_exclusion = 1"
        ).fetchall()
    }
    locked = {
        r[0]
        for r in conn.execute(
            "SELECT channel_url FROM channel_metadata WHERE category_source = 'manual'"
        ).fetchall()
    }
    conn.close()
    data = [
        {
            "u": r[0],
            "t": r[1] or "",
            "d": (r[2] or "")[:300],
            "c": r[3] or "",
            "s": r[4],
            "v": r[5],
            "sh": r[6],
            "pl": r[7],
            "dead": r[8],
        }
        for r in rows
    ]
    doc = _TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    doc = doc.replace("__CATS__", json.dumps(ALL_CATEGORIES))
    doc = doc.replace("__EXCLUDED__", json.dumps(excluded))
    doc = doc.replace("__BLOCKED__", json.dumps(sorted(blocked)))
    doc = doc.replace("__EXEMPT__", json.dumps(sorted(exempt)))
    doc = doc.replace("__LOCKED__", json.dumps(sorted(locked)))
    doc = doc.replace("__BUILT_AT__", json.dumps(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")))
    filter_options = "".join(
        f'<option value="{html.escape(c)}">{html.escape(c)}</option>' for c in ALL_CATEGORIES
    )
    doc = doc.replace("__FILTER_OPTIONS__", filter_options)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(doc, encoding="utf-8")
    stats = {
        "channels": len(data),
        "with_category": sum(1 for d in data if d["c"]),
        "other": sum(1 for d in data if d["c"] == OTHER_CATEGORY),
        "already_blocked": len(blocked),
        "page": str(output),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "excluded_categories": excluded,
    }
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output HTML (default: .logs/channel_review/review.html)",
    )
    parser.add_argument(
        "--excluded", default="",
        help="Comma-separated categories to pre-mark excluded (from discovery settings)",
    )
    parser.add_argument(
        "--open", action="store_true",
        help="Open the page in the default browser after building.",
    )
    args = parser.parse_args(argv)

    db_path = args.db_path if args.db_path is not None else get_batch_db_path()
    output = args.output if args.output is not None else get_ytis_log_root() / "channel_review" / "review.html"
    excluded = [c.strip() for c in args.excluded.split(",") if c.strip()]
    unknown = [c for c in excluded if c not in CATEGORIES]
    if unknown:
        print(f"error: unknown categories {unknown}; valid: {CATEGORIES}", file=sys.stderr)
        return 2
    stats = build_page(db_path, output, excluded)
    print(json.dumps(stats, indent=2, sort_keys=True))
    if args.open:
        import subprocess

        try:
            subprocess.run(
                ["cmd", "/c", "start", "", str(output.resolve())], check=False,
                capture_output=True,
            )
        except OSError:
            pass  # showing the page is best-effort; building it is the contract
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
