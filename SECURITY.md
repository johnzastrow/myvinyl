# Security

## Reporting a problem

Please report suspected vulnerabilities privately through GitHub's
**Security → Report a vulnerability** on this repository rather than in a public issue.

## Security review (2026-10-11, version 0.8.0)

A follow-up review of everything since 0.6.0: cover uploads and barcode edits (0.7.0), the
per-user encrypted Discogs tokens (0.8.0), the deployment files, and the security
requirements for planned work (second factor, email, off-server backups, sharing links,
two-way Discogs sync; recorded on those GitHub issues). The 0.6.0 controls below were
re-checked and still hold. No Critical or High findings.

| ID | Finding | Severity | Status |
|---|---|---|---|
| VULN-008 | Current-password checks on Profile forms had no rate limit: a stolen session could guess the password, and any member could tie up the Argon2 slots logins use | Medium | Fixed |
| VULN-009 | An admin viewing another collection ran imports and barcode searches with that user's Discogs token (could import their private Discogs collection) | Low | Fixed |
| VULN-010 | A disabled account's Discogs token kept being used by scheduled refreshes | Low | Fixed |
| C-1 | Discogs API terms may limit how long marketplace prices are shown (prices refresh weekly) | Compliance | Open, [#18](https://github.com/johnzastrow/myvinyl/issues/18) |
| C-2 | The app lacks the "not affiliated with Discogs" notice the API terms ask for | Compliance | Open, [#18](https://github.com/johnzastrow/myvinyl/issues/18) |

#### VULN-008: Unthrottled password re-checks (Medium, fixed)
- **Fix:** Every current-password check (Discogs token, username/email, password change)
  goes through a per-account limiter: 5 wrong passwords in 15 minutes locks those forms
  (HTTP 429) and signs the account out everywhere, ending any stolen session. Failures
  are logged with the user id.

#### VULN-009: Admin view used the viewed user's token (Low, fixed)
- **Fix:** Discogs work started by a request (imports, barcode searches) always uses the
  signed-in user's own token. Background lookups of a collection's albums still use the
  owner's token.

#### VULN-010: Disabled accounts' tokens stayed in use (Low, fixed)
- **Fix:** Tokens of disabled accounts are never used, the cached client is dropped on
  disable, and scheduled refreshes skip disabled owners' albums and wishes.

### Notes
- Decrypted tokens are cached in process memory while in use (one process). Losing or
  changing `MYVINYL_TOKEN_KEY` makes stored tokens unreadable; users fall back to
  anonymous access and re-enter them. Key rotation is not built yet
  ([#17](https://github.com/johnzastrow/myvinyl/issues/17)).
- Login rate limits apply per IP and per username; Profile re-checks per account.

## Security review (2026-10-09, version 0.6.0)

A full review of the stack before the first internet deployment. It covered the FastAPI
app and Jinja templates, the SQLite layer, multi-user authentication and authorization,
the Discogs client (outbound HTTP and image downloads), background jobs, file storage, the
Docker image, compose file, and Caddy configuration. Method: data-flow tracing of every
request input to every sink, checked against the OWASP cheat-sheet categories (injection,
XSS, CSRF, authentication, authorization/IDOR, SSRF, file handling, business logic,
misconfiguration, error handling, logging, supply chain), plus a dependency audit
(`pip-audit` against `uv.lock`: no known vulnerabilities).

### Summary

| ID | Finding | Severity | Status |
|---|---|---|---|
| VULN-001 | Argon2 password checks ran on the event loop: a burst of login attempts stalled every request | Medium | Fixed |
| VULN-002 | Logging out could not revoke a stolen session cookie; no "sign out everywhere" | Low | Fixed (new control) |
| VULN-003 | Cover downloads followed HTTP redirects before the host check | Low | Fixed |
| VULN-004 | One-time invite/reset tokens appeared in the access log | Low | Fixed |
| VULN-005 | Concurrent invite sign-ups with the same username raised a server error | Low | Fixed |
| VULN-006 | No cap on albums per account; one member could exhaust the shared Discogs quota | Low | Fixed |
| VULN-007 | Server header advertised `uvicorn` | Low | Fixed |

#### VULN-001: Password hashing blocked the server (Medium, fixed)
- **Location:** `main.py` login, invite/reset, and change-password handlers.
- **Issue:** Argon2id verification takes about 145 ms and 64 MB per call, and it ran
  directly inside `async` handlers. Each attempt froze the event loop, so about 7
  attempts per second (from many IPs, which the per-IP limiter doesn't stop) made the
  site unresponsive for everyone, and parallel hashes could exhaust the container's memory.
- **Fix:** Hashing and verification now run on worker threads behind a semaphore of 2
  (`check_password` / `make_hash`).

#### VULN-002: Session revocation (Low, fixed)
- **Issue:** Sessions are signed cookies. Logging out clears the cookie in that browser,
  but a copy taken earlier stayed valid until it expired (12 hours).
- **Fix:** **Profile → Sign out everywhere** bumps the account's session version, which
  invalidates every existing cookie. Password changes, resets, and disabling an account
  already did this.

#### VULN-003: Redirects on image downloads (Low, fixed)
- **Issue:** `fetch_image` checked the final URL after `urllib` had already followed any
  redirect, so the request could reach another host first. Image URLs come from Discogs
  data and are restricted to `https://i.discogs.com/`, which made this hard to exploit.
- **Fix:** Image downloads use an opener that refuses all redirects. The host, scheme, and
  port are checked before the request, and the content type is checked by its bytes
  afterwards (JPEG/PNG/WebP/GIF only, 5 MB max).

#### VULN-004: Tokens in logs (Low, fixed)
- **Issue:** `GET /link/<token>` was written to the access log in full, so anyone who
  could read the logs could use an unused invite or reset link.
- **Fix:** A logging filter replaces the token with `[redacted]`. Tokens are single-use,
  expire after 7 days, and are stored only as SHA-256 hashes.

#### VULN-005: Username race (Low, fixed)
- **Fix:** A unique-constraint failure now shows "That username is taken" instead of a
  500 error.

#### VULN-006: Shared Discogs quota (Low, fixed)
- **Fix:** Each account is limited to 20,000 albums, for both manual adds and imports.
  All Discogs calls go through one throttled worker, so heavy use slows lookups for
  everyone but can't get the server rate-limited or banned by Discogs.

#### VULN-007: Server banner (Low, fixed)
- **Fix:** uvicorn runs with `server_header=False`; Caddy also strips `Server`.

### Controls verified (no finding)

| Area | What was checked |
|---|---|
| SQL injection | Every query uses bound parameters. The only interpolated SQL fragments are fixed clauses chosen from allowlists (sort columns, filter clauses, migration DDL). |
| XSS | Jinja2 auto-escaping is on for all templates; the one `\|safe` applies to constant attribute strings in a template macro. Discogs text, notes, reviews, and filters are escaped (covered by tests). CSP is `script-src 'none'`. |
| CSRF | Every state-changing route is POST and checks a per-session token with `hmac.compare_digest`; cookies are `SameSite=Lax`. GET routes have no side effects. |
| Authentication | Argon2id hashes, a 12-character minimum, a dummy hash for unknown usernames (no timing oracle), identical error messages, per-IP and per-username rate limits, fresh session on login, session version checked on every request. |
| Authorization / IDOR | Every album, cover, note, track, trash, and wishlist route loads the record and checks owner-or-admin, returning 404 (not 403) so ids can't be probed. Admin routes require the admin role, read from the database on each request. The last active admin can't be disabled or demoted. |
| SSRF | The Discogs API host is fixed; paths are built only from integers or a regex-validated, URL-quoted username. Image URLs are allowlisted to `i.discogs.com` and must be among the release's own images. |
| Path traversal | Cover files are named `<integer>.<ext>`; reads validate that pattern before touching disk. Backup paths are fixed. |
| Discogs tokens (0.8.0) | Each user's token is encrypted with AES-256-GCM (`cryptography`), a random 96-bit nonce per save, and the user id as associated data, so a value copied to another account won't decrypt. The key (`MYVINYL_TOKEN_KEY`) lives only in the environment; database copies and backups hold ciphertext. Setting, replacing, or removing a token needs the current password and a CSRF token; a new token is verified with Discogs before it is stored. Only the last 4 characters are ever rendered; admins see set/none and can clear it (logged) but can't read it. Tokens are never logged, and `repr` of settings, clients, and the cipher hides them. If a value can't be decrypted (key changed, tampering), the user falls back to anonymous access. Decrypted tokens are held in process memory while in use. |
| File upload (0.7.0) | Cover uploads are owner-or-admin and CSRF-checked. The type comes from the file's magic bytes (JPEG, PNG, WebP, GIF only; no SVG), never from its name or declared type. The size is capped at 5 MB in the app (Content-Length checked before parsing, then the bytes read) and 6 MB in Caddy on that one path. Files are stored as `<album id>.<ext>`, served with a fixed image content type, `nosniff`, and the CSP. Image metadata such as EXIF location is kept as uploaded. |
| Deserialization | Only `json.loads` on Discogs responses (size-capped, every field type-checked). No pickle/YAML/eval. |
| Open redirect | All redirects are to fixed internal paths with integer ids. |
| CSV injection | Exported cells starting with `= + - @ \t \r` are prefixed with `'`. |
| Headers | CSP, `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: same-origin`, `Permissions-Policy`, `Cache-Control: no-store` (covers: private); HSTS from Caddy. |
| Secrets | Fail-closed configuration; secrets excluded from `repr` and logs; `.env` git- and docker-ignored; no secrets in the repository history. |
| Container | Non-root uid 10001, read-only root filesystem, all capabilities dropped, `no-new-privileges`, memory and pid limits, port bound to `127.0.0.1` only. |

### Accepted risks and recommendations

- **Username lockout:** 5 wrong passwords lock that username for 15 minutes, which
  someone could use to annoy a known user. This trades a little availability for strong
  brute-force protection; admins can still reset passwords.
- **Admin power:** By design, admins can read and change every collection and reset any
  password. Give the admin role only to people you'd trust with everything.
- **Image pinning:** The Dockerfile pins `python:3.13-slim` and `uv:0.11.20` by tag. For
  stricter supply-chain control, pin them by digest and rebuild regularly for security
  updates.
- **Backups:** Automatic backups sit on the same disk as the database. Copy them off the
  server (see `deploy/DEPLOY.md`).
- **Single process:** Rate limits and the job queue are in memory, so they reset on
  restart and assume one app process (which is how compose runs it).
- **MFA:** Not implemented. Consider putting the site behind Caddy forward-auth or a VPN
  if you need a second factor.
