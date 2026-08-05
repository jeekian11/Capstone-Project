#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-time setup: download local ("vendored") copies of the third-party JS
# libraries the system uses (SweetAlert2 confirmation dialogs, FullCalendar
# for the "View Schedule" calendar/modal, and Chart.js for the dashboard /
# analytics / inventory / PC status / activity log charts), so the whole
# system works fully offline afterwards — no internet needed at runtime.
#
# Run this ONCE from a machine/network that DOES have internet access
# (e.g. your dev machine, or the lab server itself if it's briefly online):
#
#   bash static/vendor/fetch_assets.sh
#
# It only needs to be re-run if you want to update these library versions.
# The app already falls back to loading them from the CDN automatically if
# these local files are missing, so nothing breaks if you skip this step —
# you just won't have offline support for the confirmation dialogs, the
# schedule calendar, and the charts until you run it.
#
# Note: Google Fonts (Inter / Space Grotesk / JetBrains Mono) are NOT
# vendored by this script — they are loaded from fonts.googleapis.com on
# every page. Without internet, the browser silently falls back to the
# closest system font instead (cosmetic only, nothing breaks).
# ---------------------------------------------------------------------------
set -e
cd "$(dirname "$0")"

echo "Fetching SweetAlert2..."
curl -fsSL "https://cdnjs.cloudflare.com/ajax/libs/sweetalert2/11.26.25/sweetalert2.all.min.js" \
  -o sweetalert2/sweetalert2.all.min.js

echo "Fetching FullCalendar..."
curl -fsSL "https://cdnjs.cloudflare.com/ajax/libs/fullcalendar/6.1.11/index.global.min.js" \
  -o fullcalendar/index.global.min.js
# Note: v6's "global" bundle injects its own CSS via JS — there is no
# separate .css file to download.

echo "Fetching Chart.js..."
mkdir -p chartjs
curl -fsSL "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js" \
  -o chartjs/chart.umd.min.js

echo "Done. Run 'python manage.py collectstatic' if you deploy with DEBUG=False."
