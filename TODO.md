# TODO

Backlog for myvinyl. Items marked **(decision)** need an answer before work starts.

## Next up

### Deployment (needs you, on the server)
- [ ] DNS record for `myvinyl.fluidgrid.site` (CNAME to `recipe.fluidgrid.site` or an A
      record).
- [ ] Follow `deploy/DEPLOY.md`: clone, create `.env`, `docker compose up -d --build`,
      add the Caddy site block, reload Caddy.
- [ ] Build and test the Docker image (Docker wasn't available on the development
      machine, so the image hasn't been built yet).
- [ ] Set up off-server copies of `/data/backups`.
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
