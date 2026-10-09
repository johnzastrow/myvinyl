# myvinyl

A minimal, self-hosted inventory for a vinyl record collection. It is single-user,
uses SQLite for storage, and renders pages on the server without JavaScript.

## Features

- Add, edit, and delete albums: artist, title, year, label, format, condition (Goldmine
  scale), notes, and street value
- Search by artist, title, or label; sort by any column
- Album count and total collection value
- CSV export, which also works as a backup
- Automatic street value from Discogs: leave the value blank and myvinyl fills in the
  lowest current asking price (USD) for a matching vinyl release. The match is automatic
  and may be a different pressing than yours; the edit page links to it so you can check.
  The app calls `api.discogs.com` without a token (25 requests per minute).

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
uv run python -m myvinyl.credentials   # prompts for a password, prints the two variables below
```

Set the printed values as environment variables. Keep them out of version control:

| Variable | Purpose |
|---|---|
| `MYVINYL_SECRET_KEY` | Signs the session cookie (32+ characters) |
| `MYVINYL_PASSWORD_HASH` | Argon2id hash of your login password |
| `MYVINYL_DB` | Optional SQLite file path (default `myvinyl.db`) |

```powershell
$env:MYVINYL_SECRET_KEY = '<value>'
$env:MYVINYL_PASSWORD_HASH = '<value>'   # single quotes: the hash contains $
uv run myvinyl                           # http://127.0.0.1:8000
```

The app refuses to start if either required variable is missing or invalid.

## Development

```powershell
uv run pytest
uv run ruff check --fix . ; uv run ruff format .
```

## Deploying online

The server binds to `127.0.0.1` by default. To reach it from the internet, put it behind a
reverse proxy that terminates HTTPS (for example Caddy), or keep it on a private network
such as Tailscale. Notes:

- The session cookie is marked `Secure`, so the app must be served over HTTPS (or `localhost`).
- Login rate limiting is keyed on client IP. uvicorn trusts `X-Forwarded-For` only from
  `127.0.0.1` by default, so a proxy on the same machine works as-is. For a proxy on another
  host, set `FORWARDED_ALLOW_IPS=<proxy-ip>`; otherwise the limit applies to all clients together.
- Back up by copying `myvinyl.db` or using **Export CSV**.
