# TODO

Backlog for myvinyl. Each item links to its GitHub issue, where discussion and progress
happen: https://github.com/johnzastrow/myvinyl/issues. Labels: `in progress`,
`decision needed` (an answer is needed before work starts), `idea` (not scheduled),
`security`, `ops`.

## In progress
- [ ] Per-user Discogs tokens, entered in the web UI
      ([#3](https://github.com/johnzastrow/myvinyl/issues/3)).
  - Decided 2026-10-11: one token per user; no shared token. Users without a token use
    Discogs' anonymous access (slower), and the Profile page explains how to add one.
  - Encrypted at rest with AES-256-GCM (`cryptography` package), key in a separate
    server setting; shown only as the last 4 characters; current password required to
    change it; never logged.
  - Background jobs use the album owner's token.
  - Admins see and change every collection, never anyone's token, and can clear a
    user's token.

## Next up

### Deployment
Live since 2026-10-09 at https://myvinyl.fluidgrid.site.
- [ ] Copy automatic backups off the server
      ([#6](https://github.com/johnzastrow/myvinyl/issues/6)).
- [ ] Pin the Docker base images by digest
      ([#7](https://github.com/johnzastrow/myvinyl/issues/7)).

### Security
- [ ] (decision) Second factor (TOTP), or Caddy forward-auth, or a VPN
      ([#4](https://github.com/johnzastrow/myvinyl/issues/4)).
- [ ] Re-run the security review after major changes; `pip-audit` on dependency bumps
      ([#8](https://github.com/johnzastrow/myvinyl/issues/8)).

### Email
- [ ] (decision) Send email through SMTP2GO: invites, password reset, price alerts
      ([#5](https://github.com/johnzastrow/myvinyl/issues/5)).

## Ideas, not scheduled
- Condition-based price suggestions from Discogs ([#9](https://github.com/johnzastrow/myvinyl/issues/9)).
- Separate sleeve grade ([#10](https://github.com/johnzastrow/myvinyl/issues/10)).
- Reports as a downloadable PDF ([#11](https://github.com/johnzastrow/myvinyl/issues/11)).
- CSV import ([#12](https://github.com/johnzastrow/myvinyl/issues/12)).
- Shelf location and duplicate copies ([#13](https://github.com/johnzastrow/myvinyl/issues/13)).
- Pagination for very large collections ([#14](https://github.com/johnzastrow/myvinyl/issues/14)).
- Read-only sharing of a collection ([#15](https://github.com/johnzastrow/myvinyl/issues/15)).
- Collection-wide value history chart ([#16](https://github.com/johnzastrow/myvinyl/issues/16)).

## Done
- 0.7.0: upload your own cover ([#1](https://github.com/johnzastrow/myvinyl/issues/1));
  edit the barcode or catalog number ([#2](https://github.com/johnzastrow/myvinyl/issues/2)).
- 0.6.1: change usernames, optional email per account, version footer, user guide.
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
