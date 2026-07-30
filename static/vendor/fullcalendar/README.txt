Run static/vendor/fetch_assets.sh once (with internet access) to download:
  - index.global.min.js

into this folder. (No separate .css file needed — FullCalendar v6's
"global" bundle injects its own CSS via JS.) Until then, view_schedule.html
automatically falls back to loading FullCalendar from the CDN, so it
keeps working online.
