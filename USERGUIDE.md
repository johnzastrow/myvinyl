# myvinyl User Guide

Site: **https://myvinyl.fluidgrid.site**

- [Part 1: Using myvinyl](#part-1-using-myvinyl)
- [Part 2: Administering the site](#part-2-administering-the-site)
- [Part 3: Running the server](#part-3-running-the-server)

---

## Part 1: Using myvinyl

### Getting started
1. Open the invite link the admin sent you. It works once and expires after 7 days.
2. Choose a username (3 to 32 letters, digits, `.`, `-`, or `_`), an optional email
   address, and a password of at least 12 characters.
3. You're signed in to your own private collection. Nobody but you and the admins can see it.

### Adding albums
| Method | How |
|---|---|
| By barcode or catalog number (most exact) | **Add album** → type it in the top box → set the condition → **Save**. If several pressings match, pick yours. |
| By artist and title | **Add album** → artist, title, format, condition → **Save**. |
| Your own Discogs collection (best) | Add your Discogs token first (see [Your Discogs collection](#your-discogs-collection)), then **Import** → **Sync my Discogs collection**. Private folders, grades, and notes come too. |
| Someone's public Discogs collection | **Import** → **Import someone's public collection** → their Discogs username → default condition → **Import collection**. |

- Leave **Street value** blank and myvinyl looks it up on Discogs. A lookup takes about
  12 to 30 seconds, and the album page refreshes itself until it's done.
- Fields you fill in are never overwritten. Blank label and year are filled from Discogs.
- Wrong barcode, or added without one? **Edit** → change **Barcode or catalog number**
  → **Save**. The pressing is looked up again, just as on Add.

### Your Discogs collection
With your own Discogs token, myvinyl reads your Discogs collection, including private
folders, and keeps it in step. It only reads from Discogs; it never changes anything there.

1. **Add your token:** on Discogs, Settings → Developers → **Generate new token**. In
   myvinyl, your name (top right) → **Discogs token** → paste it, enter your password,
   **Save token**. myvinyl shows which Discogs account it belongs to.
2. **Sync:** **Import** → choose a condition for copies that have no grade on Discogs →
   **Sync my Discogs collection**. The page shows progress and refreshes itself.
3. **What comes across:** every copy in every folder (two copies of a record become two
   albums), its folder, Discogs' **Media Condition** and **Sleeve Condition**, the
   **Notes** field, your star rating, and the date you added it. Only vinyl is imported.
4. **Prices:** each new album gets a quick price for your exact pressing right away. The
   range across other pressings fills in automatically over the next few hours.
5. **Sync again** any time to pick up changes. The rules:
   - Discogs decides which copies you have, their folders, and dates added.
   - myvinyl decides grades, notes, and ratings. A change made on Discogs is copied here
     only if you haven't changed that field here since the last sync.
   - Changed in both places? It goes on the **Discogs sync** page for you to choose
     **Keep mine** or **Use Discogs** (or the same for all at once).
   - Gone from Discogs? Listed on the same page: **Move to trash** or **Keep it here**.
     Nothing is removed without you.
   - Albums you already had here for the same pressing are matched, not duplicated.
   - Something you put in the trash here isn't brought back by a sync.
6. **If a sync stops** (network trouble, or the server restarted), **Resume sync** on the
   Import page continues where it left off.

A notice on your collection page tells you when a sync found something to decide. The
**Discogs sync** page (link on the Import page) also lists recent activity. Filter your
collection by **Discogs folder** with the folder menu in the filter bar.

### The album page
| Section | What you can do |
|---|---|
| Street value / Range | The lowest Discogs asking price for your pressing, plus the low-high range across up to 10 pressings. Not an appraisal. |
| Pressings checked | If the matched pressing isn't yours, click **Mine** on the right row. Value, details, and art switch to it. |
| Refresh from Discogs | Re-check prices now. Prices also refresh automatically about once a week. |
| Value history | A chart of value and range over time; it appears after two lookups. |
| Your rating and review | Half a star to 5 stars, plus a written review. |
| Rate tracks | Link above the tracklist: a rating and short note per track. |
| Album art | Click an image to make it the displayed cover, or **Use your own image** to upload one (JPEG, PNG, WebP, or GIF, up to 5 MB). Your upload stays until you remove it or click a Discogs image. |
| Notes | Add dated notes: cleanings, plays, where you found it. |
| Purchase | **Edit** → price paid, date, where bought. Gain or loss is calculated for you. |
| Record and sleeve grades | **Edit** → **Condition (record)** and **Sleeve condition** (Goldmine grades; a sleeve can also be Generic or No cover, or left Not graded). |
| Discogs chips | Folder name and "in your Discogs collection" show for albums linked to your Discogs collection. |

### Finding and reporting
- **Filter bar** (collection and Stats pages): search text, genre, decade, format,
  condition, minimum rating, Discogs folder (after a sync), value range. **Clear** resets it.
- **List / Covers** switches between the table and a cover grid. Click a column header to sort.
- **Stats:** breakdowns by genre, decade, format, condition, label, and rating.
- **Print report:** a printable list of the current (filtered) view. Use your browser's
  Print (Ctrl+P or Cmd+P); choose "Save as PDF" for a file.
- **Export CSV:** a spreadsheet of the current view.

### Wishlist
- **Wishlist** → add artist, title, and an optional target price. myvinyl checks the
  lowest Discogs price weekly and highlights records at or below your target.
- **Import wantlist** pulls in your public Discogs wantlist.
- **Got it:** choose the condition, enter what you paid, and the record moves to your collection.

### Deleting and restoring
- **Edit** → **Move this album to the trash**.
- **Trash** → **Restore** puts it back; **Delete forever** removes it. Albums left in the
  trash are deleted automatically after 30 days.

### Your profile
Click your name (top right):
- **Display name, About, Discogs username** (pre-fills the import forms).
- **Discogs token:** add, replace, or remove your own token (needs your password). It
  links your Discogs account for syncing and speeds up lookups. Only its last 4
  characters are shown, and admins can't see it.
- **Username and email:** change either one; this needs your current password. Your
  albums and settings stay the same. Email is optional and not used yet (it's kept for
  future email notices and password resets).
- **Theme:** Royal blue, Navy, Light, Gray, or Automatic (follows your device's dark mode).
- **Change password**, which signs out your other sessions.
- **Sign out everywhere**, if you used a shared or lost device.

Forgot your password? Ask an admin for a reset link.

---

## Part 2: Administering the site

Admins see an **Admin** link in the menu. Admins can see and change every collection,
so give the role only to people you'd trust with everything.

### Users
| Task | How |
|---|---|
| Invite someone | **Admin** → **Invite someone** → choose **member** or **admin** → **Create invite link** → send the link privately. It is shown only once. |
| Make someone an admin (or demote) | **Users** table → role menu → **Set**. |
| Rename a user or set their email | **Users** table → edit the username and email boxes → **Save**. They log in with the new username from then on. |
| Reset a password | **Reset password** on their row → send the one-time link. Using it signs them out everywhere. |
| Disable an account | **Disable** on their row. They're signed out at once and can't log in; their data is kept. **Enable** restores access. |
| Cancel an unused link | **Open links** → **Revoke**. |
| Work in someone's collection | **View collection** on their row. A red banner shows whose collection you're in; anything you add or import goes there. **Back to mine** to leave. Discogs sync isn't available while viewing someone else's collection: it only ever reads the signed-in user's own Discogs account. |

Notes:
- You can't disable yourself, and the last active admin can't be disabled or demoted.
- Accounts can't be deleted (so no data is lost by accident); disable them instead.
- Usernames and emails are unique, ignoring letter case.

### Backups
- myvinyl backs up the database every 24 hours and keeps the last 14.
- **Admin** → **Back up now** makes one immediately; the list shows the latest files.

### Discogs tokens
Each user adds their own token on their Profile page. The **Users** table shows
**set** or **none** per user; **Clear** removes a user's token (for example if it leaked).
Admins can never see a token.

---

## Part 3: Running the server

Server `recipe.fluidgrid.site`, user `jcz`. Everything lives in `~/myvinyldocker`.

| Task | Command (run in `~/myvinyldocker`) |
|---|---|
| Status | `docker compose ps` (should say `healthy`) |
| Logs | `docker compose logs --tail 100 -f` |
| Restart | `docker compose restart` |
| Stop / start | `docker compose down` / `docker compose up -d` |
| Update to the latest version | `git pull && docker compose up -d --build` |
| Change settings | Edit `.env` (keep it `chmod 600`), then `docker compose up -d` |
| Copy backups to the host | `docker compose cp myvinyl:/data/backups ./backups-$(date +%F)` |
| Health check | `curl -s http://127.0.0.1:8094/healthz` prints `ok` |

### Settings in `.env`
| Setting | Purpose |
|---|---|
| `MYVINYL_TOKEN_KEY` | Encrypts users' Discogs tokens. Required. Keep a copy somewhere safe: if it's lost or changed, everyone has to enter their token again. |
| `MYVINYL_REFRESH_DAYS` | Days between automatic price checks (default 7). |
| `MYVINYL_BACKUP_HOURS` / `MYVINYL_BACKUP_KEEP` | Backup interval and count (defaults 24 and 14). |
| `MYVINYL_TRASH_DAYS` | Days before trashed albums are purged (default 30). |
| `MYVINYL_HOST_PORT` | Local port Caddy proxies to (8094). Must match the Caddy block. |

Don't change `MYVINYL_SECRET_KEY` unless you mean to sign everyone out.

### Restoring a backup
```bash
cd ~/myvinyldocker
docker compose cp myvinyl:/data/backups ./restore      # pick a file from ./restore/backups
docker compose stop
docker compose cp ./restore/backups/myvinyl-YYYYMMDD-HHMMSS.db myvinyl:/data/myvinyl.db
docker compose start
```
If the app can't write the restored file, fix its ownership:
`docker compose run --rm --user root myvinyl chown 10001:10001 /data/myvinyl.db`

### Caddy
The site block is in `/etc/caddy/Caddyfile` (`myvinyl.fluidgrid.site` → `127.0.0.1:8094`).
After editing that file:
```bash
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
```
Access log: `/var/log/caddy/myvinyl.access.log`.

### Troubleshooting
| Symptom | Check |
|---|---|
| Site down, other sites fine | `docker compose ps`; `docker compose logs --tail 50` |
| 502 from Caddy | The container isn't running, or `MYVINYL_HOST_PORT` doesn't match the Caddy block. |
| App won't start after editing `.env` | The log names the bad setting (the app refuses invalid values). |
| Values never fill in | Discogs unreachable or rate-limited: check the logs for `Discogs lookup failed`, then use **Refresh from Discogs**. |
| Everyone locked out | Use an admin reset link. If no admin can log in, contact whoever manages the server. |

More detail: [README.md](README.md) · [deploy/DEPLOY.md](deploy/DEPLOY.md) · [SECURITY.md](SECURITY.md)
