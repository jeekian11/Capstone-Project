// static/js/dashboard.js
// Auto-refreshes each PC's status pill on the PC Status page without a full
// page reload. The server already keeps pinging PCs in the background (see
// labs/network.py + labs/apps.py); this just polls the JSON snapshot of
// that data and updates the DOM to match, every 10 seconds.

const STATUS_LABELS = {
  online: 'Available',
  offline: 'Offline',
  in_use: 'In Use',
  maintenance: 'Under Maintenance',
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
      pill.className = `ld-pill ${pc.status}`;
      pill.textContent = STATUS_LABELS[pc.status] || pc.status;
    }

    const userCell = row.querySelector('[data-role="current-user"]');
    if (userCell) {
      userCell.textContent = formatUser(pc) || '\u2014';
    }
  });

  updateLastUpdatedStamp();
}

function updateLastUpdatedStamp() {
  const el = document.getElementById('ldLastUpdated');
  if (!el) return;
  el.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// Only start polling on pages that actually have status pills to update
// (i.e. the PC Status page in its normal, non "day lookup" view).
if (document.querySelector('[data-role="status-pill"]')) {
  updateLastUpdatedStamp();
  setInterval(refreshPcStatuses, 10000);
}
