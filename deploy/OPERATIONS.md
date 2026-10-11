# Running the myvinyl server

Day-to-day operations for the live site. First-time setup is in [DEPLOY.md](DEPLOY.md);
how to use the site is in the app's **Help** page.

Server `recipe.fluidgrid.site`, user `jcz`. Everything lives in `~/myvinyldocker`.

| Task | Command (run in `~/myvinyldocker`) |
|---|---|
| Status | `docker compose ps` (should say `healthy`) |
| Logs | `docker compose logs --tail 100 -f` |
| Restart | `docker compose restart` |
| Stop / start | `docker compose down` / `docker compose up -d` |
| Update to the latest version | `git pull && docker compose up -d --build` |
| Change settings | Edit `.env` (keep it `chmod 600`), then `docker compose up -d` |
| Copy backups to the host | `docker compose cp myvinyl:/data/backups ./backups-$(date +%F)` |
| Health check | `curl -s http://127.0.0.1:8094/healthz` prints `ok` |

## Settings in `.env`
| Setting | Purpose |
|---|---|
| `MYVINYL_TOKEN_KEY` | Encrypts users' Discogs tokens. Required. Keep a copy somewhere safe: if it's lost or changed, everyone has to enter their token again. |
| `MYVINYL_REFRESH_DAYS` | Days between automatic price checks (default 7). |
| `MYVINYL_BACKUP_HOURS` / `MYVINYL_BACKUP_KEEP` | Backup interval and count (defaults 24 and 14). |
| `MYVINYL_TRASH_DAYS` | Days before trashed albums are purged (default 30). |
| `MYVINYL_HOST_PORT` | Local port Caddy proxies to (8094). Must match the Caddy block. |

Don't change `MYVINYL_SECRET_KEY` unless you mean to sign everyone out.

## Restoring a backup
```bash
cd ~/myvinyldocker
docker compose cp myvinyl:/data/backups ./restore      # pick a file from ./restore/backups
docker compose stop
docker compose cp ./restore/backups/myvinyl-YYYYMMDD-HHMMSS.db myvinyl:/data/myvinyl.db
docker compose start
```
If the app can't write the restored file, fix its ownership:
`docker compose run --rm --user root myvinyl chown 10001:10001 /data/myvinyl.db`

## Caddy
The site block is in `/etc/caddy/Caddyfile` (`myvinyl.fluidgrid.site` → `127.0.0.1:8094`).
After editing that file:
```bash
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
```
Access log: `/var/log/caddy/myvinyl.access.log`.

## Troubleshooting
| Symptom | Check |
|---|---|
| Site down, other sites fine | `docker compose ps`; `docker compose logs --tail 50` |
| 502 from Caddy | The container isn't running, or `MYVINYL_HOST_PORT` doesn't match the Caddy block. |
| App won't start after editing `.env` | The log names the bad setting (the app refuses invalid values). |
| Values never fill in | Discogs unreachable or rate-limited: check the logs for `Discogs lookup failed`, then use **Refresh from Discogs**. |
| Everyone locked out | Use an admin reset link. If no admin can log in, contact whoever manages the server. |

More detail: [README.md](../README.md) · [DEPLOY.md](DEPLOY.md) · [SECURITY.md](../SECURITY.md)
