// static/js/dashboard.js
setInterval(async () => {
  const res = await fetch('/api/pc-status/');
  const { pcs } = await res.json();
  pcs.forEach(pc => {
    const node = document.querySelector(`[data-pc="${pc.pc_id}"]`);
    if (node) node.className = `pc-node ${pc.status}`;
  });
}, 10000);  // refresh every 10 seconds