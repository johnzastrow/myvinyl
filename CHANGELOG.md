# Changelog

All notable changes to myvinyl. Versions follow [semantic versioning](https://semver.org/).

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
