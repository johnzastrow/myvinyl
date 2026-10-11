myvinyl keeps an inventory of your vinyl records, with values, art, and details looked
up on Discogs. Each section below covers one part of the site; the **Help** links on
the pages bring you to the matching section.

![The collection page: a table of albums with cover thumbnails, values, and a filter bar above it](../static/guide/collection.png)
*Your collection, with the filter bar and the List / Covers switch.*

## Getting started
1. Open the invite link the admin sent you. It works once and expires after 7 days.
2. Choose a username (3 to 32 letters, digits, `.`, `-`, or `_`), an optional email
   address, and a password of at least 12 characters.
3. You're signed in to your own private collection. Nobody but you and the admins can see it.

## Adding albums
| Method | How |
|---|---|
| By barcode or catalog number (most exact) | **Add album** → type it in the top box → set the condition → **Save**. If several pressings match, pick yours. |
| By artist and title | **Add album** → artist, title, format, condition → **Save**. |
| Your own Discogs collection (best) | Add your Discogs token first (see [Your Discogs collection](#your-discogs-collection)), then **Import** → **Sync my Discogs collection**. Private folders, grades, and notes come too. |
| Someone's public Discogs collection | **Import** → **Import someone's public collection** → their Discogs username → default condition → **Import collection**. |

- Leave **Street value** blank and myvinyl looks it up on Discogs. A lookup takes about
  12 to 30 seconds, and the album page refreshes itself until it's done.
- Fields you fill in are never overwritten. Blank label and year are filled from Discogs.
- **Which pressing is yours?** When a barcode or catalog number matches several vinyl
  releases, myvinyl lists them with year, country, label, catalog number, and format
  (**details** opens each on Discogs). Click **This one** on yours, or **Start over**.
- Wrong barcode, or added without one? **Edit** → change **Barcode or catalog number**
  → **Save**. The pressing is looked up again, just as on Add.

![The Add album form: a barcode or catalog number box at the top, then artist, title, format, record and sleeve condition, value, label, and purchase fields](../static/guide/add.png)
*Add album. A barcode or catalog number gives the most exact match.*

## How myvinyl uses Discogs
myvinyl gets prices, details, and art from [Discogs](https://www.discogs.com). It only
reads from Discogs; nothing you do here changes anything there.

**What a lookup does.** For each album, myvinyl finds the pressing that best matches
(or the one you picked), then checks up to 10 pressings of the same album. It saves:
- **Prices:** the lowest current asking price for your pressing (the street value), the
  low-high range and median across the checked pressings, and how many are for sale.
  Prices are in US dollars and are asking prices, not appraisals or sale prices.
- **Release details:** label, year, genres and styles, barcode and other identifiers,
  tracklist, credits, release notes, and community counts.
- **Album art:** the release's images, to choose your cover from.

**When it happens.**
- When you add an album (about 12 to 30 seconds), or click **Refresh from Discogs**.
- Automatically about once a week for every album and wishlist record. The admin sets
  how often.
- Albums from a Discogs sync get a quick price for your exact pressing first; the full
  range fills in over the next few hours.

**With or without a Discogs token.** Everything works without a token except syncing
your own collection; lookups are just slower. Discogs allows 60 requests a minute with a
token and 25 without, counted for the whole server, so everyone on this site shares
them. A full lookup takes about 12 requests. When Discogs is busy, lookups wait their
turn: what you click goes first, and bulk imports and automatic refreshes go last.

**Other ways in from Discogs:**

| Source | What comes across | What's skipped |
|---|---|---|
| Your own collection (sync, needs your token) | Every vinyl copy in every folder, including private ones, with folder, grades, notes, rating, and date added | Non-vinyl, and albums you've put in the trash here |
| Someone's public collection (**Import**) | Each vinyl release with artist, title, year, label, format, and rating; every album gets the condition you choose | Releases already in your collection (same pressing). No grades, folders, or notes. |
| Your public wantlist (**Wishlist** → **Import wantlist**) | Each wanted release, with its price checked | Records already on your wishlist |

## Your Discogs collection
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

![The Import page's Discogs section: the linked Discogs account, a condition menu, the Sync my Discogs collection button, and the result of the last sync](../static/guide/import.png)
*Import: sync your Discogs collection and see the result of the last sync.*

![The Discogs sync page: a record grade changed in both places with Keep mine and Use Discogs buttons, an album gone from Discogs with Move to trash and Keep it here buttons, and recent activity](../static/guide/sync-review.png)
*The Discogs sync page: things to decide, and recent activity.*

## The album page
| Section | What you can do |
|---|---|
| Street value | The lowest Discogs asking price for your pressing. **release** links to it on Discogs. Not an appraisal. |
| Range across pressings | The low-high range of the lowest asking prices across up to 10 pressings, with the median and how many are for sale. |
| Community | How many Discogs members have and want this release, and its average rating there. |
| Pressings checked | Each pressing checked, with its price; the year links to Discogs. If the matched pressing isn't yours, click **Mine** on the right row: value, details, and art switch to it. **Let myvinyl pick automatically** undoes your choice. |
| Refresh from Discogs | Re-check prices now. Prices also refresh automatically about once a week. |
| Value history | A chart of value and range over time; it appears after two lookups. |
| Your rating and review | Half a star to 5 stars, plus a written review. |
| Rate tracks | Link above the tracklist: a rating and short note per track. |
| Album art | Click an image to make it the displayed cover, or **Use your own image** to upload one (JPEG, PNG, WebP, or GIF, up to 5 MB). Your upload stays until you remove it or click a Discogs image. |
| Notes | Add dated notes: cleanings, plays, where you found it. **Delete** removes one. |
| Discogs details | Release date, country, label and catalog number, format, genres and styles, barcode, and (expand it) matrix, runout, and other identifiers. **View on Discogs** opens the release. |
| Tracklist, credits, release notes | From the release on Discogs. |
| Purchase | **Edit** → price paid, date, where bought. Gain or loss is calculated for you. |
| Record and sleeve grades | **Edit** → **Condition (record)** and **Sleeve condition** (Goldmine grades; a sleeve can also be Generic or No cover, or left Not graded). |
| Discogs chips | Folder name and "in your Discogs collection" show for albums linked to your Discogs collection. |

![An album page: cover, chips for year, format, grades and Discogs folder, street value and range, purchase and gain, rating, and the value-history chart](../static/guide/album.png)
*An album page: value and range, grades, purchase, rating, and value history.*

## Finding and reporting
- **Filter bar** (collection and Stats pages): search text, genre, decade, format,
  condition, minimum rating, Discogs folder (after a sync), value range. **Clear** resets it.
- **List / Covers** switches between the table and a cover grid. Click a column header to sort.
- **Stats:** breakdowns by genre, decade, format, condition, label, and rating, plus your
  10 most valuable albums.
- **Print report:** a printable list of the current (filtered) view. Use your browser's
  Print (Ctrl+P or Cmd+P); choose "Save as PDF" for a file.
- **Export CSV:** a spreadsheet of the current view.

![The cover grid view of the collection](../static/guide/grid.png)
*Covers view.*

![The Stats page: totals and breakdowns by genre, decade, format, condition, label, and rating](../static/guide/stats.png)
*Stats for the whole collection or the current filter.*

![The printable report: a table of albums with totals at the top](../static/guide/report.png)
*Print report.*

## Wishlist
- **Wishlist** → add artist, title, and an optional target price. myvinyl checks the
  lowest Discogs price weekly and highlights records at or below your target.
- **Check price** looks a record up again now; **Remove** takes it off the list.
- **Import wantlist** pulls in your public Discogs wantlist. Records already on your
  wishlist are skipped.
- **Got it:** choose the condition, enter what you paid, and the record moves to your collection.

![The Wishlist page: wanted records with target prices and current lowest prices, and forms to add a record or import a wantlist](../static/guide/wishlist.png)
*Wishlist with target prices.*

## Deleting and restoring
- **Edit** → **Move this album to the trash**.
- **Trash** → **Restore** puts it back; **Delete forever** removes it; **Empty trash**
  removes everything there.
- Albums left in the trash are deleted automatically after a set time (30 days unless
  the admin changed it). The Trash page shows the current period.

![The Trash page listing a deleted album with Restore and Delete forever buttons](../static/guide/trash.png)
*Trash.*

## Your profile
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

**Wrong passwords.** After 5 failed sign-ins within 15 minutes, signing in is paused for
a while; wait and try again. On the Profile page, 5 wrong current passwords within 15
minutes also sign you out everywhere, in case someone else is using your session.

![The Profile page: display name, about, Discogs username, theme, change password, Discogs token with the linked account, and username and email](../static/guide/profile.png)
*Profile, including your Discogs token.*
