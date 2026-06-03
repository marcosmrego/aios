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
  await Promise.all([loadCosts(), loadRuns()]);
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

// ── Runs ──────────────────────────────────────────────────────────────────────
async function loadRuns() {
  const data = await apiFetch('/dashboard/runs');
  if (!data) return;
  allRuns = data;
  renderRuns();
}

function renderRuns() {
  const filter = document.getElementById('filter-project').value;
  const runs = filter ? allRuns.filter(r => r.project === filter) : allRuns;
  const active = runs.filter(r => r.status === 'running');
  const recent = runs.filter(r => r.status !== 'running');

  // Active
  const activeEl = document.getElementById('active-runs');
  if (active.length === 0) {
    activeEl.innerHTML = '<p class="empty-state">Nenhuma execução ativa no momento.</p>';
  } else {
    activeEl.innerHTML = active.map(runCard).join('');
  }

  // Recent
  const listEl = document.getElementById('runs-list');
  if (recent.length === 0) {
    listEl.innerHTML = '<p class="empty-state">Nenhuma execução encontrada.</p>';
  } else {
    listEl.innerHTML = recent.map(runCard).join('');
  }
}

const STAGES_EXPANSAO = ['ceo', 'pm', 'architect', 'dev', 'qa', 'devops', 'marketing'];
const STAGES_CWI = ['meeting-secretary', 'pmo', 'agile-coach', 'product', 'exec-reporting'];
const STAGE_LABELS = {
  ceo: 'CEO', pm: 'PM', architect: 'Arch', dev: 'Dev', qa: 'QA', devops: 'DevOps', marketing: 'Mktg',
  'meeting-secretary': 'Secretary', pmo: 'PMO', 'agile-coach': 'Agile', product: 'Product', 'exec-reporting': 'Exec'
};

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
      loadRuns();
      loadCosts();
      if (currentRunId === evt.run_id) openRun(evt.run_id);
    }
  };
  es.onerror = () => setTimeout(connectSSE, 5000);
}

// ── Start ─────────────────────────────────────────────────────────────────────
init();
