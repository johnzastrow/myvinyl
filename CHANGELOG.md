# Changelog

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
