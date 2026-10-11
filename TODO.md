# TODO

Checklist for myvinyl. Each line links to its GitHub issue, where the details, decisions,
and progress notes live: https://github.com/johnzastrow/myvinyl/issues. Ideas, fixes, and
changes to the code, repo, or docs go in both places.

Legend: **(decision)** needs an answer before work starts.

## In progress
- [ ] Discogs integration ([#20](https://github.com/johnzastrow/myvinyl/issues/20)): plan approved 2026-10-11
  - [x] Phases 0+1: private collection import and sync, sleeve grade, 0.9.0 ([#22](https://github.com/johnzastrow/myvinyl/issues/22), [#10](https://github.com/johnzastrow/myvinyl/issues/10))
  - [ ] Phase 2: collection value and Discogs data on albums ([#23](https://github.com/johnzastrow/myvinyl/issues/23))
  - [ ] Phase 3: private wantlist sync ([#24](https://github.com/johnzastrow/myvinyl/issues/24))
  - [ ] Phase 4: discovery, gaps and Lists ([#25](https://github.com/johnzastrow/myvinyl/issues/25))
  - [ ] Phase 5: two-way sync via Discogs OAuth, opt-in ([#26](https://github.com/johnzastrow/myvinyl/issues/26))
  - Keep the user guide updated with each phase.

## Next up
- [ ] Token key rotation ([#17](https://github.com/johnzastrow/myvinyl/issues/17))
- [ ] Copy automatic backups off the server ([#6](https://github.com/johnzastrow/myvinyl/issues/6))
- [ ] Pin the Docker base images by digest ([#7](https://github.com/johnzastrow/myvinyl/issues/7))
- [ ] Security review after major changes; `pip-audit` on dependency bumps ([#8](https://github.com/johnzastrow/myvinyl/issues/8))
- [ ] (decision) Second factor, Caddy forward-auth, or VPN ([#4](https://github.com/johnzastrow/myvinyl/issues/4))
- [ ] (decision) Email through SMTP2GO: invites, password reset, price alerts ([#5](https://github.com/johnzastrow/myvinyl/issues/5))

## Ideas, not scheduled
- [ ] Condition-based price suggestions from Discogs ([#9](https://github.com/johnzastrow/myvinyl/issues/9))
- [ ] Reports as a downloadable PDF ([#11](https://github.com/johnzastrow/myvinyl/issues/11))
- [ ] Detailed printable report: full details for each album ([#27](https://github.com/johnzastrow/myvinyl/issues/27))
- [ ] CSV import ([#12](https://github.com/johnzastrow/myvinyl/issues/12))
- [ ] Shelf location and duplicate copies ([#13](https://github.com/johnzastrow/myvinyl/issues/13))
- [ ] Pagination for very large collections ([#14](https://github.com/johnzastrow/myvinyl/issues/14))
- [ ] Read-only sharing of a collection ([#15](https://github.com/johnzastrow/myvinyl/issues/15))
- [ ] Collection-wide value history chart ([#16](https://github.com/johnzastrow/myvinyl/issues/16))

## Done
- [x] 0.10.0: in-app user guide, linked from every page ([#21](https://github.com/johnzastrow/myvinyl/issues/21))
- [x] 0.9.0: sync your own Discogs collection; sleeve grade ([#22](https://github.com/johnzastrow/myvinyl/issues/22), [#10](https://github.com/johnzastrow/myvinyl/issues/10))
- [x] Discogs API terms reviewed: not applied, personal non-public use ([#18](https://github.com/johnzastrow/myvinyl/issues/18))
- [x] Test coverage 89% to 96% with a 94% floor ([#19](https://github.com/johnzastrow/myvinyl/issues/19))
- [x] 0.8.0: per-user Discogs tokens, encrypted at rest; security review fixes ([#3](https://github.com/johnzastrow/myvinyl/issues/3))
- [x] 0.7.0: upload your own cover ([#1](https://github.com/johnzastrow/myvinyl/issues/1)); edit the barcode or catalog number ([#2](https://github.com/johnzastrow/myvinyl/issues/2))
- [x] 0.6.1: change usernames, optional email per account, version footer, user guide
- [x] 0.6.0: multiple users, admin, invites, profiles, themes, trash, purchases, notes,
      wishlist, cover grid, filters, stats, reports, saved covers, backups, Docker and Caddy
      deployment, full security review
- [x] 0.5.0: pick your pressing, barcode add, Discogs import, value history, ratings and reviews
- [x] 0.4.0 and earlier: value range, Discogs metadata, album art, themes, CSV export, login
