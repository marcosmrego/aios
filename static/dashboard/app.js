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
  _initTooltip();
  await Promise.all([loadCosts(), loadRuns(), loadAgentsToday()]);
  loadStories();
  connectSSE();
}

// ── Agents today ──────────────────────────────────────────────────────────────
let _agentsConfig = {};

async function loadAgentsToday() {
  const [today, cfg] = await Promise.all([
    apiFetch('/dashboard/agents/today'),
    apiFetch('/dashboard/agents/config'),
  ]);
  if (cfg) {
    _agentsConfig = {};
    [...(cfg.expansao || []), ...(cfg.cwi || [])].forEach(a => {
      _agentsConfig[a.agent.toLowerCase()] = a.model;
    });
  }
  renderAgentsToday(today || [], cfg || {});
}

const MODEL_SHORT = {
  'claude-sonnet-4-6':         'Sonnet 4.6',
  'claude-opus-4-8':           'Opus 4.8',
  'claude-haiku-4-5-20251001': 'Haiku 4.5',
};
function shortModel(m) { return MODEL_SHORT[m] || m; }

function renderAgentsToday(rows, cfg) {
  const bar = document.getElementById('agents-today-bar');
  if (!bar) return;

  // Build config lookup: "QA Agent" → model from settings
  const cfgLookup = {};
  [...(cfg.expansao || []), ...(cfg.cwi || [])].forEach(a => {
    cfgLookup[a.agent.toLowerCase()] = a.model;
  });

  // Merge activity rows with config to show all configured agents
  const allAgents = [...(cfg.expansao || []), ...(cfg.cwi || [])];
  const activityMap = {};
  (rows || []).forEach(r => {
    const key = r.agent_name.replace(/ agent$/i, '').toLowerCase();
    activityMap[key] = r;
  });

  if (!rows.length && !allAgents.length) {
    bar.innerHTML = '';
    return;
  }

  // Today's activity table (only agents that ran today)
  const activityHtml = rows.length ? `
    <div class="agents-section">
      <div class="agents-section-title">Agentes — hoje</div>
      <table class="agents-table">
        <thead><tr><th>Agente</th><th>Modelo</th><th>Chamadas</th><th>Tokens ↑↓</th><th>Custo</th></tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td>${r.agent_name}</td>
              <td><span class="model-badge">${shortModel(r.model)}</span></td>
              <td style="color:var(--muted)">${r.runs}</td>
              <td style="font-family:monospace;font-size:.78rem;color:var(--muted)">${fmtK(r.input_tokens)}↑ ${fmtK(r.output_tokens)}↓</td>
              <td style="color:var(--orange);font-weight:600">${fmt$(r.cost_usd)}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>` : '';

  // Configured models card (always show)
  const configHtml = allAgents.length ? `
    <div class="agents-section">
      <div class="agents-section-title">Modelos configurados</div>
      <div class="model-config-grid">
        ${allAgents.map(a => `
          <div class="model-config-item">
            <span class="model-config-agent">${a.agent}</span>
            <span class="model-badge">${shortModel(a.model)}</span>
          </div>`).join('')}
      </div>
    </div>` : '';

  bar.innerHTML = `<div class="agents-bar-inner">${activityHtml}${configHtml}</div>`;
}

function fmtK(n) {
  if (n >= 1000000) return (n/1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n/1000).toFixed(0) + 'k';
  return n;
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

function fmt$(v) { return `USD ${Number(v).toFixed(2)}`; }

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

// ── Run metadata helpers ──────────────────────────────────────────────────────
function _sprint(run) {
  if (run.started_at) {
    const d = new Date(run.started_at);
    const jan4 = new Date(d.getFullYear(), 0, 4);
    const week = Math.ceil(((d - jan4) / 86400000 + jan4.getDay() + 1) / 7);
    return `${d.getFullYear()}-W${String(week).padStart(2,'0')}`;
  }
  return run.run_id.slice(-6);
}

function _title(run) {
  const ctx = run.extra_context || '';
  // Remove long CLIMA context — use first sentence
  const first = ctx.split('—')[0].split(':')[0].trim();
  return first.length > 2 ? first.slice(0, 45) : run.run_id;
}

function _storyInfo(run) {
  const devStage = (run.stages || []).find(s => s.stage_name === 'dev');
  if (!devStage) return null;
  const summary = devStage.output_summary || '';
  const m = summary.match(/(\d+)\s+stori/i);
  return m ? `${m[1]} US` : null;
}

function _kbCard(run, col) {
  const cardCls = run.status === 'running' ? 'running'
    : run.status === 'completed' ? 'completed'
    : run.status === 'paused' ? 'paused'
    : 'failed';

  const proj = { expansao: 'Expansão AI', cwi: 'CWI', climate: 'Climate', 'grc-flow': 'GRC Flow' }[run.project] || run.project;
  const sprint = _sprint(run);
  const title  = _title(run);
  const stories = _storyInfo(run);
  const elapsed = run.started_at ? _elapsed(new Date(run.started_at), run.completed_at ? new Date(run.completed_at) : new Date()) : '';

  // Blocker info for failed runs
  let blocker = '';
  if (run.status === 'failed') {
    const failedStage = (run.stages || []).find(s => s.status === 'failed');
    const errMsg = failedStage?.error_msg || run.error_msg || '';
    if (errMsg) blocker = `<div class="kb-card-blocker">⚠ ${errMsg.slice(0, 55)}${errMsg.length > 55 ? '…' : ''}</div>`;
    else if (col !== '__done__') blocker = `<div class="kb-card-blocker">⚠ Parou neste estágio</div>`;
  }

  const gates = (run.gates || []).filter(g => g.decision === 'pending');
  const gateAlert = gates.length ? `<div class="kb-card-blocker" style="color:var(--yellow)">🚦 Gate aguardando aprovação</div>` : '';

  return `
    <div class="kb-card ${cardCls}" onclick="openRun('${run.run_id}')">
      <div class="kb-card-sprint">${proj} · ${sprint}</div>
      <div class="kb-card-title">${title}</div>
      <div class="kb-card-meta">
        <span class="kb-card-cost">${fmt$(run.cost_usd || 0)}</span>
        ${stories ? `<span class="kb-card-stories">${stories}</span>` : ''}
        <span class="kb-card-time">${elapsed}</span>
      </div>
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
  const proj = { expansao: 'Expansão AI', cwi: 'CWI', climate: 'Climate', 'grc-flow': 'GRC Flow' }[run.project] || run.project;
  const sprint = _sprint(run);
  document.getElementById('modal-title').textContent = `${proj} — ${sprint}`;
  document.getElementById('modal-run-id').textContent = run.run_id;

  // Load QA results if QA stage has run
  const hasQA = (run.stages || []).some(s => s.stage_name === 'qa' && ['completed','running'].includes(s.status));
  if (hasQA) loadModalQAStories(sprint);
  else document.getElementById('modal-qa-section').classList.add('hidden');

  const ctx = run.extra_context || '';
  const ctxEl = document.getElementById('modal-context');
  if (ctxEl) ctxEl.textContent = ctx || '—';

  const stageList = run.pipeline === 'cwi' ? STAGES_CWI : STAGES_EXPANSAO;
  document.getElementById('modal-kanban').innerHTML = `
    <table class="modal-stages-table">
      <thead><tr><th>Agente</th><th>Status</th><th>Stories / Resumo</th><th>Custo</th><th>Tokens</th></tr></thead>
      <tbody>
        ${stageList.map(s => {
          const st = (run.stages || []).find(x => x.stage_name === s);
          const cls = stageClass(run.stages, s, run.current_stage, run.status);
          const statusBadge = st ? `<span class="badge badge-${st.status === 'completed' ? 'completed' : st.status === 'running' ? 'running' : st.status === 'failed' ? 'failed' : 'paused'}">${st.status}</span>` : '<span style="color:var(--muted)">—</span>';
          const summary = st?.output_summary || '—';
          const cost = st ? fmt$(st.cost_usd || 0) : '—';
          const tokens = st ? `${(st.input_tokens||0).toLocaleString()}↑ ${(st.output_tokens||0).toLocaleString()}↓` : '—';
          const rowCls = cls === 'failed' ? 'style="background:rgba(248,81,73,.04)"' : cls === 'running' ? 'style="background:rgba(88,166,255,.04)"' : '';
          return `<tr ${rowCls}><td><strong>${STAGE_LABELS[s]||s}</strong></td><td>${statusBadge}</td><td style="max-width:240px;font-size:.8rem;color:var(--muted)">${summary}</td><td style="font-family:monospace;font-size:.8rem;color:var(--orange)">${cost}</td><td style="font-family:monospace;font-size:.75rem;color:var(--muted)">${tokens}</td></tr>`;
        }).join('')}
      </tbody>
    </table>`;

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

  // Footer
  document.getElementById('modal-cost').textContent = fmt$(run.cost_usd || 0);
  const started = run.started_at ? new Date(run.started_at) : null;
  const ended   = run.completed_at ? new Date(run.completed_at) : new Date();
  document.getElementById('modal-duration').textContent = started ? formatDuration(ended - started) : '—';
}

function formatDuration(ms) {
  const s = Math.floor(ms / 1000);
  if (s < 60)   return `${s}s`;
  if (s < 3600) return `${Math.floor(s/60)}m ${s%60}s`;
  return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`;
}

// ── QA results in run modal ───────────────────────────────────────────────────
async function loadModalQAStories(sprint) {
  const sec = document.getElementById('modal-qa-section');
  const list = document.getElementById('modal-qa-list');
  if (!sprint) { sec.classList.add('hidden'); return; }

  const stories = await apiFetch(`/dashboard/stories?sprint=${sprint}`);
  if (!stories || !stories.length) { sec.classList.add('hidden'); return; }

  const qaStories = stories.filter(s => s.qa_result);
  if (!qaStories.length) { sec.classList.add('hidden'); return; }

  const VERDICT_LABEL = {
    deploy:               { label: 'Deploy',            cls: 'completed' },
    deploy_with_caveats:  { label: 'Deploy c/ ressalvas', cls: 'paused'    },
    fix_required:         { label: 'Corrigir',           cls: 'failed'     },
    block_deploy:         { label: 'Bloqueado',          cls: 'failed'     },
  };

  list.innerHTML = `
    <table class="modal-stages-table">
      <thead><tr><th>Story</th><th>Título</th><th>Resultado</th><th>Notas</th></tr></thead>
      <tbody>
        ${qaStories.map(s => {
          const v = VERDICT_LABEL[s.qa_result] || { label: s.qa_result, cls: '' };
          const rowCls = s.qa_result === 'fix_required' || s.qa_result === 'block_deploy'
            ? 'style="background:rgba(248,81,73,.04)"' : '';
          return `<tr ${rowCls}>
            <td><strong>${s.story_id}</strong></td>
            <td style="font-size:.8rem;color:var(--muted);max-width:180px">${(s.title||'').slice(0,50)}</td>
            <td><span class="badge badge-${v.cls}">${v.label}</span></td>
            <td style="font-size:.75rem;color:var(--muted);max-width:220px">${s.qa_notes || '—'}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
  sec.classList.remove('hidden');
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
    if (evt.type === 'run_update' || evt.type === 'stage_update' || evt.type === 'gate_decision' || evt.type === 'gate_pending') {
      loadRuns();  // also calls renderKanban()
      loadCosts();
      loadAgentsToday();
      if (currentRunId === evt.run_id) openRun(evt.run_id);
    }
  };
  es.onerror = () => setTimeout(connectSSE, 5000);
}

// ── Tabs ──────────────────────────────────────────────────────────────────────
const TABS = ['stories', 'runs', 'history', 'credits'];

function switchTab(tab) {
  TABS.forEach(t => {
    document.getElementById(`panel-${t}`).classList.toggle('hidden', t !== tab);
    document.getElementById(`tab-${t}`).classList.toggle('active', t === tab);
  });
  if (tab === 'stories') loadStories();
  if (tab === 'credits') loadCredits();
  if (tab === 'history') renderRuns();
  if (tab === 'runs')    renderKanban();
}

// ── Stories kanban ────────────────────────────────────────────────────────────
const STORY_COLS = [
  { key: 'backlog',       label: '📋 Backlog',         cls: '' },
  { key: 'dev',           label: '💻 Dev',              cls: '' },
  { key: 'qa',            label: '🔍 QA',               cls: '' },
  { key: 'qa_approved',   label: '✅ QA Aprovado',      cls: 'done-col' },
  { key: 'qa_rejected',   label: '❌ QA Reprovado',     cls: 'has-failed' },
  { key: 'deploy_ready',  label: '🚀 Fila de Deploy',   cls: 'active' },
  { key: 'deployed',      label: '🟢 Deployed',         cls: '' },
  { key: 'done',          label: '🏁 Concluído',        cls: 'done-col' },
];

let _allStories = [];

async function loadStories() {
  // Load sprints into selector (once)
  const sprintSel = document.getElementById('story-sprint-filter');
  if (sprintSel.options.length <= 1) {
    const sprints = await apiFetch('/dashboard/stories/sprints');
    if (sprints && sprints.length) {
      sprintSel.innerHTML = `<option value="">Todos</option>` +
        sprints.map(s => `<option value="${s}">${s}</option>`).join('');
      if (sprints.length) sprintSel.value = sprints[0]; // default to latest
    }
  }

  const sprint  = sprintSel.value;
  const url = sprint ? `/dashboard/stories?sprint=${sprint}` : '/dashboard/stories';
  const stories = await apiFetch(url);
  if (!stories) return;
  _allStories = stories;
  updateEpicFilter(stories);
  renderStoriesKanban(filterStories(stories));
}

function filterStories(stories) {
  const project = document.getElementById('filter-story-project').value;
  const epic    = document.getElementById('filter-story-epic').value;
  return stories.filter(s =>
    (!project || s.project === project) &&
    (!epic    || s.epic_id === epic)
  );
}

function updateEpicFilter(stories) {
  const project = document.getElementById('filter-story-project').value;
  const epics = [...new Map(
    stories
      .filter(s => !project || s.project === project)
      .map(s => [s.epic_id, s.epic_title || s.epic_id])
  ).entries()];

  const sel = document.getElementById('filter-story-epic');
  const current = sel.value;
  sel.innerHTML = `<option value="">Todos os épicos</option>` +
    epics.map(([id, title]) => `<option value="${id}">${id} — ${title}</option>`).join('');
  if (epics.find(([id]) => id === current)) sel.value = current;
}

function onStoryFilterChange() {
  updateEpicFilter(_allStories);
  renderStoriesKanban(filterStories(_allStories));
}

function renderStoriesKanban(stories) {
  const board = document.getElementById('stories-board');
  const byStatus = {};
  STORY_COLS.forEach(c => byStatus[c.key] = []);
  stories.forEach(s => {
    const col = byStatus[s.status] ? s.status : 'backlog';
    byStatus[col].push(s);
  });

  board.innerHTML = STORY_COLS.map(col => {
    const cards = byStatus[col.key] || [];
    return `
      <div class="kb-col ${col.cls}">
        <div class="kb-col-head">
          <div class="kb-col-name">${col.label}</div>
          <div class="kb-col-count">${cards.length} US</div>
        </div>
        <div class="kb-col-body">
          ${cards.map(storyCard).join('') || '<div style="padding:8px;font-size:.7rem;color:var(--muted);text-align:center">—</div>'}
        </div>
      </div>`;
  }).join('');
}

// ── Story tooltip ─────────────────────────────────────────────────────────────
const _storyMap = new Map();   // story_id → story object
const _tt = { el: null };

const STATUS_LABELS = {
  backlog: 'Backlog', dev: 'Em desenvolvimento', qa: 'Em QA',
  qa_approved: 'QA Aprovado', qa_rejected: 'QA Reprovado',
  deploy_ready: 'Fila de Deploy', deployed: 'Deploy realizado', done: 'Concluído'
};

function _initTooltip() {
  _tt.el = document.getElementById('story-tooltip');

  document.addEventListener('mousemove', (e) => {
    const t = _tt.el;
    if (!t || !t.classList.contains('visible')) return;
    const m = 16, w = 300;
    let x = e.clientX + m;
    let y = e.clientY + m;
    if (x + w + m > window.innerWidth)  x = e.clientX - w - m;
    if (y + t.offsetHeight + m > window.innerHeight) y = e.clientY - t.offsetHeight - m;
    t.style.left = x + 'px';
    t.style.top  = y + 'px';
  });

  // Delegate hover events on stories-board
  document.addEventListener('mouseover', (e) => {
    const card = e.target.closest('[data-story-id]');
    if (!card) return;
    const s = _storyMap.get(card.dataset.storyId);
    if (s) _showTooltip(e, s);
  });

  document.addEventListener('mouseout', (e) => {
    const card = e.target.closest('[data-story-id]');
    if (!card) return;
    const rel = e.relatedTarget;
    if (!rel || !card.contains(rel)) _hideTooltip();
  });
}

function _showTooltip(e, s) {
  const t = _tt.el;
  if (!t) return;
  const cls = `story-tooltip-status-${s.status}`;
  const rows = [
    ['Projeto',  s.project  || '—'],
    ['Épico',    `${s.epic_id || '—'} ${s.epic_title ? '· ' + s.epic_title.slice(0,28) : ''}`],
    ['Sprint',   s.sprint   || '—'],
    ['Status',   `<span class="${cls}">${STATUS_LABELS[s.status] || s.status}</span>`],
    ['Arquivos', s.dev_files > 0 ? `${s.dev_files} gerados` : '—'],
    ['QA',       s.qa_result || '—'],
  ];

  // Format qa_notes as bullet list: "[CRITICAL] desc; [MEDIUM] desc" → bullets
  let qaNotesHtml = '';
  if (s.qa_notes) {
    const bullets = s.qa_notes.split(';').map(n => n.trim()).filter(Boolean).map(n => {
      const cls = n.startsWith('[CRITICAL]') ? 'color:var(--red)' : n.startsWith('[HIGH]') ? 'color:var(--orange)' : 'color:var(--muted)';
      return `<div style="font-size:.72rem;${cls};padding:1px 0">• ${n}</div>`;
    }).join('');
    if (bullets) qaNotesHtml = `<div style="margin-top:6px;border-top:1px solid rgba(255,255,255,.08);padding-top:6px">${bullets}</div>`;
  }

  t.innerHTML = `
    <div class="story-tooltip-id">${s.story_id}</div>
    <div class="story-tooltip-title">${s.title}</div>
    ${rows.map(([l, v]) => `
      <div class="story-tooltip-row">
        <span class="story-tooltip-label">${l}</span>
        <span class="story-tooltip-value">${v}</span>
      </div>`).join('')}
    ${qaNotesHtml}`;
  t.style.left = (e.clientX + 16) + 'px';
  t.style.top  = (e.clientY + 16) + 'px';
  t.classList.add('visible');
}

function _hideTooltip() {
  if (_tt.el) _tt.el.classList.remove('visible');
}

function storyCard(s) {
  // Register story in map for tooltip lookup
  _storyMap.set(s.story_id, s);

  const statusCls = {
    backlog: '', dev: 'running', qa: 'running',
    qa_approved: 'completed', qa_rejected: 'failed',
    deploy_ready: 'running', deployed: 'completed', done: 'completed'
  }[s.status] || '';

  const epicBadge  = s.epic_id  ? `<div class="kb-card-sprint">${s.epic_id}</div>` : '';
  const filesBadge = s.dev_files > 0 ? `<span class="kb-card-stories">${s.dev_files} arq.</span>` : '';
  const qaNote     = s.qa_notes ? `<div class="kb-card-blocker">${s.qa_notes.slice(0,55)}${s.qa_notes.length > 55 ? '…' : ''}</div>` : '';

  return `
    <div class="kb-card ${statusCls}" data-story-id="${s.story_id}">
      ${epicBadge}
      <div class="kb-card-title">${s.story_id} — ${s.title.slice(0,38)}${s.title.length > 38 ? '…' : ''}</div>
      <div class="kb-card-meta">${filesBadge}</div>
      <span class="kb-card-status ${statusCls}">${s.status.replace('_',' ')}</span>
      ${qaNote}
    </div>`;
}

// ── Start ─────────────────────────────────────────────────────────────────────
init();
