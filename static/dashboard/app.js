// AIOS Dashboard — app.js

const API = '';
let token = localStorage.getItem('aios_token');
let allRuns = [];
let currentRunId = null;

// ── Auth guard ────────────────────────────────────────────────────────────────
if (!token) { window.location.href = '/dashboard/login-page'; }
document.getElementById('user-label').textContent = localStorage.getItem('aios_user') || '';

function logout() {
  localStorage.removeItem('aios_token');
  localStorage.removeItem('aios_user');
  window.location.href = '/dashboard/login-page';
}

async function apiFetch(path, opts = {}) {
  const res = await fetch(API + path, {
    ...opts,
    headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json', ...(opts.headers || {}) }
  });
  if (res.status === 401) { logout(); return null; }
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  await Promise.all([loadCosts(), loadRuns(), loadCredits()]);
  connectSSE();
}

// ── Costs ─────────────────────────────────────────────────────────────────────
async function loadCosts() {
  const data = await apiFetch('/dashboard/costs');
  if (!data) return;
  const t = data.runs?.totals || {};
  document.getElementById('cost-today').textContent  = fmt$(t.today  || 0);
  document.getElementById('cost-week').textContent   = fmt$(t.week   || 0);
  document.getElementById('cost-month').textContent  = fmt$(t.month  || 0);
  document.getElementById('cost-total').textContent  = fmt$(t.total  || 0);
}

function fmt$(v) { return `USD ${Number(v).toFixed(4)}`; }

// ── Credits ───────────────────────────────────────────────────────────────────
async function loadCredits() {
  const data = await apiFetch('/dashboard/credits');
  if (!data) return;

  const el    = document.getElementById('credits-remaining');
  const bar   = document.getElementById('credits-bar');
  const detail = document.getElementById('credits-detail');
  const sub   = document.getElementById('credits-sub');
  const stats = document.getElementById('credits-stats');

  if (!data.configured) {
    el.textContent = '—';
    detail.textContent = 'Registre a primeira recarga para ativar o monitoramento de saldo.';
    stats.innerHTML = '';
    renderTopupHistory(data.topups || []);
    return;
  }

  const rem = data.estimated_remaining;
  const pct = data.percent_remaining;
  el.textContent = fmt$(rem);
  sub.textContent = `estimado restante (${pct.toFixed(1)}%)`;

  const cls = pct < 10 ? 'danger' : pct < 25 ? 'warn' : '';
  el.className = 'credits-remaining ' + cls;
  bar.className = 'credits-bar ' + cls;
  bar.style.width = Math.min(pct, 100) + '%';
  detail.textContent = `Monitoramento desde ${data.since_date}`;

  stats.innerHTML = `
    <div class="credits-stat-row"><span class="credits-stat-label">Total recarregado</span><span class="credits-stat-value">${fmt$(data.total_topup)}</span></div>
    <div class="credits-stat-row"><span class="credits-stat-label">Gasto (pipeline)</span><span class="credits-stat-value" style="color:var(--orange)">${fmt$(data.pipeline_spent)}</span></div>
    <div class="credits-stat-row"><span class="credits-stat-label">Gasto (projetos ext.)</span><span class="credits-stat-value" style="color:var(--orange)">${fmt$(data.agent_spent)}</span></div>
    <div class="credits-stat-row"><span class="credits-stat-label">Total gasto</span><span class="credits-stat-value" style="color:var(--red)">${fmt$(data.total_spent)}</span></div>
  `;

  renderTopupHistory(data.topups || []);
}

function renderTopupHistory(topups) {
  const el = document.getElementById('topup-history');
  if (!topups.length) { el.innerHTML = ''; return; }
  el.innerHTML = `
    <table class="topup-table">
      <thead><tr><th>Data</th><th>Valor</th><th>Observação</th><th></th></tr></thead>
      <tbody>
        ${topups.map(t => `
          <tr>
            <td>${t.topup_date}</td>
            <td class="amount">${fmt$(t.amount_usd)}</td>
            <td style="color:var(--muted)">${t.notes || '—'}</td>
            <td><button class="del-btn" onclick="deleteTopup(${t.id})">✕</button></td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

function toggleTopupForm() {
  const f = document.getElementById('topup-form');
  f.classList.toggle('hidden');
  if (!f.classList.contains('hidden')) {
    document.getElementById('topup-date').value = new Date().toISOString().slice(0, 10);
    document.getElementById('topup-amount').focus();
  }
}

async function saveTopup() {
  const date   = document.getElementById('topup-date').value;
  const amount = parseFloat(document.getElementById('topup-amount').value);
  const notes  = document.getElementById('topup-notes').value;
  if (!date || !amount || isNaN(amount) || amount <= 0) {
    alert('Informe data e valor válidos.');
    return;
  }
  await apiFetch('/dashboard/credits/topups', {
    method: 'POST',
    body: JSON.stringify({ amount_usd: amount, topup_date: date, notes })
  });
  document.getElementById('topup-amount').value = '';
  document.getElementById('topup-notes').value = '';
  document.getElementById('topup-form').classList.add('hidden');
  await loadCredits();
}

async function deleteTopup(id) {
  if (!confirm('Remover esta recarga?')) return;
  await apiFetch(`/dashboard/credits/topups/${id}`, { method: 'DELETE' });
  await loadCredits();
}

// ── Runs ──────────────────────────────────────────────────────────────────────
async function loadRuns() {
  const data = await apiFetch('/dashboard/runs');
  if (!data) return;
  allRuns = data;
  renderRuns();
  renderKanban();
}

function renderRuns() {
  const filter = document.getElementById('filter-project').value;
  const runs = filter ? allRuns.filter(r => r.project === filter) : allRuns;

  const listEl = document.getElementById('runs-list');
  if (!runs.length) {
    listEl.innerHTML = '<p class="empty-state">Nenhuma execução encontrada.</p>';
  } else {
    listEl.innerHTML = runs.map(runCard).join('');
  }
}

const STAGES_EXPANSAO = ['ceo', 'pm', 'architect', 'dev', 'qa', 'devops', 'marketing'];
const STAGES_CWI = ['meeting-secretary', 'pmo', 'agile-coach', 'product', 'exec-reporting'];
const STAGE_LABELS = {
  ceo: 'CEO', pm: 'PM', architect: 'Arch', dev: 'Dev', qa: 'QA', devops: 'DevOps', marketing: 'Mktg',
  'meeting-secretary': 'Secretary', pmo: 'PMO', 'agile-coach': 'Agile', product: 'Product', 'exec-reporting': 'Exec'
};

// ── Kanban board ─────────────────────────────────────────────────────────────
function renderKanban() {
  const filter = document.getElementById('kanban-filter').value;
  const runs = filter ? allRuns.filter(r => r.project === filter) : allRuns;
  const board = document.getElementById('kanban-board');

  // Build columns: one per stage + "Concluído"
  const cols = [...STAGES_EXPANSAO, '__done__'];
  const colCards = Object.fromEntries(cols.map(c => [c, []]));

  for (const run of runs) {
    const col = _kanbanColumn(run);
    if (colCards[col] !== undefined) colCards[col].push(run);
  }

  board.innerHTML = cols.map(col => {
    const cards = colCards[col];
    const label = col === '__done__' ? '✅ Concluído' : (STAGE_LABELS[col] || col);
    const hasFailed = cards.some(r => r.status === 'failed' && _kanbanColumn(r) === col);
    const hasActive = cards.some(r => r.status === 'running');
    const colCls = hasActive ? 'active' : hasFailed ? 'has-failed' : col === '__done__' ? 'done-col' : '';
    return `
      <div class="kb-col ${colCls}">
        <div class="kb-col-head">
          <div class="kb-col-name">${label}</div>
          <div class="kb-col-count">${cards.length} run${cards.length !== 1 ? 's' : ''}</div>
        </div>
        <div class="kb-col-body">
          ${cards.map(r => _kbCard(r, col)).join('') || '<div style="padding:8px;font-size:.7rem;color:var(--muted);text-align:center">—</div>'}
        </div>
      </div>`;
  }).join('');
}

function _kanbanColumn(run) {
  if (run.status === 'completed') return '__done__';

  // Find the deepest active or failed stage
  const stages = run.stages || [];
  const order = run.pipeline === 'cwi' ? STAGES_CWI : STAGES_EXPANSAO;

  // If running: use current_stage
  if (run.status === 'running' && run.current_stage) return run.current_stage;

  // Failed/paused: find the last non-skipped stage that ran
  for (let i = order.length - 1; i >= 0; i--) {
    const s = stages.find(st => st.stage_name === order[i]);
    if (s && ['running', 'failed', 'completed'].includes(s.status)) return order[i];
  }

  // Fallback: first stage
  return order[0] || 'ceo';
}

function _kbCard(run, col) {
  const cardCls = run.status === 'running' ? 'running'
    : run.status === 'completed' ? 'completed'
    : run.status === 'paused' ? 'paused'
    : 'failed';

  const proj = { expansao: 'Expansão AI', cwi: 'CWI', climate: 'Climate', 'grc-flow': 'GRC Flow' }[run.project] || run.project;
  const shortId = run.run_id.replace('hist-', '').slice(0, 8);
  const elapsed = run.started_at ? _elapsed(new Date(run.started_at), run.completed_at ? new Date(run.completed_at) : new Date()) : '';

  // Find blocker info for failed runs
  let blocker = '';
  if (run.status === 'failed' && run.stages) {
    const failedStage = run.stages.find(s => s.status === 'failed');
    const errMsg = failedStage?.error_msg || run.error_msg || '';
    if (errMsg) blocker = `<div class="kb-card-blocker">⚠ ${errMsg.slice(0, 60)}${errMsg.length > 60 ? '…' : ''}</div>`;
    else if (col !== '__done__') blocker = `<div class="kb-card-blocker">⚠ Parou neste estágio</div>`;
  }

  const gates = (run.gates || []).filter(g => g.decision === 'pending');
  const gateAlert = gates.length ? `<div class="kb-card-blocker" style="color:var(--yellow)">🚦 Gate pendente</div>` : '';

  return `
    <div class="kb-card ${cardCls}" onclick="openRun('${run.run_id}')">
      <div class="kb-card-project">${proj}</div>
      <div class="kb-card-id">${shortId}</div>
      <div class="kb-card-cost">${fmt$(run.cost_usd || 0)}</div>
      <div class="kb-card-time">${elapsed}</div>
      <span class="kb-card-status ${cardCls}">${run.status}</span>
      ${blocker}${gateAlert}
    </div>`;
}

function _elapsed(start, end) {
  const ms = end - start;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s/60)}m`;
  return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`;
}

function stageClass(stages, name, currentStage, runStatus) {
  const s = (stages || []).find(st => st.stage_name === name);
  if (s) {
    if (s.status === 'completed') return 'done';
    if (s.status === 'running')   return 'running';
    if (s.status === 'failed')    return 'failed';
    if (s.status === 'skipped')   return 'skipped';
  }
  if (name === currentStage && runStatus === 'running') return 'running';
  return '';
}

function runCard(run) {
  const stageList = run.pipeline === 'cwi' ? STAGES_CWI : STAGES_EXPANSAO;
  const pills = stageList.map(s => {
    const cls = stageClass(run.stages, s, run.current_stage, run.status);
    return `<span class="stage-pill ${cls}">${STAGE_LABELS[s] || s}</span>`;
  }).join('');
  const started = run.started_at ? new Date(run.started_at).toLocaleString('pt-BR') : '—';
  const projectLabel = { expansao: '🚀 Expansão AI', cwi: '🏢 CWI', climate: '🌦 Climate', 'grc-flow': '📋 GRC Flow' }[run.project] || run.project;
  return `
  <div class="run-card" onclick="openRun('${run.run_id}')">
    <div class="run-card-header">
      <span class="run-title">${projectLabel} — Sprint ${run.extra_context?.match(/W\d+/)?.[0] || run.run_id}</span>
      <span class="badge badge-${run.status}">${run.status}</span>
    </div>
    <div class="pipeline-progress">${pills}</div>
    <div class="run-meta" style="margin-top:8px">
      <span>${started}</span>
      <span class="run-cost">${fmt$(run.cost_usd || 0)}</span>
      <span>${run.run_id}</span>
    </div>
  </div>`;
}

// ── Run detail modal ──────────────────────────────────────────────────────────
async function openRun(runId) {
  currentRunId = runId;
  const run = await apiFetch(`/dashboard/runs/${runId}`);
  if (!run) return;
  renderModal(run);
  document.getElementById('modal-overlay').classList.remove('hidden');
}

function renderModal(run) {
  document.getElementById('modal-title').textContent =
    `${run.project} / ${run.pipeline} — ${run.status}`;
  document.getElementById('modal-run-id').textContent = run.run_id;

  // Kanban
  const stageList = run.pipeline === 'cwi' ? STAGES_CWI : STAGES_EXPANSAO;
  document.getElementById('modal-kanban').innerHTML = stageList.map(s => {
    const st = (run.stages || []).find(x => x.stage_name === s);
    const cls = stageClass(run.stages, s, run.current_stage, run.status);
    const cost = st ? `<div class="kanban-stage-cost">${fmt$(st.cost_usd || 0)}</div>` : '';
    const tokens = st ? `<div class="kanban-tokens">${(st.input_tokens||0).toLocaleString()} ↑ ${(st.output_tokens||0).toLocaleString()} ↓</div>` : '';
    return `
    <div class="kanban-col">
      <div class="kanban-col-header ${cls}">${STAGE_LABELS[s] || s}</div>
      ${cost}${tokens}
    </div>`;
  }).join('');

  // Gates
  const pendingGates = (run.gates || []).filter(g => g.decision === 'pending');
  const gatesSection = document.getElementById('modal-gates');
  if (pendingGates.length > 0) {
    gatesSection.classList.remove('hidden');
    document.getElementById('gates-list').innerHTML = pendingGates.map(g => `
      <div class="gate-item">
        <span class="gate-name">🚦 Gate: ${g.gate_id}</span>
        <div class="gate-actions">
          <button class="btn-approve" onclick="decideGate('${run.run_id}','${g.gate_id}','approved')">Aprovar</button>
          <button class="btn-reject"  onclick="decideGate('${run.run_id}','${g.gate_id}','rejected')">Rejeitar</button>
        </div>
      </div>`).join('');
  } else {
    gatesSection.classList.add('hidden');
  }

  // Costs
  document.getElementById('modal-cost').textContent = fmt$(run.cost_usd || 0);
  const started = run.started_at ? new Date(run.started_at) : null;
  const ended   = run.completed_at ? new Date(run.completed_at) : new Date();
  document.getElementById('modal-duration').textContent =
    started ? formatDuration(ended - started) : '—';
}

function formatDuration(ms) {
  const s = Math.floor(ms / 1000);
  if (s < 60)   return `${s}s`;
  if (s < 3600) return `${Math.floor(s/60)}m ${s%60}s`;
  return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`;
}

async function decideGate(runId, gateId, decision) {
  await apiFetch(`/dashboard/runs/${runId}/gate/${gateId}`, {
    method: 'POST', body: JSON.stringify({decision})
  });
  openRun(runId); // refresh
}

function closeModal(e) {
  if (!e || e.target.id === 'modal-overlay') {
    document.getElementById('modal-overlay').classList.add('hidden');
    currentRunId = null;
  }
}

// ── SSE ───────────────────────────────────────────────────────────────────────
function connectSSE() {
  const es = new EventSource(`/dashboard/stream?token=${token}`);
  es.onmessage = (e) => {
    const evt = JSON.parse(e.data);
    if (evt.type === 'run_update' || evt.type === 'stage_update' || evt.type === 'gate_decision') {
      loadRuns();  // also calls renderKanban()
      loadCosts();
      if (currentRunId === evt.run_id) openRun(evt.run_id);
    }
  };
  es.onerror = () => setTimeout(connectSSE, 5000);
}

// ── Start ─────────────────────────────────────────────────────────────────────
init();
