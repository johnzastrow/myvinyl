# Deploying myvinyl at myvinyl.fluidgrid.site

This guide assumes the server that already serves `recipe.fluidgrid.site` with **Caddy
installed on the host** (a system service), plus Docker with the Compose plugin.

```
Internet ──HTTPS──> Caddy (host, :443) ──HTTP──> 127.0.0.1:8085 ──> myvinyl container (:8000)
                                                                     └─ volume myvinyl-data:/data
```

## 1. DNS

Create a DNS record for `myvinyl.fluidgrid.site` pointing at the server. The simplest is a
CNAME to `recipe.fluidgrid.site`, or an A/AAAA record with the same IP. Check it with:

```bash
dig +short myvinyl.fluidgrid.site
```

## 2. Get the code

```bash
sudo mkdir -p /opt/myvinyl && sudo chown "$USER" /opt/myvinyl
git clone https://github.com/johnzastrow/myvinyl.git /opt/myvinyl
cd /opt/myvinyl
```

## 3. Secrets

```bash
cp deploy/env.template .env
chmod 600 .env
docker compose build
docker compose run --rm myvinyl python -m myvinyl.credentials
```

The last command asks for the admin password (12+ characters) and prints
`MYVINYL_SECRET_KEY` and `MYVINYL_PASSWORD_HASH`. Paste both into `.env`, keeping single
quotes around the hash. Add `MYVINYL_DISCOGS_TOKEN` if you have one.

## 4. Start

```bash
docker compose up -d
docker compose ps            # STATUS should become "healthy"
curl -s http://127.0.0.1:8085/healthz   # prints: ok
```

If port 8085 is already used on the server, change `127.0.0.1:8085` in `compose.yaml` and
`reverse_proxy 127.0.0.1:8085` in the Caddy block to the same free port.

## 5. Caddy

Append `deploy/Caddyfile.myvinyl` to the host Caddyfile, then validate and reload:

```bash
sudo tee -a /etc/caddy/Caddyfile < deploy/Caddyfile.myvinyl
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Open https://myvinyl.fluidgrid.site and log in as `admin` (or your `MYVINYL_ADMIN_USER`).
Then **Admin → Invite someone** to add other users.

## Moving your existing local collection

To bring the database from your laptop instead of starting empty:

```bash
# On your laptop: stop myvinyl, then copy the database to the server.
scp myvinyl.db server:/opt/myvinyl/
# On the server, before the first `docker compose up`:
docker compose create
docker compose cp myvinyl.db myvinyl:/data/myvinyl.db
docker compose run --rm --user root myvinyl chown 10001:10001 /data/myvinyl.db
docker compose up -d
rm myvinyl.db
```

When an existing database has no accounts yet, the first start creates the admin from
`MYVINYL_PASSWORD_HASH` and gives it all existing albums.

## Updating

```bash
cd /opt/myvinyl
git pull
docker compose up -d --build
```

The database is upgraded automatically on startup. Take a backup first (below).

## Backups

- myvinyl writes a backup every 24 hours (keeping 14) to `/data/backups` inside the
  volume. **Admin → Back up now** makes one on demand.
- These live on the same disk as the database. Copy them somewhere else regularly:

```bash
docker compose cp myvinyl:/data/backups ./backups-$(date +%F)
```

- Restore: stop the container, copy a backup over `/data/myvinyl.db`, start it again.

## Security notes

- The container runs as an unprivileged user (uid 10001) with a read-only filesystem, all
  Linux capabilities dropped, and `no-new-privileges`. Only `/data` and `/tmp` are writable.
- The app port is bound to `127.0.0.1`, so only Caddy can reach it. Don't change it to
  `0.0.0.0` or remove the `127.0.0.1:` prefix: that would expose plain HTTP and make the
  `FORWARDED_ALLOW_IPS="*"` setting unsafe.
- `.env` holds the session key and Discogs token. Keep it `chmod 600`, out of git (it is in
  `.gitignore`), and out of backups that leave the server unencrypted.
- Consider a firewall (for example `ufw allow 80,443/tcp` plus SSH only).
