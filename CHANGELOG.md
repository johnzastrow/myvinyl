# Changelog

All notable changes to myvinyl. Versions follow [semantic versioning](https://semver.org/).

## 0.6.0 - 2026-10-09

### Added
- **Multiple users.** Each user has a private collection, wishlist, and stats. Admins can
  view and change any collection ("View collection" with a banner), invite people with
  one-time links (7-day expiry), create password-reset links, change roles, and disable
  accounts. The last active admin is protected.
- **Profiles and themes.** Display name, about text, Discogs username (pre-fills imports),
  and a theme per user: Royal blue, Navy, Light, Gray, or Automatic.
- **Trash (soft delete).** Deleted albums go to the trash with all their data; restore
  them, delete them forever, or empty the trash. Trashed albums are purged after
  `MYVINYL_TRASH_DAYS` (default 30).
- **Purchases.** Price paid, date bought, and where, with gain or loss per album and in
  the collection totals.
- **Dated notes** per album, alongside the existing notes field.
- **Wishlist.** Target price, current lowest Discogs price (re-checked weekly), "at or
  below target" highlight, Discogs wantlist import, and "Got it" to move a record into the
  collection with the price paid.
- **Cover grid view** of the collection.
- **Filters** by text, genre or style, decade, format, condition, minimum rating, and value
  range. Totals, stats, CSV export, and reports follow the filters.
- **Stats page** with breakdowns by genre, decade, format, condition, label, and rating,
  plus the most valuable records.
- **Printed reports** of any filtered view, with cover thumbnails and a black-on-white
  print stylesheet.
- **Saved covers.** The displayed cover is downloaded and served locally.
- **Automatic backups** using SQLite's online backup API (`MYVINYL_BACKUP_HOURS`,
  `MYVINYL_BACKUP_KEEP`), plus "Back up now" on the admin page.
- **Deployment:** Dockerfile (non-root, read-only), `compose.yaml`, a Caddy site block for
  `myvinyl.fluidgrid.site`, and `deploy/DEPLOY.md`.
- `/healthz` endpoint for container health checks.
- `SECURITY.md` with the full security review.

### Changed
- Login asks for a username. The first admin is created from `MYVINYL_PASSWORD_HASH`
  (username `MYVINYL_ADMIN_USER`, default `admin`) and owns existing albums.
- The Discogs token is one shared server setting; imports take a Discogs username, so
  each user can import their own public collection or wantlist.
- The database uses WAL mode so pages stay responsive while background jobs write.

### Security
- Password hashing and verification run off the event loop with limited concurrency
  (a login flood could previously stall the whole site).
- "Sign out everywhere" on the profile page.
- Cover downloads refuse HTTP redirects.
- One-time link tokens are redacted from the access log and stored only as hashes.
- No `Server` header; `Permissions-Policy` header added.
- Each account is capped at 20,000 albums so one user can't exhaust the shared Discogs
  quota.

### Upgrade notes
- New tables (`users`, `invites`, `album_notes`, `wishlist`) and album columns are added
  automatically. Keep `MYVINYL_PASSWORD_HASH` set for the first start after upgrading, so
  the admin account can be created.

## 0.5.0 - 2026-10-09

### Added
- **Pick your pressing.** A **Mine** button on every row of the pressings table pins the
  album to that pressing; value, metadata, and art follow it, and refreshes keep it.
  "Let myvinyl pick automatically" undoes it.
- **Add by barcode or catalog number.** An optional field on the Add form finds the exact
  pressing on Discogs and fills in artist, title, year, label, and format. When several
  pressings share a barcode (for example black and colored vinyl), you choose one.
- **Import from Discogs.** Copies the vinyl in your Discogs collection (requires a token),
  with pressings already picked and your Discogs star ratings. Safe to re-run: releases
  already imported are skipped.
- **Value history.** Every lookup is recorded, and the album page charts value and the
  low-high range over time. Albums are re-checked automatically when their prices are
  older than `MYVINYL_REFRESH_DAYS` (default 7; 0 turns it off), a few at a time.
- **Ratings and reviews.** Rate albums from half a star to 5 stars and write a review;
  rate each track and add a short note. Ratings show in the collection and sort.
- CSV export includes pressing picked, barcode/catalog number, rating, and review.

### Changed
- Discogs lookups now run on a single background worker, so new albums, refreshes, and
  imports share the rate limit in order. The collection page shows how many are waiting.

### Upgrade notes
- New columns (`pressing_locked`, `identifier`, `rating`, `review`) and tables
  (`value_history`, `track_ratings`) are added automatically on startup. History starts
  with the next lookup of each album.

## 0.4.0 - 2026-10-09

### Added
- **Value range across pressings.** Every lookup checks the lowest asking price of up to 10
  matching vinyl pressings and records the low, median, and high, plus each pressing's
  price and copies for sale. The collection page shows each album's range and the
  collection's total range.
- **Discogs metadata.** The full Discogs release record for the best-matching pressing is
  stored, and a new album page shows release date, country, label and catalog number,
  format details, genres, styles, barcodes, matrix/runout identifiers, tracklist, credits,
  community have/want counts and rating, and release notes.
- **Fill in the blanks.** An empty label or year is filled from the matched pressing.
  Fields you entered are never overwritten.
- **Album art chooser.** All images on the Discogs release (front, back, labels, inserts)
  are kept; pick which one the album page displays.
- **Optional Discogs token** (`MYVINYL_DISCOGS_TOKEN`) raises the API limit from 25 to 60
  requests per minute. The README explains how to get one.
- Logo (SVG) and favicon (SVG, ICO, and Apple touch icon).
- CSV export now includes value low/median/high, value source, and Discogs release ID.

### Changed
- **Lookups run in the background.** Saving an album returns immediately; the album page
  shows progress and refreshes itself until the lookup finishes. A built-in throttle keeps
  requests under the Discogs rate limit and retries once after a 429 response.
- A value you type is kept when Discogs data refreshes. Clear the value and refresh to go
  back to the Discogs price.
- **Stronger Bills theme.** Royal blue pages (navy in dark mode), a navy header with a
  red-white-red stripe, red and white buttons, white inputs, red-topped cards and
  condition chips.
- Timestamps are displayed in US Eastern time (EST/EDT).
- Edit and Cancel now return to the album page.

### Fixed
- Pressing labels showed every company credit from the search result; only the label is
  kept now.

### Upgrade notes
- The database is upgraded automatically on startup (new `value_*`, `discogs_status`,
  `discogs_checked_at`, and `cover_uri` columns; new `discogs_releases` and
  `discogs_pressings` tables). Click **Refresh from Discogs** on existing albums to fill
  in range, metadata, and art.
- New dependency: `tzdata` (time zone data on Windows). Run `uv sync`.

## 0.3.0 - 2026-10-09

- Automatic street value: when an album is saved without a value, myvinyl picks a matching
  vinyl release on Discogs (structured artist/title search, closest year, official
  pressings first) and stores its lowest current asking price in USD. No API token needed.
- "Look up value on Discogs" button on the edit page; the edit page links to the matched
  release. Typing a value by hand marks it as manual.
- Database: new `value_source` and `discogs_release_id` columns, added automatically to
  existing databases on startup.

## 0.2.0 - 2026-10-09

- New Buffalo Bills color theme (royal blue, red, white, navy) for light and dark mode:
  blue top bar with a red-white-red stripe, red table rule, striped rows.

## 0.1.0 - 2026-10-09

- Initial release: add, edit, delete, search, and sort albums; collection count and value
  totals; CSV export; single-user Argon2id login with rate limiting and CSRF protection.
