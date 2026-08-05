# Vendored third-party assets (offline support)

This folder holds local copies of the third-party JS libraries the
system uses:

- `sweetalert2/` — confirmation dialogs used across the app
  (`compulabConfirmSubmit()` in `base.html`)
- `fullcalendar/` — the calendar on the "View Schedule" page
- `chartjs/` — the charts on the Admin/Instructor dashboards, Reporting &
  Analytics, Inventory, PC Power Status, and PC Activity Log pages

## One-time setup

Run this once, from any machine with internet access:

```bash
bash static/vendor/fetch_assets.sh
```

That downloads the missing `.js` files into the three folders above.
After that, the whole system works fully offline — no CDN calls at
runtime for these three libraries.

## Why this exists

These libraries used to be loaded straight from a CDN
(`cdn.jsdelivr.net`, `cdnjs.cloudflare.com`). That's fine when the lab PC
has internet, but breaks the confirmation dialogs, the schedule
calendar, and all the charts entirely when it doesn't.

Templates now reference the local copies first, with a small JS snippet
that automatically falls back to the CDN if the local file isn't present
(e.g. before you've run the setup script) — so this keeps working
online in the meantime, with no manual switch needed either way.

## What's still online-only

Google Fonts (Inter, Space Grotesk, JetBrains Mono) are loaded from
`fonts.googleapis.com` on every page and are **not** vendored here.
Without internet the browser just falls back to the closest system font —
purely cosmetic, no feature breaks. If you want pixel-exact typography
offline too, vendor the font files (e.g. via the `@fontsource/*` npm
packages) and swap each page's Google Fonts `<link>` for a local
stylesheet — ask if you'd like this done as well.

## Updating versions later

Re-run `fetch_assets.sh` any time to refresh all three libraries to the
pinned versions in that script.
