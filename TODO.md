# TODO

Backlog for myvinyl. Items marked **(decision)** need an answer before work starts.

## Next up

### Deployment
Live since 2026-10-09 at https://myvinyl.fluidgrid.site (server `recipe.fluidgrid.site`,
stack in `~/myvinyldocker`, container `myvinyl` on `127.0.0.1:8094`).
- [x] DNS (already resolved via the existing fluidgrid.site records).
- [x] Image built and running (healthy); HTTPS certificate issued by Caddy.
- [x] Caddy site block applied live through the admin API.
- [ ] **Persist the Caddy config** (needs sudo): copy
      `~/myvinyldocker/caddy/Caddyfile.proposed` to `/etc/caddy/Caddyfile`. Until then a
      Caddy restart or reload from the file drops the myvinyl site.
- [ ] Admin: log in, change the generated password, delete `~/myvinyldocker/ADMIN_PASSWORD.txt`.
- [ ] Optional: set `MYVINYL_DISCOGS_TOKEN` in `~/myvinyldocker/.env`, then `docker compose up -d`.
- [ ] Copy `/data/backups` off the server (cron + rsync or similar).
- [ ] Optional: pin the Docker base images by digest.

### Security follow-ups (see SECURITY.md)
- [ ] (decision) Second factor (TOTP), or put the site behind Caddy forward-auth or a VPN.
- [ ] Re-run the security review after major changes; run `pip-audit` on dependency bumps.

## Ideas, not scheduled
- Condition-based price suggestions from Discogs (token, possibly seller settings).
- Separate sleeve grade (Goldmine grades record and sleeve separately).
- Report as a downloadable PDF (today: browser Print to PDF).
- CSV import.
- Shelf location and duplicate copies.
- Pagination for very large collections.
- Read-only sharing of a collection with people who have no account.
- Collection-wide value history chart.

## Done
- 0.6.0: multiple users with admin, invite links, and profiles; five themes; trash with
  restore; purchase tracking and gain/loss; dated notes; wishlist with wantlist import;
  cover grid; filters; stats; printed reports; saved covers; automatic backups; Docker and
  Caddy deployment files; full security review with fixes.
- Repository published: https://github.com/johnzastrow/myvinyl (public).
- 0.5.0: pick your pressing, barcode/catalog-number add, Discogs import, value history
  with weekly refresh, album and track ratings and reviews.
- 0.4.0: value range across pressings, Discogs metadata, album art chooser, optional
  token, stronger Bills theme, Eastern time, logo and favicon, README.
- 0.3.0: automatic street value from Discogs.
- 0.2.0: Bills color theme.
- 0.1.0: inventory, search, sort, CSV export, secure single-user login.
