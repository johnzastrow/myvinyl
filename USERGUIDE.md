# myvinyl User Guide

The user guide is built into the app: click **Help** in the menu at
**https://myvinyl.fluidgrid.site**, or a **Help** link on any page to jump to the matching
section. Admins also see the admin section there.

It is written in Markdown inside the package, so it always matches the running version:

- [Using myvinyl](src/myvinyl/guide/using.md): adding albums, your Discogs collection, the
  album page, filters and reports, wishlist, trash, and your profile.
- [Administering the site](src/myvinyl/guide/admin.md): users, invites, backups, and
  Discogs tokens (shown in the app only to admins).

Running the server (Docker, `.env` settings, restores, Caddy, troubleshooting) is in
[deploy/OPERATIONS.md](deploy/OPERATIONS.md); first-time setup is in
[deploy/DEPLOY.md](deploy/DEPLOY.md).
