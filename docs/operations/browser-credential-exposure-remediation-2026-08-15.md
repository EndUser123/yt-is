# Browser credential exposure remediation runbook

```
agent: zcode
host: both
date: 2026-08-15
severity: critical
status: partially applied (tracking removed); operator actions pending
```

## Finding

15,095 files under `.browser/notebooklm/` (a real Chromium user-data directory)
were tracked in git and pushed to `origin` (`github.com/EndUser123/yt-is.git`;
`main` == `origin/main` at review time). The tracked set includes
credential-bearing artifacts, with non-empty content confirmed at review time:

- `.browser/notebooklm/Default/Network/Cookies` (36,864 bytes)
- `.browser/notebooklm/Default/Login Data` (131,072 bytes)
- `.browser/notebooklm/Default/Network/Trust Tokens` (40,960 bytes)
- `.browser/notebooklm/Default/Session Storage/*.ldb`
- `.browser/notebooklm/Default/Extension Cookies`

Treat every Google/NotebookLM session embodied in that profile as disclosed.

## Applied in this repo (agent: zcode, 2026-08-15)

- `git rm -r --cached .browser` — removed from the index only; the working
  directory is untouched so any runtime use of the profile continues to work.
- `git rm --cached csf/yt_is_data.db` — 0-byte tracked DB, contrary to the
  existing `*.sqlite`/local-only ignore policy.
- `.gitignore`: added `.browser/`.

These steps stop NEW exposures. They do not remove anything from history.

## Triage results (agent: zcode, 2026-08-15)

- The exposed profile dates from commit `c720775` (2026-04-23). It no longer
  existed in the live checkout when remediation began; copies remained in git
  history, on `origin`, and in the testing worktree.
- Cookie triage (read-only, no values decrypted): 43 Google-family cookies;
  **30 SID-family session cookies whose expiry runs to 2027-05-28** (SID,
  HSID, SSID, SAPISID, `__Secure-1PSID/3PSID` and variants) for
  `google.com`, `google.ca`, and `notebooklm.google.com`. They do not age
  out on their own.
- The profile is a **multi-account** profile: an `AccountChooser` URL carries
  `Email=troup.hominidae...` and visits use `authuser=0` and `authuser=2`,
  so up to all three canonical identities may be embodied in it.
- Mitigating factor, not a pardon: Chromium encrypts cookie values with
  DPAPI bound to the committing Windows account, so a remote cloner cannot
  decrypt them without that account's credentials. Treat as exposed anyway.
- Canonical identities are unaffected and independent: the sanctioned
  keepalive (`python -m csf.nlm_keepalive`) passed token-only repair/probe
  for `a.hominidae`, `troup.hominidae`, and `brsthomson` (exit 0, backups
  pushed, 2026-08-15).
- Local plaintext copies destroyed: `.browser/` removed from the testing
  worktree. The live checkout had none. Remaining copies exist only in git
  history and on the remote until the operator actions below.

## Operator actions required (cannot be done by an agent safely)

1. **Sign out the exposed sessions from the Google side.** For each of the
   three identities (`a.hominidae@gmail.com`, `troup.hominidae@gmail.com`,
   `brsthomson@hotmail.com` — at minimum `troup.hominidae`, confirmed in the
   profile): sign in, open Google Account → Security → "Your devices" /
   "Sign out of all sessions" (or change password, which invalidates all
   sessions). This is the only mechanism that revokes the 2027-expiry SID
   cookies; deleting local files cannot.
2. After sign-out, re-verify each identity with
   `python -m csf.nlm_keepalive` (token-only; repair from durable master
   token if a session was invalidated). If a master token itself fails,
   use the one-time `bin/csf-nlm-auth --profile <exact-profile>` bootstrap.
3. **Decide on history scrubbing.** Removing the blobs from history requires
   `git filter-repo` (or BFG) on a fresh clone plus `git push --force` to
   `origin`, coordinated with anyone holding clones. This rewrites every
   commit SHA; the local worktrees under `P:/.worktrees/` will need to be
   re-created afterwards. If the repository is treated as disposable/private
   and steps 1-2 are complete, the operator may instead accept the
   history as tainted-but-dead.
4. **Check other copies.** Any clone, bundle, or backup of this repo made
   between 2026-04-23 and 2026-08-15 contains the same credential material.

## Status after operator sign-out (2026-08-15) — RESOLVED

Operator signed out all Google-side sessions and completed the one-time
`troup.hominidae` bootstrap. Final keepalive (2026-08-15 07:21): all three
identities pass token-only repair/probe, exit 0, backups pushed for
`a.hominidae`, `troup.hominidae`, and `brsthomson`. Every session now in
use was minted after the sign-out; none derives from the exposed profile.

**Remaining open item (optional, operator decision):** the 2026-04-23
cookie blobs still exist in git history and on `origin` until a
`git filter-repo` + force-push scrub is done (step 3 below). With the
sessions revoked Google-side and DPAPI encryption as a second barrier,
this is defense-in-depth, no longer active exposure.

Operator signed out all Google-side sessions. Follow-up keepalive:

- `a.hominidae`: token-only repair/probe passed — fresh session minted
  from its durable master token.
- `brsthomson`: token-only repair/probe passed — same.
- `troup.hominidae`: **failed** — `MasterTokenError`, the durable master
  token refresh was rejected (expected for the confirmed-exposed
  identity). One-time operator bootstrap required:
  `python P:/packages/yt-is/bin/csf-nlm-auth --profile troup.hominidae`
  (add `--cdp-url http://127.0.0.1:18870` attaching a browser context
  signed into only that exact account if no interactive window works).
  Keepalive exits `3` until this completes; that is actionable auth
  health, not a reason to touch any other auth path.

## Verification after operator actions

- `git ls-files .browser` → empty.
- `git log --all --oneline -- .browser/` → only pre-removal history (or empty
  after filter-repo).
- Canonical auth probes pass for all three identities
  (`python -m csf.nlm_keepalive` exit 0).
- Google Account security pages show no unexpected active sessions/devices.
- A fetch smoke (bounded, e.g. 1 video per account) completes with the new
  sessions before resuming backlog work.

## Provenance

Discovered during the 2026-08-15 whole-package review (testing worktree
`P:/.worktrees/yt-is-testing-review-20260815`). The review report's claim
ledger classified the exposure as `verified_fact` from `git ls-files` plus
`main` == `origin/main` at commit `1eabee8`.
