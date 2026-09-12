# Lane C findings — RRE v1 amendment 2: sidecar attribution, HMAC secret lifecycle, malformed-input fail-closure

Frozen tree reviewed: `P:/tmp/rre-am2-review/tree/packages/yt-is/`
Module: `ef/eval_recommendation_regret.py` (rre_v3_sidecar_binding_3), CLI `scripts/eval_recommendation_regret.py`.
Method: independent probe scripts (all saved in this directory), executed with Python 3.14.0, stdlib only, synthetic fixtures, no network, no git, no writes to the tree (bytecode writing disabled; tree pyc hashes verified unchanged before/after). Raw outputs: `probe_ab_results.json`, `probe_c_results.json`, `probe_cli_results.json`, `probe_e_results.json`, `probe_d_results.txt`, `matrix_table.md`.

## Verdict per section

| Section | Verdict | One-line basis |
|---|---|---|
| A. Unbind attribution safety | PASS | 37/37 probe rows pass: stored map never trusted, re-derivation + HMAC enforced, same path pairwise/listwise |
| B. Prior falsifier reproduction | PASS | 5/5 pairwise and 5/5 listwise tampered attributions fail closed with `PacketBindError`; renamed-arms forgery refused on the MAC; 5/5 same-layout wrong sidecars refused |
| C. HMAC / secret lifecycle | PASS (with observations O1-O2) | 42/42 module rows + 20/20 CLI check rows pass (plus 2 OBS rows: orphan sidecar, Windows ACL); secret never in packet/sidecar/stdout/argv/prompt; no regeneration at unbind; rotation semantics as claimed |
| D. Threat-model honesty | reported (F-C1, F-C2, F-C3) | item_ids and judgments are outside mechanical protection (by design); see below |
| E. Fail-closure matrix | FAIL | 125 rows: 103 PASS, **11 traceback rows**, 11 silent-accept rows |

Per the lane's stated FAIL rule ("any matrix row tracebacks"), the overall verdict is FAIL. The attribution/integrity core (A/B/C) is sound; the failures are input-hygiene fail-closure gaps (crash instead of controlled refusal) plus a few silent-acceptance paths, itemized below. No wrong-but-accepted attribution was found anywhere.

## A. Attribution safety (verified by execution)

- Honest round trip on 5 pairwise packets: judge slot outcome for the baseline arm unbinds to `preferred_arm == baseline` (5/5).
- Naive tamper (flip stored `reverse_map` only), flip `slots` only, flip both, and flip-both-plus-RECOMPUTED-unkeyed-digest: all refused with `PacketBindError` ("sidecar slot map does not match the re-derived frozen assignment"). The stored map is never used before it is compared to the re-derivation `canonical_binding(packet_id, sidecar_arms, schema)` (same frozen `assign_slots` + `BLIND_SALT`; listwise salts packet_id with `|listwise` and rewrites tokens to `LIST_*`).
- Packet identity: one-byte packet content change -> digest mismatch; sidecar `packet_id` != packet `packet_id` -> refusal. Note on wording: the digest is over the canonical JSON form (`canonical_doc_hash`, sorted keys/compact), not literal file bytes; every value change changes it, only key-order/whitespace-invariant re-serializations do not. Not a defect; the claim's "exact packet bytes" should read "canonical packet content".
- Arm discipline on the sidecar path: 1 arm, 3 arms, duplicates, non-string, whitespace-only, unhashable list entry all refused (the `all(isinstance(a, str) ...)` guard short-circuits before `set(arms)`, so no `TypeError` leaks).
- Constant-time compare: `hmac.compare_digest` with an `isascii()` guard; non-ASCII MAC string, empty, None, int, list, wrong-length, bit-flipped, uppercase variants all refused with controlled `PacketBindError` (no `TypeError` from `compare_digest`).
- Same code path pairwise and listwise: `unbind_result` and `unbind_list_result` both gate on the identical `check_sidecar_binding`.

## B. Prior falsifier MUST fail closed — reproduced as failure

- Pairwise: 5 packets, judge genuinely prefers the BASELINE arm (judgment = baseline's slot token; honest unbind verified `preferred_arm == "A"` first). Sidecar `reverse_map` tampered so attribution would invert, with the attacker-computable unkeyed digest ALSO recomputed. Result: **5/5 refused with `PacketBindError`**, 0 wrong-but-accepted records.
- Listwise: same construction with `LIST_*` tokens. **5/5 refused**.
- Same-layout wrong sidecar (sidecar from a different packet with the same arm names A/B), tried under both the wrong and the right secret: **5/5 refused** (packet_id equality, digest, and MAC all bind).
- Renamed-arms forgery (A/B -> W/Z chosen so the frozen re-derivation itself places W in the judge's preferred slot; `slots`/`reverse_map` re-derived, unkeyed digest recomputed — everything the secret-less forger can compute): passes re-derivation and digest, **refused exactly on the MAC** ("sidecar binding MAC verification failed"). Same via `unbind_result`.

## C. HMAC / secret lifecycle (verified by execution)

GENERATION
- `new_binding_secret()`: 64 hex chars, all-distinct across 200 draws (`secrets.token_hex(32)`, 256 bits).
- `build_blinded_packet` generates the secret internally (call-count probe: exactly 1 `new_binding_secret()` per blind when none supplied).
- CLI `cmd_blind` never passes `binding_secret=` into build; an arms-file carrying a top-level `binding_secret` field and items carrying `binding_secret`/`salt` fields is IGNORED — a fresh secret is generated, and the injected secret fails the MAC on the produced packet (while the generated secret verifies).

STORAGE
- Secret hex absent from the blinded-packet JSON and from the sidecar JSON; no `secret`/`salt`/`mac`/`hmac`/`key`-named key anywhere in either (normalized recursive key scan); sidecar top-level keys are exactly `packet_id, arms, slots, reverse_map, blinded_packet_sha256, binding_version, binding_mac, item_ids`.
- CLI stdout prints the blinded packet and the secret's PATH (`binding_secret_written_to`), never the value; secret absent from stdout and stderr (captured via subprocess).
- Refusals: no `--sidecar-out` -> exit 2 "refusing to inline sidecar without --sidecar-out" (and the secret file is NOT written — sidecar refusal precedes it); `--sidecar-out` without `--secret-out` -> exit 2 "refusing to inline binding secret". O1 (observation, not gated): with `--sidecar-out` but no `--secret-out`, the sidecar file is already written when the refusal fires — an orphan sidecar with no usable secret remains on disk (harmless: unusable without the secret).
- O2 (Windows ACL observation): `--secret-out` uses plain `write_text`; the file lands with inherited ACLs — `BUILTIN\Administrators:(I)(F)`, `NT AUTHORITY\SYSTEM:(I)(F)`, `NT AUTHORITY\Authenticated Users:(I)(M)`, `BUILTIN\Users:(I)(RX)`. Any local authenticated user can read (and modify) the HMAC key file. No restrictive mode is attempted. Observation per instructions, not scored as a defect; on a shared host this materially weakens "out of band".
- No argv flag accepts a secret VALUE: the only secret-related flag is `--secret-out` (a path); argparse surface audited (`blind --help` flags + `inspect.getsource(cmd_blind)`).
- Judge path: with an in-process simulated transport, the rendered pairwise prompt contains no secret, no arm names, no sidecar map; judge refuses without `--transport` (exit 3); an invalid packet (planted `secret_score` machinery key) is refused at exit 5 with the transport NEVER invoked (validate-before-render holds).

USE (each MAC input load-bearing)
- Packet content: tamper + recomputed digest -> MAC refuses (digest alone would pass).
- Arms set: rename forgery with re-derived map + recomputed digest -> MAC refuses.
- reverse_map: function-level MAC changes under map flip (`_binding_mac` differs); behaviorally any non-re-derived map refuses first at re-derivation.
- binding_version: flip refused at the version gate ("sidecar binding_version ... unknown"); the version string is inside the MAC blob (independent manual HMAC reproduces the module's MAC byte-for-byte).
- Digest load-bearing separately: one-byte packet change with intact MAC -> "blinded_packet binding digest mismatch".
- MAC corruption set (empty, None, int, non-ASCII, wrong length, bit-flip hex, uppercase, list): all controlled `PacketBindError`.

RESUME
- Secret variants at unbind: `None`, `""`, `b""`, `"  "`, `"abcd"`, 15-byte hex, non-hex `zz*64`, wrong-but-valid 256-bit secret: all `PacketBindError` (hex/length normalization errors are converted, never `ValueError`/`TypeError` leaks). Boundary: exactly 16 bytes passes normalization per the documented ">= 16 bytes" and then fails the MAC with the wrong value.
- No regeneration/fallback: `new_binding_secret` monkeypatched to a tripwire — valid unbind still succeeds, tampered and missing-secret unbinds still refuse, tripwire never fires (0 calls). No unbind code path creates a secret.

ROTATION
- Re-blinding the same packet_id with identical content yields a NEW secret; the OLD secret fails on the NEW sidecar and the NEW secret fails on the OLD sidecar (MAC mismatch both ways); each (sidecar, secret) pair remains internally verifiable (nothing revokes the old pair — but the old pair only ever attests the identical packet content, so no attribution divergence is possible). Same packet_id with DIFFERENT content under the old sidecar fails the digest.
- No mid-evaluation swap path: `aggregate_pair_results`/`evaluate` expose no secret parameter (signature introspection); every unbind re-verifies the MAC for its own sidecar, so substituting another secret mid-run can only produce refusals.

## D. Threat-model honesty (report exactly, no fixes)

- F-C1 (d1, item_ids): tampering ONLY the sidecar's `item_ids` (judge_id -> real_id map) passes `check_sidecar_binding` and unbind — the MAC/digest cover packet+arms+reverse_map, not `item_ids`. No function in the module reads `item_ids` (only the write in `_bound_sidecar`), so nothing in-module can be silently corrupted; but any downstream tool or human using the sidecar to recover real ids gets a silently corrupted map. Minimal repro: swap `item_ids[arm]["real_id"]` between the two arms -> binding check returns OK.
- F-C2 (d2, judgments): judgments are not MAC-covered. Swapping the judge's slot outcome post-judging (ITEM_1 -> ITEM_2) before `unbind_result` silently inverts `preferred_arm` (verified: honest "A" became "B" with no mechanical violation); editing `preferred_arm` in a pair record before `aggregate_pair_results` silently inverts the counts (A:1/B:0 -> A:0/B:1); fabricated `dims_ratings` and `transport_meta` pass through unauthenticated. What the protocol actually enforces mechanically: slot-map/packet/arm binding (HMAC+re-derivation) and outcome VOCABULARY at parse and unbind; what it assumes: that the judgments dict handed to unbind is the one the judge produced. There is no transcript-to-judgment binding (the CLI prints raw judge output alongside the parse, an audit artifact, not a mechanical check).
- F-C3 (d3, adversaries): The HMAC + re-derivation protects against a file-writer WITHOUT the secret: such an adversary cannot forge a sidecar that attributes a judge's slot outcome to a chosen arm (map flip, rename, cross-packet transplant all refuse), and cannot tamper the packet without the digest/MAC catching it — this holds for the blinded packet and the sidecar's arm/slot map. It does NOT protect against the curator WITH the secret (they can re-seal anything, including a fully forged sidecar — the design's answer is that the secret is generated blind-time and held out of band, but a curator who holds it can re-forge; only process discipline bounds this), nor does it protect judgments, `item_ids`, `dims_ratings`, or `transport_meta`, which carry no authentication anywhere. The candidate producer sees only blinded packets (allowlist + opaque ids) and has no channel into the binding; they are not an attribution threat unless they are also the sidecar file-writer, which the MAC already answers.

## E. Fail-closure matrix

125 rows executed (full table: `matrix_table.md`, raw JSON `probe_e_results.json`). Result: 103 PASS, 11 traceback FAILs, 11 silent-accept FAILs. Highlights by spec row:

Spec-listed rows that PASS include: malformed/missing packet_id (all 5 variants), arms 1/3, item record not a dict, title/claims/why_surfaced non-string (int/dict/list), evidence_refs non-list (str/dict/int), related_records scalar + entry non-dict + extra keys + kind/id non-string/empty, non-string recent_surfaced_items, listwise k=0 and empty arms, all E.2 validate rows except view-as-scalar, all E.3 binding rows except blinded_packet-as-list (extra), all E.4 judgment rows except judgments-as-list (extra), listwise unknown outcome / missing side / foreign mark id, aggregate missing/unknown judgment, conflicting duplicate packet_id (PBE), exact-duplicate dedup receipt (1 receipt, 1 counted), provisional exclusion counted, three-arm record with pref outside the record's arms (PBE), evidence-class invalid (PBE).

Traceback rows (verdict-critical findings):

| # | Case (spec row) | Actual exception |
|---|---|---|
| F1 | E.1 arms passed as list (duplicate-arm list path) | `AttributeError: 'list' object has no attribute 'keys'` |
| F2 | E.1 non-string arm id (int arm key) | `TypeError: '<' not supported between instances of 'str' and 'int'` (from `sorted` in `assign_slots`) |
| F3 | E.1 listwise k str/float | `TypeError: slice indices must be integers...` |
| F4 | E.2 view as scalar | `AttributeError: 'str' object has no attribute 'get'` at line 1112 (`(it or {}).get("item_id")` after the controlled error was already appended) |
| F5 | E.5 marks value int/None | `TypeError: 'int'/'NoneType' object is not iterable` |
| F6 | extra: arm value not a list (pairwise `len(recs)`, listwise `[:k]`) | `TypeError: object of type 'int' has no len()` / `'int' object is not subscriptable` |
| F7 | extra: `check_sidecar_binding` with blinded_packet as list | `AttributeError: 'list' object has no attribute 'get'` |
| F8 | extra: `unbind_result` judgments as list | `AttributeError: 'list' object has no attribute 'get'` |

CLI-reachable variants of the same class (subprocess-verified, `cmd_blind`): arms-file with `k: "abc"` -> `ValueError` traceback exit 1; `mode: 5` -> `TypeError` traceback; `arms` key missing -> `KeyError` traceback; arm entry missing `items` -> `KeyError` traceback. Controlled at CLI: packet_id int and duplicate arm names both refuse with exit 2 and a `PacketBindError` message. F9 (silent): arms-file `k: 1.5` is silently truncated by `int()` to k=1 and builds successfully.

Silent-acceptance rows (not tracebacks; fail-closure spec expected `PacketBindError`):

| # | Case | Actual |
|---|---|---|
| F10 | title/claims/why_surfaced = None at build | field silently omitted (`item.get(k)` None-skip); claims/why absence later downgrades provenance (provisional), title absence has no consequence anywhere |
| F11 | evidence_refs entries: int, None, nested list, dict | accepted at build AND at `validate_packet` for non-machinery entries (a dict with a `score` key IS caught by validate's machinery scan, exit-5 at judge time); the docstring's "curator-supplied strings under documented curator rules" is honest but unenforced — this remains the one judge-visible field with no type check on entries |
| F12 | listwise k = -1 | accepted; slice semantics drop the last item and `"k": -1` is frozen into the packet |
| F13 | mixed-arm record set (chal B vs C across records) | aggregated without error; per-record arm vocabulary only |
| F14 | evaluate challenger_arm == baseline_arm | no check; report emitted (header arms A/A; counts still come from each record's own arms; a record with chal==base credits the challenger counter first) |
| F15 | judgments_evidence_class missing | `TypeError: evaluate() missing 1 required keyword-only argument` — signature-level refusal, fails closed (no report emitted), but is a raw TypeError rather than `PacketBindError` |

## Summary judgment

The security-critical claims hold under adversarial execution: the falsifier fails closed 5/5 + 5/5, every secret-less forgery path is refused on the MAC, the secret is never leaked on any CLI/file/prompt surface, and unbind never regenerates or falls back. The defects are concentrated in malformed-input hygiene: five spec-listed matrix cases (F1-F5) crash with raw `AttributeError`/`TypeError` instead of `PacketBindError` — several of these are reachable through the CLI's arms file (k/mode/arms-shape), the rest through direct module calls — and six input shapes are silently accepted where the fail-closure spec required refusal (F10-F14, plus CLI k-truncation F9). None of the tracebacks or silent accepts enable a wrong-but-accepted attribution; they are availability/clean-refusal gaps, not integrity breaks.

LANE_C_VERDICT: FAIL
