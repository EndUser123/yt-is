# Credential rotation runbook — browser-profile leak (2026-08-15)

agent: zcode | host: both | incident: Chromium profile committed 2026-04-23 (`c720775`/`8bbf096`), pushed to github.com/EndUser123/yt-is, untracked from `main` 2026-08-15 (`2eeb910`) but **still present in remote history until the scrub lands**

## What was exposed

`.browser/notebooklm/` — a live Chromium profile: `Network/Cookies` (36 KB),
`Login Data` (128 KB), `Trust Tokens`, `Session Storage`, `Local State`.
A gitleaks scan of the deletion diff also surfaced jwt/api-key-shaped strings
in profile files. The profile held authenticated Google/NotebookLM sessions
for the accounts used by the multi-account fetch stack.

## Rotation steps (operator — cannot be automated from the agent side)

1. **Enumerate the affected Google accounts.** Sources: NLM auth profiles
   (`bin/csf-nlm-auth`, `.data/yt-is/nlm-auth/` — never committed), the
   coordinator's account config in `scripts/run_multi_account_fetch.py`, and
   the backup repo `C:\Users\brsth\.ytis-nlm-auth-backup\`.
2. **For each account:** Google Account → Security:
   - "Your devices" / "Manage all devices" → sign out ALL sessions.
   - Change the account password if there is any reuse concern.
   - Review 2FA/recovery settings for unexpected changes.
3. **Re-authenticate the NLM profiles** via `bin/csf-nlm-auth` for each
   account after rotation; verify a fetch works end-to-end.
4. **Treat any non-Google credentials found in the profile** (saved logins
   for other sites in `Login Data`) as exposed too; rotate those separately.
5. **Remote hygiene after the history scrub (step 4, gated):** confirm no
   GitHub forks (repo visibility dependent), and note that GitHub may cache
   pre-scrub commits; contact GitHub Support to purge cached views/forks if
   the repo was public.

## Order relative to other remediation steps

Rotation can run in parallel with everything else. The history scrub
(force-push of `git filter-repo` output) does not reduce exposure until the
sessions above are rotated — cookies in old commits remain valid credentials
regardless of git state.
