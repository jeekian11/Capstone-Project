// static/js/dashboard.js
// Auto-refreshes each PC's status pill on the PC Status page without a full
// page reload. The server already keeps pinging PCs in the background (see
// labs/network.py + labs/apps.py); this just polls the JSON snapshot of
// that data and updates the DOM to match, every 10 seconds.

const STATUS_LABELS = {
  online: 'Online',
  offline: 'Offline',
  in_use: 'In Use',
  issue: 'Has Issue',
};

function formatUser(pc) {
  const first = pc.current_user__first_name || '';
  const last = pc.current_user__last_name || '';
  const fullName = `${first} ${last}`.trim();
  return fullName || pc.current_user__username || null;
}

async function refreshPcStatuses() {
  let data;
  try {
    const res = await fetch('/labs/pc-status/api/');
    if (!res.ok) return;
    data = await res.json();
  } catch (err) {
    console.error('PC status refresh failed:', err);
    return;
  }

  (data.pcs || []).forEach((pc) => {
    const row = document.querySelector(`tr[data-pc="${pc.pc_id}"]`);
    if (!row) return;

    const pill = row.querySelector('[data-role="status-pill"]');
    if (pill) {
      pill.className = `status-pill pill-${pc.status}`;
      pill.textContent = STATUS_LABELS[pc.status] || pc.status;
    }

    const userCell = row.querySelector('[data-role="current-user"]');
    if (userCell) {
      userCell.textContent = formatUser(pc) || '\u2014';
    }
  });
}

// Only start polling on pages that actually have status pills to update
// (i.e. the PC Status page in its normal, non "day lookup" view).
if (document.querySelector('[data-role="status-pill"]')) {
  setInterval(refreshPcStatuses, 10000);
}
