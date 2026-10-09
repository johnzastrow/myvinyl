# TODO

Backlog for myvinyl, in rough priority order. Items marked **(decision)** need an answer
before work starts.

## Next up

### Multi-user (decision)
Do this before the per-user features below, since every table needs an owner.
- [ ] Decide: separate private collections per user, or one shared collection with
      several editors?
- [ ] Decide: how accounts are created (admin invites, open sign-up, admin creates them).
- [ ] Decide: roles (admin / member / read-only viewer).
- [ ] Users table, per-user Argon2id passwords, per-user login rate limits.
- [ ] Scope albums, ratings, notes, wishlist, and history to their owner; authorization
      checks on every route.
- [ ] Admin page: invite or create users, reset passwords, disable accounts.
- [ ] Move the current single-password login to the first admin account.
- [ ] Per-user Discogs token for imports (stored encrypted), or a shared server token.

### Collection data
- [ ] Purchase tracking: price paid, date, where bought. Gain or loss per album and for
      the whole collection.
- [ ] Ad hoc dated notes per album (a journal of entries), alongside the existing notes field.
- [ ] Wishlist: records you want, with current lowest Discogs price, target price,
      "Got it" to move to the collection, and import of your Discogs wantlist.

### Browsing and reports
- [ ] Cover grid view of the collection.
- [ ] Filters: genre, style, decade, format, condition, rating, price range. Store Discogs
      genres and styles on each album so filtering is fast.
- [ ] Stats page: counts and value by genre, decade, format, condition; top labels;
      most valuable records; rating distribution; purchase cost against current value.
- [ ] Printed reports from any filtered view: print stylesheet plus a printable report
      page (cover thumbnails, values, totals), suitable for insurance.

### Reliability
- [ ] Save chosen covers locally (only from `i.discogs.com`, type and size checked) and
      serve them from myvinyl.
- [ ] Automatic backups: daily SQLite backup with the online backup API, keep the last N.

### Deployment (decision)
- [ ] Dockerfile (non-root user, `/data` volume for database, covers, backups).
- [ ] docker-compose.yml.
- [ ] Caddy site `myvinyl.fluidgrid.site` on the server that already serves
      `recipe.fluidgrid.site`. Decide: does Caddy run on the host or in Docker? That sets
      the upstream address and the trusted-proxy setting.
- [ ] DNS record for `myvinyl.fluidgrid.site`.

## Ideas, not scheduled
- Condition-based price suggestions from Discogs (token, possibly seller settings).
- Separate sleeve grade (Goldmine grades record and sleeve separately).
- Insurance report as PDF.
- CSV import.
- Shelf location and duplicate copies.
- Pagination for large collections.

## Done
- 0.5.0: pick your pressing, barcode/catalog-number add, Discogs import, value history
  with weekly refresh, album and track ratings and reviews.
- 0.4.0: value range across pressings, Discogs metadata, album art chooser, optional
  token, stronger Bills theme, Eastern time, logo and favicon, README.
- 0.3.0: automatic street value from Discogs.
- 0.2.0: Bills color theme.
- 0.1.0: inventory, search, sort, CSV export, secure single-user login.
