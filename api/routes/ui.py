"""
GET /ui — Single-page demo dashboard with tabs.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Auto SRE Agent</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           background: #0f1117; color: #e2e8f0; min-height: 100vh; }

    header { background: #1a1d2e; border-bottom: 1px solid #2d3148;
             padding: 16px 32px; display: flex; align-items: center; gap: 12px; }
    header h1 { font-size: 18px; font-weight: 700; color: #fff; }
    .live-badge { background: #22c55e22; color: #22c55e; border: 1px solid #22c55e44;
                  border-radius: 6px; padding: 2px 10px; font-size: 12px; }
    .refresh-info { margin-left: auto; font-size: 12px; color: #475569; }

    /* Tabs */
    .tabs { display: flex; gap: 0; border-bottom: 1px solid #2d3148;
            padding: 0 32px; background: #1a1d2e; }
    .tab { padding: 12px 24px; font-size: 14px; font-weight: 500; cursor: pointer;
           color: #64748b; border-bottom: 2px solid transparent; transition: all .15s;
           user-select: none; }
    .tab:hover { color: #e2e8f0; }
    .tab.active { color: #fff; border-bottom-color: #6366f1; }
    .tab-badge { display: inline-block; background: #f59e0b; color: #000;
                 border-radius: 10px; padding: 0 6px; font-size: 11px;
                 font-weight: 700; margin-left: 6px; vertical-align: middle; }

    main { padding: 24px 32px; }
    .panel { display: none; }
    .panel.active { display: block; }

    .empty { color: #475569; font-size: 14px; padding: 32px;
             border: 1px dashed #2d3148; border-radius: 8px; text-align: center; }

    /* Approval cards */
    .card { background: #1a1d2e; border: 1px solid #2d3148; border-radius: 10px;
            padding: 20px; margin-bottom: 12px; }
    .card.urgent { border-color: #f59e0b55; }
    .card-row { display: flex; justify-content: space-between; align-items: center;
                margin-bottom: 8px; }
    .card h3 { font-size: 15px; font-weight: 600; color: #f59e0b; }
    .pill { display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: 11px; font-weight: 600; text-transform: uppercase; }
    .risk-high   { background: #ef444422; color: #ef4444; }
    .risk-medium { background: #f59e0b22; color: #f59e0b; }
    .risk-low    { background: #22c55e22; color: #22c55e; }
    .meta { font-size: 12px; color: #64748b; }
    .rationale { font-size: 13px; color: #94a3b8; margin: 10px 0 16px; line-height: 1.5; }
    .expires { font-size: 11px; color: #f59e0b99; margin-top: 12px; }
    .btn-row { display: flex; gap: 10px; }
    .btn { padding: 8px 22px; border-radius: 6px; border: none; font-size: 13px;
           font-weight: 600; cursor: pointer; transition: opacity .15s; }
    .btn:hover { opacity: .85; }
    .btn:disabled { opacity: .35; cursor: not-allowed; }
    .btn-approve { background: #22c55e; color: #fff; }
    .btn-reject  { background: #ef4444; color: #fff; }

    /* Incidents table */
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; padding: 8px 14px; color: #64748b; font-weight: 500;
         border-bottom: 1px solid #2d3148; white-space: nowrap; }
    td { padding: 10px 14px; border-bottom: 1px solid #1a1d2e; vertical-align: middle; }
    tr:hover td { background: #1a1d2e55; }

    .status-resolved          { background: #22c55e22; color: #22c55e; }
    .status-awaiting_approval { background: #f59e0b22; color: #f59e0b; }
    .status-open              { background: #3b82f622; color: #3b82f6; }
    .status-failed            { background: #ef444422; color: #ef4444; }
    .status-executing         { background: #a855f722; color: #a855f7; }
    .status-planned           { background: #06b6d422; color: #06b6d4; }

    .mono { font-family: monospace; font-size: 11px; color: #475569; }
    .action-cell { font-size: 12px; }
    .action-cell .resource { color: #64748b; }

    .toast { position: fixed; bottom: 24px; right: 24px; background: #1e2235;
             border: 1px solid #2d3148; border-radius: 8px; padding: 12px 20px;
             font-size: 13px; opacity: 0; transition: opacity .3s;
             pointer-events: none; z-index: 999; min-width: 240px; }
    .toast.show { opacity: 1; }
    .toast.ok  { border-color: #22c55e66; color: #22c55e; }
    .toast.err { border-color: #ef444466; color: #ef4444; }
  </style>
</head>
<body>

<header>
  <span style="font-size:20px">🤖</span>
  <h1>Auto SRE Agent</h1>
  <span class="live-badge">● Live</span>
  <span class="refresh-info" id="refreshInfo">Refreshing…</span>
</header>

<div class="tabs">
  <div class="tab active" onclick="switchTab('approvals')">
    Pending Approvals <span class="tab-badge" id="approvalCount" style="display:none">0</span>
  </div>
  <div class="tab" onclick="switchTab('incidents')">Incidents</div>
</div>

<main>
  <!-- APPROVALS TAB -->
  <div class="panel active" id="panel-approvals">
    <div id="approvalsList"><div class="empty">Loading…</div></div>
  </div>

  <!-- INCIDENTS TAB -->
  <div class="panel" id="panel-incidents">
    <div id="incidentsList"><div class="empty">Loading…</div></div>
  </div>
</main>

<div class="toast" id="toast"></div>

<script>
  const API_KEY = 'change-me';
  const H = { 'X-API-Key': API_KEY, 'Content-Type': 'application/json' };

  function switchTab(name) {
    document.querySelectorAll('.tab').forEach((t, i) => {
      t.classList.toggle('active', ['approvals','incidents'][i] === name);
    });
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.getElementById(`panel-${name}`).classList.add('active');
  }

  function toast(msg, type='ok') {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.className = `toast show ${type}`;
    setTimeout(() => el.className = 'toast', 3000);
  }

  function timeAgo(iso) {
    const s = Math.floor((Date.now() - new Date(iso)) / 1000);
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s/60)}m ago`;
    return `${Math.floor(s/3600)}h ago`;
  }

  function timeLeft(iso) {
    const s = Math.floor((new Date(iso) - Date.now()) / 1000);
    if (s <= 0) return '⚠ expired';
    if (s < 60) return `⏱ ${s}s left`;
    return `⏱ ${Math.floor(s/60)}m ${s%60}s left`;
  }

  async function decide(approvalId, approved) {
    document.querySelectorAll(`[data-id="${approvalId}"]`).forEach(b => b.disabled = true);
    try {
      const res = await fetch(`/approvals/${approvalId}`, {
        method: 'POST', headers: H,
        body: JSON.stringify({ approved, reviewer: 'dashboard',
                               notes: approved ? 'Approved via UI' : 'Rejected via UI' })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed');
      toast(approved ? '✓ Approved — agent resuming' : '✗ Rejected', approved ? 'ok' : 'err');
      setTimeout(refresh, 1200);
    } catch(e) {
      toast(`Error: ${e.message}`, 'err');
      document.querySelectorAll(`[data-id="${approvalId}"]`).forEach(b => b.disabled = false);
    }
  }

  async function loadApprovals() {
    const res = await fetch('/approvals/pending', { headers: H });
    const data = await res.json();
    const badge = document.getElementById('approvalCount');
    if (data.length) {
      badge.style.display = 'inline-block';
      badge.textContent = data.length;
    } else {
      badge.style.display = 'none';
    }
    const el = document.getElementById('approvalsList');
    if (!data.length) {
      el.innerHTML = '<div class="empty">No pending approvals — all clear ✓</div>';
      return;
    }
    el.innerHTML = data.map(a => `
      <div class="card urgent">
        <div class="card-row">
          <h3>${a.action_type.replace(/_/g,' ')}</h3>
          <span class="pill risk-${a.risk_level}">${a.risk_level} risk</span>
        </div>
        <div class="meta">
          <b>${a.target_resource}</b> &nbsp;·&nbsp; ${a.target_namespace} &nbsp;·&nbsp; ${timeAgo(a.created_at)}
        </div>
        <div class="rationale">${a.rationale}</div>
        <div class="btn-row">
          <button class="btn btn-approve" data-id="${a.approval_id}"
            onclick="decide('${a.approval_id}', true)">✓ Approve</button>
          <button class="btn btn-reject" data-id="${a.approval_id}"
            onclick="decide('${a.approval_id}', false)">✗ Reject</button>
        </div>
        <div class="expires">${timeLeft(a.expires_at)}</div>
      </div>`).join('');
  }

  async function loadIncidents() {
    const res = await fetch('/incidents/?limit=30', { headers: H });
    const data = await res.json();
    const el = document.getElementById('incidentsList');
    if (!data.length) {
      el.innerHTML = '<div class="empty">No incidents recorded yet</div>';
      return;
    }
    el.innerHTML = `<table>
      <thead><tr>
        <th>Time</th><th>Alert</th><th>Namespace</th>
        <th>Status</th><th>Action</th><th>Result</th>
      </tr></thead>
      <tbody>${data.map(i => `<tr>
        <td class="meta" style="white-space:nowrap">${timeAgo(i.created_at)}</td>
        <td><b>${i.alert.alert_name}</b></td>
        <td class="meta">${i.alert.namespace}</td>
        <td><span class="pill status-${i.status}">${i.status.replace(/_/g,' ')}</span></td>
        <td class="action-cell">${i.proposed_action
          ? `${i.proposed_action.action_type.replace(/_/g,' ')}<br>
             <span class="resource">${i.proposed_action.target_resource}</span>`
          : '<span class="meta">—</span>'}</td>
        <td class="meta">${i.action_result
          ? (i.action_result.success ? '✓ success' : '✗ failed')
          : '—'}</td>
      </tr>`).join('')}</tbody>
    </table>`;
  }

  async function refresh() {
    try {
      await Promise.all([loadApprovals(), loadIncidents()]);
    } catch(e) { console.error(e); }
    document.getElementById('refreshInfo').textContent =
      `Updated ${new Date().toLocaleTimeString()} · auto-refresh 5s`;
  }

  refresh();
  setInterval(refresh, 5000);
</script>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(content=_HTML)
