'use strict';

// ── State ──────────────────────────────────────────────────────────
let currentDays = 7;
let viewMode = 'per_video';
let cachedTimeseries = null;
let cachedVideos = null;
let sortCol = 'total_views';
let sortDir = 'desc';

// ── Plotly dark config ─────────────────────────────────────────────
const PLOTLY_LAYOUT_BASE = {
  paper_bgcolor: '#161b22',
  plot_bgcolor:  '#161b22',
  font: { family: 'Inter, system-ui, sans-serif', color: '#8b949e', size: 12 },
  margin: { t: 8, r: 16, b: 36, l: 48 },
  xaxis: {
    gridcolor: '#21262d',
    linecolor: '#30363d',
    tickcolor: '#30363d',
    tickfont: { size: 11 },
    zeroline: false,
  },
  yaxis: {
    gridcolor: '#21262d',
    linecolor: '#30363d',
    tickcolor: '#30363d',
    tickfont: { size: 11 },
    zeroline: false,
  },
  legend: {
    bgcolor: 'rgba(22,27,34,0.9)',
    bordercolor: '#30363d',
    borderwidth: 1,
    font: { size: 11 },
    orientation: 'h',
    y: -0.22,
  },
  hovermode: 'x unified',
  hoverlabel: {
    bgcolor: '#1c2128',
    bordercolor: '#30363d',
    font: { family: 'Inter, system-ui, sans-serif', size: 12, color: '#e6edf3' },
  },
};

const PLOTLY_CONFIG = { displayModeBar: false, responsive: true };

// YouTube red + a palette of distinct muted colors
const PALETTE = [
  '#ff3c3c', '#58a6ff', '#3fb950', '#d29922', '#bc8cff',
  '#e3b341', '#79c0ff', '#56d364', '#f78166', '#d2a8ff',
];

// ── Bootstrap ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.period-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentDays = parseInt(btn.dataset.days, 10);
      loadAll();
    });
  });
  loadAll();
});

async function loadAll() {
  await Promise.all([loadKpis(), loadTimeseries(), loadVideos()]);
}

// ── KPIs ───────────────────────────────────────────────────────────
async function loadKpis() {
  try {
    const data = await apiFetch(`/youtube/api/kpis?days=${currentDays}`);
    document.getElementById('kpi-views').textContent    = fmtNum(data.total_views);
    document.getElementById('kpi-hours').textContent    = fmtNum(data.total_hours) + ' h';
    document.getElementById('kpi-subs').textContent     = fmtNum(data.total_subs);
    document.getElementById('kpi-duration').textContent = fmtDuration(data.avg_duration_s);
    document.getElementById('kpi-pct').textContent      = data.avg_pct.toFixed(1) + '%';

    const pd = data.period;
    document.getElementById('kpi-views-sub').textContent    = `${pd.start} → ${pd.end}`;
    document.getElementById('kpi-hours-sub').textContent    = `${(data.total_hours * 60).toFixed(0)} min total`;
    document.getElementById('kpi-subs-sub').textContent     = `${data.video_count} vídeos no canal`;
    document.getElementById('kpi-duration-sub').textContent = 'por visualização';
    document.getElementById('kpi-pct-sub').textContent      = 'do vídeo visto';

    if (data.last_synced) {
      const dt = new Date(data.last_synced);
      document.getElementById('last-synced').textContent = `Atualizado ${dt.toLocaleDateString('pt-BR')} ${dt.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
    }
  } catch (e) {
    console.error('kpis error', e);
  }
}

// ── Time series ────────────────────────────────────────────────────
async function loadTimeseries() {
  try {
    const data = await apiFetch(`/youtube/api/timeseries?days=${currentDays}`);
    cachedTimeseries = data;
    renderViewsChart(data);
    renderHoursChart(data);
    renderSubsChart(data);
  } catch (e) {
    console.error('timeseries error', e);
  }
}

function setViewMode(mode) {
  viewMode = mode;
  document.getElementById('toggle-per-video').classList.toggle('active', mode === 'per_video');
  document.getElementById('toggle-total').classList.toggle('active', mode === 'total');
  if (cachedTimeseries) renderViewsChart(cachedTimeseries);
}

function renderViewsChart(data) {
  const el = document.getElementById('chart-views');
  const layout = {
    ...PLOTLY_LAYOUT_BASE,
    yaxis: { ...PLOTLY_LAYOUT_BASE.yaxis, title: { text: 'Views', font: { size: 11 } } },
  };

  if (viewMode === 'total') {
    const traces = [{
      x: data.totals.map(r => r.date),
      y: data.totals.map(r => Number(r.views)),
      type: 'scatter',
      mode: 'lines',
      name: 'Canal total',
      line: { color: '#ff3c3c', width: 2 },
      fill: 'tozeroy',
      fillcolor: 'rgba(255,60,60,0.08)',
    }];
    Plotly.react(el, traces, { ...layout, showlegend: false }, PLOTLY_CONFIG);
    return;
  }

  // group per_video
  const byVideo = {};
  data.per_video.forEach(r => {
    if (!byVideo[r.video_id]) byVideo[r.video_id] = { title: r.title, dates: [], views: [] };
    byVideo[r.video_id].dates.push(r.date);
    byVideo[r.video_id].views.push(Number(r.views));
  });

  const entries = Object.entries(byVideo).sort(
    (a, b) => b[1].views.reduce((s, v) => s + v, 0) - a[1].views.reduce((s, v) => s + v, 0)
  );

  const traces = entries.map(([, info], i) => ({
    x: info.dates,
    y: info.views,
    type: 'scatter',
    mode: 'lines',
    name: truncate(info.title, 32),
    line: { color: PALETTE[i % PALETTE.length], width: 2 },
  }));

  const showLegend = entries.length > 1;
  Plotly.react(el, traces, { ...layout, showlegend: showLegend }, PLOTLY_CONFIG);
}

function renderHoursChart(data) {
  const el = document.getElementById('chart-hours');
  const layout = {
    ...PLOTLY_LAYOUT_BASE,
    margin: { t: 8, r: 16, b: 36, l: 56 },
    yaxis: { ...PLOTLY_LAYOUT_BASE.yaxis, title: { text: 'Horas', font: { size: 11 } } },
    showlegend: false,
  };
  const traces = [{
    x: data.totals.map(r => r.date),
    y: data.totals.map(r => Number(r.hours_watched)),
    type: 'scatter',
    mode: 'lines',
    name: 'Horas',
    line: { color: '#58a6ff', width: 2 },
    fill: 'tozeroy',
    fillcolor: 'rgba(88,166,255,0.08)',
  }];
  Plotly.react(el, traces, layout, PLOTLY_CONFIG);
}

function renderSubsChart(data) {
  const el = document.getElementById('chart-subs');
  const layout = {
    ...PLOTLY_LAYOUT_BASE,
    margin: { t: 8, r: 16, b: 36, l: 48 },
    yaxis: { ...PLOTLY_LAYOUT_BASE.yaxis, title: { text: 'Inscritos', font: { size: 11 } } },
    showlegend: false,
  };
  const traces = [{
    x: data.totals.map(r => r.date),
    y: data.totals.map(r => Number(r.subs_gained)),
    type: 'bar',
    name: 'Inscritos',
    marker: { color: '#3fb950' },
  }];
  Plotly.react(el, traces, layout, PLOTLY_CONFIG);
}

// ── Videos table + top bar chart ──────────────────────────────────
async function loadVideos() {
  try {
    const data = await apiFetch(`/youtube/api/videos?days=${currentDays}`);
    cachedVideos = data.videos;
    const label = `últimos ${currentDays} dias`;
    document.getElementById('table-period-label').textContent = label;
    renderVideosTable(cachedVideos);
    renderTopVideosChart(cachedVideos);
  } catch (e) {
    console.error('videos error', e);
  }
}

function renderTopVideosChart(videos) {
  const el = document.getElementById('chart-top-videos');
  const top = [...videos].sort((a, b) => b.total_views - a.total_views).slice(0, 8);
  const layout = {
    ...PLOTLY_LAYOUT_BASE,
    margin: { t: 8, r: 20, b: 8, l: 16 },
    xaxis: { ...PLOTLY_LAYOUT_BASE.xaxis, title: '' },
    yaxis: {
      ...PLOTLY_LAYOUT_BASE.yaxis,
      autorange: 'reversed',
      tickfont: { size: 10 },
      automargin: true,
    },
    showlegend: false,
  };
  const traces = [{
    x: top.map(v => v.total_views),
    y: top.map(v => truncate(v.title, 26)),
    type: 'bar',
    orientation: 'h',
    marker: { color: top.map((_, i) => i === 0 ? '#ff3c3c' : 'rgba(88,166,255,0.6)') },
    text: top.map(v => fmtNum(v.total_views)),
    textposition: 'outside',
    textfont: { size: 10, color: '#8b949e' },
  }];
  Plotly.react(el, traces, layout, PLOTLY_CONFIG);
}

function renderVideosTable(videos) {
  const sorted = sortVideos(videos);
  const maxViews = Math.max(...videos.map(v => v.total_views), 1);
  const maxHours = Math.max(...videos.map(v => v.total_hours), 1);

  const rows = sorted.map(v => `
    <tr>
      <td>
        <div class="video-title-cell">
          <img class="video-thumb"
               src="https://i.ytimg.com/vi/${v.video_id}/mqdefault.jpg"
               alt="" loading="lazy" onerror="this.style.display='none'">
          <a class="video-title-text"
             href="https://www.youtube.com/watch?v=${v.video_id}"
             target="_blank" rel="noopener"
             title="${escHtml(v.title)}">${escHtml(v.title)}</a>
        </div>
      </td>
      <td>
        <div class="bar-cell">
          <span class="metric-num metric-yt">${fmtNum(v.total_views)}</span>
          <div class="bar-bg"><div class="bar-fill yt-red" style="width:${pct(v.total_views, maxViews)}%"></div></div>
        </div>
      </td>
      <td>
        <div class="bar-cell">
          <span class="metric-num metric-blue">${v.total_hours}h</span>
          <div class="bar-bg"><div class="bar-fill blue" style="width:${pct(v.total_hours, maxHours)}%"></div></div>
        </div>
      </td>
      <td><span class="metric-num">${fmtDuration(v.avg_duration_s)}</span></td>
      <td>
        <span class="metric-num metric-green">${v.avg_pct.toFixed(1)}%</span>
      </td>
      <td><span class="metric-num">${fmtNum(v.total_subs)}</span></td>
      <td><span class="published-badge">${v.published_at ? v.published_at.slice(0, 10) : '—'}</span></td>
    </tr>
  `).join('');

  document.getElementById('videos-tbody').innerHTML = rows || '<tr><td colspan="7" class="loading-row">Sem dados para o período</td></tr>';
  updateSortHeaders();
}

function sortTable(col) {
  if (sortCol === col) {
    sortDir = sortDir === 'desc' ? 'asc' : 'desc';
  } else {
    sortCol = col;
    sortDir = 'desc';
  }
  if (cachedVideos) renderVideosTable(cachedVideos);
}

function sortVideos(videos) {
  return [...videos].sort((a, b) => {
    const va = a[sortCol], vb = b[sortCol];
    if (typeof va === 'number') return sortDir === 'desc' ? vb - va : va - vb;
    return sortDir === 'desc'
      ? String(vb).localeCompare(String(va))
      : String(va).localeCompare(String(vb));
  });
}

function updateSortHeaders() {
  document.querySelectorAll('.videos-table th.sortable').forEach(th => {
    th.classList.remove('sort-asc', 'sort-desc');
    if (th.dataset.col === sortCol) th.classList.add(sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
  });
}

// ── Helpers ────────────────────────────────────────────────────────
async function apiFetch(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function fmtNum(n) {
  n = Number(n);
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + 'k';
  return n.toLocaleString('pt-BR');
}

function fmtDuration(secs) {
  secs = Math.round(Number(secs));
  if (!secs) return '—';
  const m = Math.floor(secs / 60), s = secs % 60;
  return `${m}m ${String(s).padStart(2, '0')}s`;
}

function truncate(str, max) {
  return str && str.length > max ? str.slice(0, max) + '…' : str || '';
}

function escHtml(s) {
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function pct(val, max) {
  if (!max) return 0;
  return Math.max(2, Math.round((val / max) * 100));
}
