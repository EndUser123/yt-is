# Lane B findings — R3 structural blinding + opaque IDs (adversarial review)

Frozen tree under review (read + import only):
- `P:/tmp/rre-am2-review/tree/packages/yt-is/ef/eval_recommendation_regret.py`
  sha256 `195ab347f91ad6409ef0b6b5e33f4e4a0d23a34c055657f033e3b0cab1f90cb2`
- `P:/tmp/rre-am2-review/tree/packages/yt-is/scripts/eval_recommendation_regret.py`
  sha256 `ff9a24bc3b8a932ed2e7f1206c5a8661910c474cfa16ceb2a03ed1566709e4fd`

Method: every claim tested by execution against the imported module.
Probe scripts and raw JSON outputs in this directory:
`_rre_lane_common.py`, `probe_g1_matrix.py` (+`g1_results.json`),
`probe_g2_residual.py` (+`g2_results.json`), `probe_g3_build_render.py`
(+`g3_results.json`), `probe_g4_opaque.py` (+`g4_results.json`),
`probe_g5_structural.py` (+`g5_results.json`). The render path is a
byte-faithful replication of `cmd_judge` lines 130-151 (validate first,
then render `json.dumps` of the views), so "renders" below means
"reaches the exact prompt the CLI judge would send".

## Group verdicts

| Group | Verdict | One-line result |
|---|---|---|
| G1 variant matrix | PARTIAL FAIL | 15,776 trials; every non-homoglyph class caught at all 8 positions; 54/15,776 leaks, ALL homoglyph spellings at the two evidence_refs-entry positions |
| G2 documented residual | FAIL (claim false as worded) | wrapper homoglyphs pass validation but never render (documented, honest); item-view keys and related_records entries block every spelling; evidence_refs ENTRY keys do not — the load-bearing claim is false for that container |
| G3 build/render integrity | PARTIAL FAIL | six-field allowlist and questions/instruction non-render hold; evidence_refs entries pass through build verbatim with no entry-type check |
| G4 opaque IDs | PASS (2 notes) | all executed invariants hold; whitespace-only source accepted; public constant salt makes token prefixes producer-grindable |
| G5 primary protection | FAIL | structural watermarks (plain-spelled keys, homoglyph machinery keys, arm-fingerprint shapes, typed numeric values) reach the rendered judge prompt from validation-passing packets, including through the official build path |

## G1 matrix summary

18 concepts x ~109 variants x 8 positions = 15,776 trials. Variant
classes: plain, UPPER, Title, 5 separator forms, fullwidth lower/upper,
16 invisible chars x 3 positions, 16 variation selectors x 3 positions,
small-cap, mathematical bold, homoglyph full/first-letter.

Position outcomes (totals over all concepts/variants):

| Position | CAUGHT | LEAK (ok+renders) | PASS_NORENDER | CRASH |
|---|---|---|---|---|
| P1 item view top level | 1972 | 0 | 0 | 0 |
| P2 evidence_refs entry (dict) | 1945 | 27 | 0 | 0 |
| P3 evidence_refs entry (list-of-dict) | 1945 | 27 | 0 | 0 |
| P4 related_records entry | 1972 | 0 | 0 | 0 |
| P5 listwise list entry | 1972 | 0 | 0 | 0 |
| P6 key wrapping the doc | 1972 | 0 | 0 | 0 |
| P7 dict inside recent_surfaced_items | 1945 | 0 | 0 | 27 |
| P8 top-level sibling of blinded_packet | 1945 | 0 | 27 | 0 |

All 54 leaks are homoglyph spellings (`homoglyph_full` 36,
`homoglyph_first` 18); every leaked key had
`is_machinery_class == False` after normalization, i.e. the normalized
detector is the only thing standing between those keys and the prompt,
and at evidence_refs-entry positions nothing else stands behind it.
Caught classes confirm the claim-2 normalization pipeline exactly:
NFKC folds fullwidth and mathematical bold; the small-cap table folds
small capitals; the invisible set strips all 16 chars and all 16
variation selectors at start/middle/end; separators `-_. ` and U+3000
fold; casefold catches UPPER/Title. Full per-concept table in
`g1_results.json`. Per concept the leak count was 2-4 (only the
homoglyph variants whose normalized form escapes both the equivalence
set and every substring).

## Findings

### F-B1 (CRITICAL for the R3 claim): evidence_refs entries are an unconstrained pass-through container inside the judge view

`build_judge_item_view` type-checks evidence_refs only as a list;
entries pass through verbatim (`view[k] = v`). `validate_packet` also
checks only list-ness — no entry schema, no entry type check. The
recursive machinery-key scan is the ONLY defense there, and it is a
normalized blacklist: any key that does not normalize to a machinery
equivalence or substring passes, and homoglyph machinery spellings pass
by design (documented residual — but the doc scopes that residual to
wrapper keys, which never render; these render).

Executed end-to-end through the official build path
(`build_blinded_packet` -> `validate_packet` -> cmd_judge render sim):

1. Plain-spelled structural watermark (no homoglyphs needed, keys
   `slot`, `position`, `rating`, `reverse_map` all classify
   non-machinery): packet validates ok=True and the rendered prompt
   contains `"slot": 1`, `"rating": 0.97`,
   `"reverse_map": {"ITEM_1": "arm-B"}` — arm identity, rank-like and
   score-like TYPED values in structured fields.
2. Homoglyph machinery keys: refs `[{"sc\u043ere": 0.91,
   "\u0430rm": "challenger", "r\u0430nk": 1}]` (Cyrillic o/a/a)
   validate ok=True and render with key and numeric value intact.
3. Arm-correlated structural fingerprint: arm A items with string refs
   vs arm B items with dict refs `{"v": 2}` — both validate, the judge
   sees a reliable structural tell distinguishing the arms
   (`"v": 2` in prompt, types str vs dict).

This falsifies the amendment's load-bearing sentence "inside
judge-visible containers the allowlist + positive schema catch any
unknown key regardless of spelling": evidence_refs entries are inside a
judge-visible container (they are json.dumps'd into the prompt) and
unknown keys there are caught only when machinery-normalizing. It also
violates R3's "no pass-through projection of arbitrary source
dictionaries" at entry granularity.

Mitigation observed (does not restore the claim): any dict/list
evidence_refs entry makes the item provenance-UNRESOLVED, and
`aggregate_pair_results` then counts the record `provisional_excluded`
with `n_decided_strict_conformance == 0` — a producer watermarking via
dict refs self-excludes from gate counts. But the judge prompt exposure
happens before exclusion, the mitigation requires the provenance
machinery to be run with an inventory (see F-B3: that path crashes),
and the fingerprint channel (item 3) degrades the judge's blindness for
every judged packet.

Repro: `cd /p/tmp/rre-am2-review/lane-b && python probe_g5_structural.py`
(keys E1/E2/E3) and `python probe_g2_residual.py` (27/27 homoglyph keys
that pass validation at `evref_entry_key` all render). Minimal repro:

```python
import sys; sys.path.insert(0, r'P:/tmp/rre-am2-review/tree/packages/yt-is')
from ef import eval_recommendation_regret as rre
item = {"item_id": "x", "title": "t", "claims": "c", "why_surfaced": "w",
        "evidence_refs": [{"sc\u043ere": 0.91}],  # Cyrillic o
        "related_records": [], "marker_synthetic": True}
pkg = rre.build_blinded_packet("p", {"A": [item], "B": [dict(item, item_id="y")]})
val = rre.validate_packet({"blinded_packet": pkg["blinded_packet"]}, None)
print(val["ok"])   # True
import json
print("sc\u043ere" in json.dumps(pkg["blinded_packet"]["items"], ensure_ascii=False))  # True
```

### F-B2 (MAJOR): validation does not enforce opaque-token format on id fields; a substituted packet renders producer-chosen ids with FULL VALID provenance

`validate_packet` requires `item_id` to be a string, nothing more. A
hand-crafted packet (not produced by `build_blinded_packet`, which does
replace ids) with `"item_id": "arm-B_rank-1_score-0.97"` validates
ok=True with `provenance_states {"ITEM_1": "VALID", "ITEM_2": "VALID"}`
(inventory supplied) and the string renders into the judge prompt.
Same for `recent_surfaced_items`: arbitrary strings
(`"ARM A loses"`, `"policy goal_aware_v2 score 0.9 rank 1"`) validate
and render verbatim into `RECENT_ITEMS:`. The "producer IDs never
render" guarantee is build-time only; the CLI judge's only gate
(`cmd_judge` -> `validate_packet`) does not enforce it, so whoever
controls the packet file controls these rendered strings with zero
eligibility cost. This is the id channel, distinct from prose fields
only in that the amendment explicitly claims it as evaluator-owned.
Repro: `python probe_g5_structural.py` (keys E4/E5).

### F-B3 (MINOR, robustness contract violation): uncaught TypeError tracebacks

Two paths crash with raw tracebacks instead of controlled
`PacketBindError`/validation errors, contradicting the amendment's
"controlled type errors ... are validation errors, never tracebacks":

1. `validate_item_provenance` line 970 `r not in evidence_inventory`
   raises `TypeError: cannot use 'dict' as a dict key` when
   evidence_refs contains an unhashable entry (dict/list) and an
   inventory is supplied. This is reachable from `cmd_judge
   --evidence-inventory` and from `make_demo_pair_record`: the packet
   is structurally valid, then the CLI tracebacks instead of exiting
   with a validation error. Repro in `probe_g5_structural.py`
   (`E1b0_inventory_crash`).
2. A dict inside `recent_surfaced_items` passes validation, then
   `"; ".join(recent_items)` in the render path raises TypeError
   (27 G1 trials). Fails closed (no prompt sent) but by traceback.

### F-B4 (INFO): whitespace-only opaque-id source is accepted

`_opaque_id` raises for int/float/None/dict/list/bool/empty-string, but
a whitespace-only string `"   "` returns a token (no `.strip()` check,
unlike `_require_packet_id`). The amendment claim ("fail closed on
non-string sources") holds as worded — whitespace is a string — but the
review brief's expectation (whitespace-only must raise) does not hold.
Repro: `python probe_g4_opaque.py` (`e_nonstring_sources`,
`e_build_item_id_types`).

### F-B5 (INFO): opaque tokens are producer-computable; prefix grinding demonstrated

`BLIND_SALT` is a public module constant, so anyone who knows the
packet_id (and the frozen file) can compute all tokens offline and GRIND
real-id strings until tokens carry a chosen prefix: prefix "ab" found in
157 tries (`grind_156 -> RRE_J_abda40f9f6a0e921`); cost is ~16^k for k
hex chars. This is inside the claim's "beyond choosing the real id
string" carve-out — grinding IS choosing the id string — but it means a
producer who knows packet_id can watermark their items' token
appearances for a colluding judge at trivial cost. Per-packet secret
salting would close it. Repro: `python probe_g4_opaque.py` (`h_prefix_grind`).

### F-B6 (INFO, doc gap): related_records `kind` is a provenance-VALID value watermark through the official build path

`kind` is producer free text, copied verbatim at build, unconstrained
(no closed vocabulary), rendered to the judge, and provenance ignores
related_records — so `{"kind": "goal_armB_batch7", "id": "g_1"}` keeps
`provenance VALID` (unlike F-B1's dict refs, which self-exclude). The
amendment's curator free-text rules enumerate title/claims/why_surfaced/
evidence_refs but not related_records.kind. Repro:
`python probe_g5_structural.py` (`E6_related_kind_value_channel`).

## Claims vs execution (wording precision)

- Claim 1 (allowlist-built view): TRUE as worded — build copies exactly
  the six fields (verified: source items carrying `arm`, `score`,
  `rank`, `policy`, `similarity_diagnostic`, nested dicts produce views
  with exactly the six allowlisted keys; `machinery_in_views_json`
  false). related_records entries reduce to `{"kind","id"}` with opaque
  ids; unknown entry keys fail closed at build AND validation for every
  spelling tested (46/46). The wording obscures that evidence_refs
  ENTRIES are pass-through (F-B1); the claim's own words never promised
  otherwise.
- Claim 2 (normalized classification): TRUE as worded and verified
  exhaustively — normalization pipeline and classification match the
  code and catch every non-homoglyph class at every position. The
  exemptions list only guards the substring check (exact equivalences
  fire regardless); no overlap exists between the two sets, so this
  subtlety is currently inert.
- Claim 3 (validate before render): TRUE — verified by construction of
  cmd_judge (validate at lines 130-136 precedes every render call) and
  by the render sim: across all probes, zero validation-failing packets
  rendered. The P7 crash class (F-B3.2) fails AFTER validation, by
  traceback, still before any judge invocation.
- Claim 4 (opaque IDs): TRUE on every executed property — same real id
  under both arms yields the identical token (duplicate-across-arms
  detected, `pair_kind` = DUPLICATE_ACROSS_ARMS; distinct ids NORMAL);
  listwise position-independent (pos 0 == pos 4); packet-scoped
  (different packet_id -> different token); 200,000 distinct ids -> 0
  collisions (birthday expectation 1.1e-9); token independent of arm
  names, item order, and source scores; byte-sensitive (ids that
  normalize identically — case/fullwidth/ZWSP variants — yield distinct
  tokens, so id-collision tricks cannot merge items). Deviations:
  F-B4 (whitespace-only accepted), F-B5 (public-salt grindability).

## Verdict rationale

The opaque-ID machinery and the six-field allowlist do what the
amendment says. The primary-protection claim fails in scope:
evidence_refs entries form an unconstrained, rendered, producer-
controlled structural channel through which arm identity, rank/score-
like typed values, and arm-fingerprint shapes reach the judge prompt
from packets that pass `validate_packet` — including packets produced
by the official build path (F-B1) — and the id channel is unenforced at
the validation seam with full provenance validity (F-B2). That meets
the FAIL criterion "any structural leak reaches a validated packet or
rendered prompt".

LANE_B_VERDICT: FAIL
