# Vendored third-party assets (offline support)

This folder holds local copies of the two third-party JS libraries the
system uses:

- `sweetalert2/` — confirmation dialogs used across the app
  (`compulabConfirmSubmit()` in `base.html`)
- `fullcalendar/` — the calendar on the "View Schedule" page

## One-time setup

Run this once, from any machine with internet access:

```bash
bash static/vendor/fetch_assets.sh
```

That downloads the missing `.js`/`.css` files into the two folders above.
After that, the whole system works fully offline — no CDN calls at
runtime.

## Why this exists

Both libraries used to be loaded straight from a CDN
(`cdn.jsdelivr.net`, `cdnjs.cloudflare.com`). That's fine when the lab PC
has internet, but breaks the confirmation dialogs and the schedule
calendar entirely when it doesn't.

Templates now reference the local copies first, with a small JS snippet
that automatically falls back to the CDN if the local file isn't present
(e.g. before you've run the setup script) — so this keeps working
online in the meantime, with no manual switch needed either way.

## Updating versions later

Re-run `fetch_assets.sh` any time to refresh both libraries to the
pinned versions in that script.
