// ==================================================================
// Eval Lab — 评估实验室
// 场景基准 / 自对弈 / 循环赛 Elo / 生成题库
// ==================================================================

// NOTE: default params here are the "honest" self-play defaults — low sample
// counts / short time caps so a match completes in reasonable wall-clock time.
// The match result JSON records the exact params used; nothing is hidden.
const MODEL_PARAMS = {
  blind: [],
  mc: [
    { key: 'num_samples', label: '样本', dflt: 8 },
    { key: 'time_limit_ms', label: '时限ms', dflt: 500 },
    { key: 'inner', label: '内层', dflt: 'blind', opts: ['blind', 'informed', 'round', 'exact'] },
    { key: 'enumerator', label: '枚举', dflt: 'top_n', opts: ['full', 'top_n', 'memory'] },
  ],
  ismcts: [
    { key: 'max_iterations', label: '迭代', dflt: 80 },
    { key: 'time_limit_ms', label: '时限ms', dflt: 300 },
    { key: 'inner', label: '内层', dflt: 'blind', opts: ['blind', 'informed', 'round', 'exact'] },
    { key: 'enumerator', label: '枚举', dflt: 'full', opts: ['full', 'top_n', 'memory'] },
  ],
  llm: [
    { key: 'model', label: '模型', dflt: 'deepseek-chat', opts: ['deepseek-chat', 'deepseek-reasoner'] },
    { key: 'mode', label: '模式', dflt: 'candidate', opts: ['candidate', 'free'] },
    { key: 'temperature', label: '温度', dflt: 0.3 },
  ],
};

let E = {
  mode: 'benchmark',
  // modelDefaults() is hoisted, so it's safe to call during this literal init.
  // Without it MC would fall back to backend defaults (128 samples / 10s /
  // informed inner) and a default self-play run would look frozen.
  models: [{ id: 'blind', label: 'Blind', params: modelDefaults('blind') },
           { id: 'mc', label: 'MC', params: modelDefaults('mc') }],
  matchA: 0, matchB: 1,
  nGames: 3, seed: 0, timeoutMs: 20000, maxWorkers: 1,
  maxScenarios: 0,
  genN: 50, genMaxCards: 4, genSeed: 0, genTimeLimit: 5000,
  jobTimer: null,
  jobStart: null,
  jobId: null,     // currently running job (for stop)
  jobRetries: 0,   // transient poll-failure retry counter
};

function escapeHtml(t) {
  const d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML;
}

function modelDefaults(id) {
  const p = {};
  for (const f of (MODEL_PARAMS[id] || [])) p[f.key] = f.dflt;
  return p;
}

// ==================================================================
// Init
// ==================================================================
async function init() {
  try {
    const r = await fetch('/api/eval/scenarios');
    const d = await r.json();
    const c = d.counts || {};
    document.getElementById('scenario-counts').textContent =
      '题库 ' + (c.total || 0) + ' 条（' + Object.entries(c.by_source || {}).map(([k, v]) => k + ' ' + v).join(' · ') + '）';
  } catch (e) { /* silent */ }
  renderModelPicker();
  renderParamPanel();
}

function setMode(mode) {
  // Silent cancel: clear E.jobId FIRST so the pending poll's stale-guard
  // swallows it — otherwise the "已停止" branch repaints the panel right
  // after setMode resets it (the ghost-message flash).
  if (E.jobTimer) { clearTimeout(E.jobTimer); E.jobTimer = null; }
  if (E.jobId) {
    const jid = E.jobId;
    E.jobId = null;
    fetch('/api/eval/jobs/' + jid + '/cancel', { method: 'POST' }).catch(() => {});
    renderParamPanel();
  }
  E.mode = mode;
  document.querySelectorAll('.etab').forEach(b => b.classList.toggle('on', b.dataset.mode === mode));
  document.getElementById('match-pick').style.display = (mode === 'match') ? '' : 'none';
  if (mode === 'match') renderMatchPicker();
  if (mode === 'history') { renderParamPanel(); renderHistory(); return; }
  renderParamPanel();
  document.getElementById('eval-result').innerHTML = '<div class="emp">选择评估类型并运行</div>';
}

// ==================================================================
// Model picker
// ==================================================================
function renderModelPicker() {
  const opts = Object.keys(MODEL_PARAMS).map(id => `<option value="${id}">${id}</option>`).join('');
  document.getElementById('model-picker').innerHTML = E.models.map((m, i) => {
    const sel = opts.replace(`value="${m.id}"`, `value="${m.id}" selected`);
    const params = MODEL_PARAMS[m.id] || [];
    let ph = '';
    if (params.length) {
      ph = '<div class="mparams">' + params.map(f => {
        if (f.opts) {
          const o = f.opts.map(v => `<option value="${v}"${String(m.params[f.key]) === v ? ' selected' : ''}>${v}</option>`).join('');
          return `<label>${f.label}<select onchange="upModelParam(${i},'${f.key}',this.value)">${o}</select></label>`;
        }
        return `<label>${f.label}<input type="number" step="${f.key === 'temperature' ? 0.1 : 1}" value="${m.params[f.key]}" onchange="upModelParam(${i},'${f.key}',parseFloat(this.value))"></label>`;
      }).join('') + '</div>';
    }
    return `<div class="mrow">
      <select onchange="chgModel(${i},this.value)">${sel}</select>
      <input type="text" class="mlabel" value="${escapeHtml(m.label)}" placeholder="名称" onchange="upModel(${i},'label',this.value)">
      <button class="btn btn-gy" onclick="rmModel(${i})">×</button>
      ${ph}
    </div>`;
  }).join('') + `<button class="btn" onclick="addModel()">+ 模型</button>`;
}

function addModel() {
  const id = 'blind';
  E.models.push({ id, label: id, params: modelDefaults(id) });
  renderModelPicker();
}
function rmModel(i) { E.models.splice(i, 1); renderModelPicker(); if (E.mode === 'match') renderMatchPicker(); }
function upModel(i, k, v) { E.models[i][k] = v; renderModelPicker(); }
function upModelParam(i, k, v) { E.models[i].params[k] = v; }
function chgModel(i, id) {
  E.models[i].id = id;
  E.models[i].params = modelDefaults(id);
  renderModelPicker();
}

function renderMatchPicker() {
  const opts = E.models.map((m, i) => `<option value="${i}">${escapeHtml(m.label || m.id)}</option>`).join('');
  document.getElementById('match-picker').innerHTML =
    `<div class="param-row"><span>A（坐 0/2）</span><select onchange="E.matchA=+this.value">${opts}</select></div>` +
    `<div class="param-row"><span>B（坐 1/3）</span><select onchange="E.matchB=+this.value">${opts}</select></div>`;
}

// ==================================================================
// Param panel
// ==================================================================
function renderParamPanel() {
  if (E.mode === 'history') {
    document.getElementById('param-panel').innerHTML = '<span class="emp">历史为只读视图</span>';
    document.getElementById('run-panel').innerHTML = '';
    return;
  }
  let h = '';
  if (E.mode === 'match' || E.mode === 'roundrobin') {
    h += `<div class="param-row"><span>局数</span><input type="number" value="${E.nGames}" onchange="E.nGames=+this.value"></div>`;
    h += `<div class="param-row"><span>种子</span><input type="number" value="${E.seed}" onchange="E.seed=+this.value"></div>`;
    h += `<div class="param-row"><span>超时ms</span><input type="number" value="${E.timeoutMs}" onchange="E.timeoutMs=+this.value"></div>`;
    h += `<div class="param-row"><span>并行</span><input type="number" value="${E.maxWorkers}" onchange="E.maxWorkers=+this.value" title=">1 时走多进程：每局独立 seed、结果与串行完全一致；live 面板按局汇报而非逐步"></div>`;
  } else if (E.mode === 'benchmark') {
    h += `<div class="param-row"><span>最大场景数</span><input type="number" value="${E.maxScenarios}" onchange="E.maxScenarios=+this.value" title="0=全部"></div>`;
  } else if (E.mode === 'generate') {
    h += `<div class="param-row"><span>数量</span><input type="number" value="${E.genN}" onchange="E.genN=+this.value"></div>`;
    h += `<div class="param-row"><span>每家最多张</span><input type="number" value="${E.genMaxCards}" onchange="E.genMaxCards=+this.value"></div>`;
    h += `<div class="param-row"><span>种子</span><input type="number" value="${E.genSeed}" onchange="E.genSeed=+this.value"></div>`;
    h += `<div class="param-row"><span>求解时限ms</span><input type="number" value="${E.genTimeLimit}" onchange="E.genTimeLimit=+this.value"></div>`;
  }
  document.getElementById('param-panel').innerHTML = h || '<span class="emp">无参数</span>';

  let btn = '';
  if (E.mode === 'benchmark') btn = `<button class="btn btn-gn" onclick="runBenchmark()">运行基准</button>`;
  else if (E.mode === 'match') btn = `<button class="btn btn-gn" onclick="runMatch()">开始自对弈</button>`;
  else if (E.mode === 'roundrobin') btn = `<button class="btn btn-gn" onclick="runRoundRobin()">开始循环赛</button>`;
  else if (E.mode === 'generate') btn = `<button class="btn btn-gn" onclick="runGenerate()">生成题库</button>`;
  const stop = E.jobId ? `<button class="btn btn-red" onclick="stopJob()">停止</button>` : '';
  document.getElementById('run-panel').innerHTML = `<div style="display:flex;gap:6px">${btn}${stop}</div>`;
}

function collectModels() {
  return E.models.map(m => ({ id: m.id, label: m.label, ...(m.params || {}) }));
}

// ==================================================================
// Run — benchmark (async job)
// ==================================================================
async function runBenchmark() {
  const el = document.getElementById('eval-result');
  el.innerHTML = '<div class="running">启动基准…</div>';
  const body = { models: collectModels(), max_scenarios: E.maxScenarios };
  const d = await postJob('/api/eval/benchmark', body, el);
  startJob(d.job_id, renderBenchmark);
}

function renderBenchmark(d) {
  const sum = d.summary || [], res = d.results || [];
  const ciTxt = s => (s.wilson ? ` <span class="mono">[${s.wilson[0]}–${s.wilson[1]}]</span>` : '');
  let h = '<h3>排行榜</h3><table class="ev"><tr><th>模型</th><th>准确率</th><th>正确/总数</th><th>平均</th></tr>' +
    sum.map(s => `<tr><td>${escapeHtml(s.model_name)}</td><td class="${s.accuracy >= 80 ? 'ok' : 'bad'}">${s.accuracy}%${ciTxt(s)}</td><td>${s.correct}/${s.total}</td><td>${s.avg_ms}ms</td></tr>`).join('') + '</table>';
  // per-scenario matrix
  const models = sum.map(s => s.model);
  const bySc = {};
  for (const r of res) { bySc[r.scenario_id] = bySc[r.scenario_id] || {}; bySc[r.scenario_id][r.model] = r.correct; }
  h += '<h3>逐场景</h3><table class="ev"><tr><th>场景</th>' + models.map(m => `<th>${escapeHtml(m)}</th>`).join('') + '</tr>';
  for (const sid of Object.keys(bySc)) {
    h += `<tr><td>${escapeHtml(sid)}</td>` + models.map(m => {
      const v = bySc[sid][m];
      return `<td class="${v ? 'ok' : 'bad'}">${v === undefined ? '?' : (v ? 'OK' : 'XX')}</td>`;
    }).join('') + '</tr>';
  }
  h += '</table>';
  document.getElementById('eval-result').innerHTML = h;
}

// ==================================================================
// Run — match / round-robin / generate (async)
// ==================================================================
async function runMatch() {
  const el = document.getElementById('eval-result');
  el.innerHTML = '<div class="running">启动自对弈…</div>';
  const body = { a: collectModels()[E.matchA], b: collectModels()[E.matchB], n_games: E.nGames, seed: E.seed, agent_timeout_ms: E.timeoutMs, max_workers: E.maxWorkers };
  const d = await postJob('/api/eval/match', body, el);
  startJob(d.job_id, renderMatch);
}

async function runRoundRobin() {
  const el = document.getElementById('eval-result');
  el.innerHTML = '<div class="running">启动循环赛…</div>';
  const body = { models: collectModels(), n_games: E.nGames, seed: E.seed, agent_timeout_ms: E.timeoutMs, max_workers: E.maxWorkers };
  const d = await postJob('/api/eval/round_robin', body, el);
  startJob(d.job_id, renderRoundRobin);
}

async function runGenerate() {
  const el = document.getElementById('eval-result');
  el.innerHTML = '<div class="running">生成题库中（精确解标注，可能较慢）…</div>';
  const body = { n: E.genN, max_cards: E.genMaxCards, seed: E.genSeed, time_limit_ms: E.genTimeLimit };
  const d = await postJob('/api/eval/scenarios/generate', body, el);
  startJob(d.job_id, renderGenerate);
}

async function postJob(url, body, el) {
  try {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return await r.json();
  } catch (e) { el.innerHTML = '<div class="bad">错误: ' + escapeHtml(e.message) + '</div>'; throw e; }
}

function startJob(jobId, renderFn) {
  if (E.jobTimer) { clearTimeout(E.jobTimer); E.jobTimer = null; }
  if (E.jobId && E.jobId !== jobId) {
    // A previous job was still running — best-effort cancel it.
    fetch('/api/eval/jobs/' + E.jobId + '/cancel', { method: 'POST' }).catch(() => {});
  }
  E.jobId = jobId;
  E.jobStart = Date.now();
  E.jobRetries = 0;
  renderParamPanel();       // show the stop button
  pollJob(jobId, renderFn);
}

function finishJob() {
  E.jobId = null;
  E.jobStart = null;
  E.jobRetries = 0;
  renderParamPanel();       // hide the stop button
}

async function stopJob() {
  if (!E.jobId) return;
  const jobId = E.jobId;
  try {
    await fetch('/api/eval/jobs/' + jobId + '/cancel', { method: 'POST' });
  } catch (e) { /* server may be slow; still mark stopping */ }
  const el = document.getElementById('eval-result');
  el.innerHTML = '<div class="running">正在停止…（当前局完成即中止）</div>';
  E.jobTimer = setTimeout(() => pollJob(jobId, null), 1000);
}

async function pollJob(jobId, renderFn) {
  if (E.jobId !== jobId) return;   // stale poll — a newer job took over
  try {
    const r = await fetch('/api/eval/jobs/' + jobId);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    if (E.jobId !== jobId) return; // superseded while awaiting
    if (d.status === 'done') {
      if (renderFn) renderFn(d.result);
      finishJob();
      return;
    }
    if (d.status === 'cancelled') {
      document.getElementById('eval-result').innerHTML = '<div style="color:#e67e22">已停止</div>';
      finishJob();
      return;
    }
    if (d.status === 'error') {
      document.getElementById('eval-result').innerHTML = '<div class="bad">错误: ' + escapeHtml(d.error) + '</div>';
      finishJob();
      return;
    }
    document.getElementById('eval-result').innerHTML = renderJobProgress(d);
    E.jobTimer = setTimeout(() => pollJob(jobId, renderFn), 500);
  } catch (e) {
    if (E.jobId !== jobId) return;
    // Transient failure (server restart, network blip) — retry before declaring death.
    E.jobRetries = (E.jobRetries || 0) + 1;
    if (E.jobRetries <= 5) {
      document.getElementById('eval-result').innerHTML = `<div class="running">轮询重试 ${E.jobRetries}/5…</div>`;
      E.jobTimer = setTimeout(() => pollJob(jobId, renderFn), 1500 * E.jobRetries);
    } else {
      document.getElementById('eval-result').innerHTML = '<div class="bad">轮询错误: ' + escapeHtml(e.message) + '</div>';
      finishJob();
    }
  }
}

// ------------------------------------------------------------------
// Live dashboard — mode-aware, reads job detail for rich graphics
// ------------------------------------------------------------------

const COMBO_CN = { SINGLE: '单张', PAIR: '对子', TRIPLE: '三条', TRIPLE_PAIR: '三带二',
  STRAIGHT: '顺子', CONSECUTIVE_PAIRS: '连对', CONSECUTIVE_TRIPLES: '钢板',
  NORMAL_BOMB: '炸弹', STRAIGHT_FLUSH: '同花顺', ROCKET: '天王炸' };

function renderJobProgress(d) {
  const det = d.detail || {};
  if (det.mode === 'multiprocess') return renderMpLive(d, det);
  if (det.game !== undefined && det.hand_sizes) return renderMatchLive(d, det);
  if (det.rr_pair) return renderRRLive(d, det);
  if (det.partial_summary) return renderBenchLive(d, det);
  if (det.solved !== undefined) return renderGenLive(d, det);
  // Generic fallback: bar + message + ETA
  const head = progressHead(d.progress, d.message, null);
  return `<div class="job-running">${head}</div>`;
}

function progressHead(p, msg, extra) {
  const pct = Math.round(Math.max(0, Math.min(1, p || 0)) * 100);
  const elapsed = E.jobStart ? fmtDuration((Date.now() - E.jobStart) / 1000) : '';
  const eta = computeEta(p);
  return `<div class="live-head">
    <span class="chip-run">运行中</span>
    <span class="big-pct">${pct}<small>%</small></span>
    <div class="pbar"><div class="pbar-fill" style="width:${pct}%"></div></div>
    <div class="job-eta">已运行 ${elapsed}${eta ? ' · 预计剩余 ' + eta : ''}</div>
    ${msg ? `<div class="job-msg">${escapeHtml(msg)}</div>` : ''}
    ${extra || ''}
  </div>`;
}

// Multiprocess match mode: live detail arrives once per completed game
// (move-level callbacks cannot cross process boundaries). Per-seed results
// stay identical to sequential mode; the per-move dashboard is the
// parallel=1 trade-off.
function renderMpLive(d, det) {
  const workers = det.workers || '?';
  const lastW = det.last_winner_team === 0 ? 'A 胜'
    : det.last_winner_team === 1 ? 'B 胜'
    : det.last_winner_team === -1 ? '出错' : '—';
  const stats = [
    ['并行进程', workers + ' 路'],
    ['完成局数', `${det.game || 0}/${det.n_games || '?'}`],
    ['最近结果', lastW],
    ['最近 seed', det.last_seed ?? '—'],
  ].map(([k, v]) => `<div class="stat-card"><div class="sc-k">${k}</div><div class="sc-v">${v}</div></div>`).join('');
  let scoreBlocks = '';
  if ((det.scores || []).length) {
    scoreBlocks = '<div class="score-row">' + det.scores.map(s =>
      `<div class="gblock ${s === 0 ? 'wA' : s === 1 ? 'wB' : s === -2 ? 'wRun' : 'wErr'}"></div>`).join('') + '</div>';
  }
  return `<div class="job-running">
    ${progressHead(d.progress,
      `多进程模式 · ${workers} 路并行 · 每局完成时更新`, scoreBlocks)}
    <div class="stat-row">${stats}</div>
    <div class="emp">多进程模式按局汇报进度（逐步明细请把并行设为 1）</div>
  </div>`;
}

function renderMatchLive(d, det) {
  const aName = (E.models[E.matchA] && (E.models[E.matchA].label || E.models[E.matchA].id)) || 'A';
  const bName = (E.models[E.matchB] && (E.models[E.matchB].label || E.models[E.matchB].id)) || 'B';
  const maxHand = 27;
  const seats = [0, 1, 2, 3].map(p => {
    const n = det.hand_sizes[p] || 0;
    const teamCls = p % 2 === 0 ? 'tA' : 'tB';
    const nm = p % 2 === 0 ? aName : bName;
    return `<div class="seat-bar"><span class="seat-lb">P${p + 1} ${nm}</span>
      <div class="seat-track"><div class="seat-fill ${teamCls}" style="width:${Math.round(n / maxHand * 100)}%"></div></div>
      <span class="seat-n">${n}</span></div>`;
  }).join('');
  // Sparkline: recent move durations
  const hist = det.move_ms_hist || [];
  const hMax = Math.max(50, ...hist);
  const spark = hist.map(ms =>
    `<div class="spark-col${ms > 1000 ? ' slow' : ''}" style="height:${Math.max(2, Math.round(ms / hMax * 40))}px" title="${ms}ms"></div>`
  ).join('');
  const thinkTotal = Math.max(1, (det.think_ms || [0, 0]).reduce((a, b) => a + b, 0));
  const tA = (det.think_ms ? det.think_ms[0] : 0), tB = (det.think_ms ? det.think_ms[1] : 0);
  const pctA = Math.round(tA / thinkTotal * 100);
  const lastAct = det.last_action === 'pass'
    ? `P${(det.last_player ?? -1) + 1} 过牌`
    : `P${(det.last_player ?? -1) + 1} 出 ${escapeHtml(COMBO_CN[det.last_combo_type] || det.last_combo_type || '?')} <span class="mono">${escapeHtml((det.last_combo_cards || []).join(' '))}</span>`;
  const stats = [
    ['当前局', `${det.game}/${det.n_games}`],
    ['第几步', det.move],
    ['轮次', det.trick],
    ['步速', (det.moves_per_sec || 0).toFixed(2) + '/s'],
    ['上一步', Math.round(det.last_move_ms || 0) + 'ms'],
    ['平均步', Math.round(det.avg_move_ms || 0) + 'ms'],
  ].map(([k, v]) => `<div class="stat-card"><div class="sc-k">${k}</div><div class="sc-v">${v}</div></div>`).join('');
  let scoreBlocks = '';
  if ((det.scores || []).length) {
    scoreBlocks = '<div class="score-row">' + det.scores.map(s =>
      `<div class="gblock ${s === 0 ? 'wA' : s === 1 ? 'wB' : s === -2 ? 'wRun' : 'wErr'}"></div>`).join('') + '</div>';
  }
  return `<div class="job-running">
    ${progressHead(d.progress,
      `局 ${det.game}/${det.n_games} · 第 ${det.move} 步 · 最新：${lastAct}`, scoreBlocks)}
    <div class="stat-row">${stats}</div>
    <h3>手牌余量</h3>${seats}
    <h3>思考时间（速度去哪了）</h3>
    <div class="duel-bar">
      <div class="duel-a" style="width:${pctA}%">${aName} ${fmtMs(tA)}</div>
      <div class="duel-b" style="flex:1">${bName} ${fmtMs(tB)}</div>
    </div>
    <h3>最近 ${MAX_SPARK} 步耗时</h3>
    <div class="spark">${spark || '<span class="emp">采样中…</span>'}</div>
  </div>`;
}

function renderRRLive(d, det) {
  // The sub-match detail (game/hand_sizes/...) renders as the "current match"
  // panel; hide its own progress header since the RR header already has one.
  const inner = det.hand_sizes
    ? renderMatchLive({ progress: d.progress }, det).replace(/^[\s\S]*?<div class="stat-row">/, '<div class="stat-row">')
    : '';
  const rank = (det.elo || []).map((e, i) =>
    `<tr><td>${i + 1}</td><td>${escapeHtml(e.label)}</td><td>${e.rating}</td></tr>`).join('');
  return `<div class="job-running">
    ${progressHead(d.progress, `对局 ${det.rr_done + 1}/${det.rr_total} · ${escapeHtml(det.rr_pair)}`)}
    <h3>当前对阵</h3>${inner || '<div class="emp">等待首局…</div>'}
    <h3>实时 Elo</h3><table class="ev"><tr><th>#</th><th>模型</th><th>Elo</th></tr>${rank}</table>
  </div>`;
}

function renderBenchLive(d, det) {
  const sum = det.partial_summary || [];
  const board = sum.length
    ? '<table class="ev"><tr><th>模型</th><th>准确率</th><th>正确/总数</th><th>平均</th></tr>'
      + sum.map(s => `<tr><td>${escapeHtml(s.model_name)}</td>
        <td class="${s.accuracy >= 80 ? 'ok' : s.accuracy >= 50 ? '' : 'bad'}">${s.accuracy}%${s.wilson ? ` <span class="mono">[${s.wilson[0]}–${s.wilson[1]}]</span>` : ''}</td>
        <td>${s.correct}/${s.total}</td><td>${s.avg_ms}ms</td></tr>`).join('') + '</table>'
    : '<div class="emp">等待首个场景完成…</div>';
  // Matrix cells light up as results land
  const matrix = (det.matrix || []).map(cell => {
    const [ok, err] = cell;
    const cls = err ? 'mx-err' : ok ? 'mx-ok' : 'mx-bad';
    const ch = err ? '!' : ok ? '✓' : '✗';
    return `<div class="mx-cell ${cls}">${ch}</div>`;
  }).join('');
  return `<div class="job-running">
    ${progressHead(d.progress,
      `场景 ${det.si}/${det.n_scenarios} · ${escapeHtml(det.scenario_name || '')} · 模型 ${det.mi}/${det.n_models} · ${escapeHtml(det.model_name || '')}`,
      `<div class="bench-last">上一判定：<span class="${det.last_correct ? 'ok' : 'bad'}">${det.last_correct ? '正确 ✓' : '错误 ✗'}</span> · ${Math.round(det.last_ms || 0)}ms</div>`)}
    <h3>实时排行榜</h3>${board}
    <h3>逐格点亮</h3><div class="mx-grid">${matrix}</div>
  </div>`;
}

function renderGenLive(d, det) {
  const total = (det.solved || 0) + (det.skipped || 0);
  const solvedPct = total ? Math.round((det.solved / total) * 100) : 0;
  return `<div class="job-running">
    ${progressHead(d.progress, `已生成 ${det.solved}/${det.of_n}（跳过 ${det.skipped}）`)}
    <div class="dual-bar">
      <div class="dual-solved" style="width:${solvedPct}%">求解成功 ${det.solved}</div>
      <div class="dual-skipped" style="flex:1">跳过 ${det.skipped}</div>
    </div>
    <div class="job-eta">共尝试 ${det.attempts || 0} 次 · 精确解标注较慢，属正常现象</div>
  </div>`;
}

const MAX_SPARK = 60;

function fmtMs(ms) {
  if (ms >= 60000) return Math.floor(ms / 60000) + 'm' + Math.round((ms % 60000) / 1000) + 's';
  if (ms >= 1000) return (ms / 1000).toFixed(1) + 's';
  return Math.round(ms) + 'ms';
}

function computeEta(p) {
  if (!E.jobStart || p <= 0) return '…';
  const elapsed = (Date.now() - E.jobStart) / 1000;
  const remaining = elapsed / p - elapsed;
  if (remaining < 0) return '即将完成';
  return fmtDuration(remaining);
}

function fmtDuration(s) {
  if (s < 60) return Math.round(s) + 's';
  return Math.floor(s / 60) + 'm ' + Math.round(s % 60) + 's';
}

function renderMatch(m) {
  const n = m.n_games, aw = m.a_wins, bw = m.b_wins;
  const aName = (m.a && (m.a.label || m.a.id)) || 'A';
  const bName = (m.b && (m.b.label || m.b.id)) || 'B';
  let h = `<h3>自对弈结果</h3><div>${escapeHtml(aName)} ${aw} : ${bw} ${escapeHtml(bName)}（${n} 局，${m.elapsed_s}s）</div>`;
  h += '<div style="color:#888;font-size:0.78em">真实参数：' + escapeHtml(JSON.stringify({ a: m.a, b: m.b, n_games: n })) + '</div>';
  h += '<table class="ev"><tr><th>#</th><th>种子</th><th>胜方</th><th>局数</th></tr>' +
    (m.games || []).map((g, i) => `<tr><td>${i + 1}</td><td>${g.seed}</td><td>${g.winning_team === 0 ? aName : bName}</td><td>${g.rounds_played}</td></tr>`).join('') + '</table>';
  document.getElementById('eval-result').innerHTML = h;
}

function renderRoundRobin(d) {
  const rk = d.ranking || [];
  let h = '<h3>Elo 排名</h3><table class="ev"><tr><th>排名</th><th>模型</th><th>Elo</th><th>胜/负</th></tr>' +
    rk.map((r, i) => `<tr><td>${i + 1}</td><td>${escapeHtml((r.model && (r.model.label || r.model.id)) || '?')}</td><td>${r.rating}</td><td>${r.wins}/${r.losses}</td></tr>`).join('') + '</table>';
  document.getElementById('eval-result').innerHTML = h;
}

function renderGenerate(d) {
  let h = `<h3>生成完成</h3><div>新增 ${d.generated} 条场景</div>`;
  h += '<table class="ev"><tr><th>ID</th><th>名称</th><th>手牌</th><th>期望</th></tr>' +
    (d.scenarios || []).map(s => `<tr><td>${escapeHtml(s.id)}</td><td>${escapeHtml(s.name)}</td><td>${s.hand_size}张</td><td>${s.expected_pass ? '过牌' : '出牌'}</td></tr>`).join('') + '</table>';
  document.getElementById('eval-result').innerHTML = h;
  init(); // refresh counts
}

// ==================================================================
// History — persisted runs (SQLite store), Elo trend, A/B compare
// ==================================================================

const HIST_COLORS = ['#4e8cff', '#e67e22', '#27ae60', '#9b59b6', '#e74c3c', '#16a085'];

async function renderHistory() {
  const el = document.getElementById('eval-result');
  el.innerHTML = '<div class="emp">加载历史…</div>';
  let runs = [];
  try {
    const r = await fetch('/api/eval/history?limit=100');
    const d = await r.json();
    runs = d.runs || [];
  } catch (e) {
    el.innerHTML = '<div class="bad">加载失败: ' + escapeHtml(e.message) + '</div>';
    return;
  }
  if (!runs.length) {
    el.innerHTML = '<div class="emp">暂无历史记录 — 跑一次基准 / 自对弈后会自动入库</div>';
    return;
  }
  const ids = runs.map(r => r.id);
  const opts = ids.map((id, i) => `<option value="${id}">#${id}</option>`).join('');
  let h = '<h3>回归基线</h3><div id="reg-section">加载中…</div>';
  h += '<h3>Elo 走势（循环赛）</h3>' + renderEloTrend(runs);
  h += '<h3>A / B 对比</h3><div style="display:flex;gap:8px;align-items:center">' +
    `<select id="hist-cmp-a" onchange="renderHistCompare()">${opts}</select>` +
    '<span>vs</span>' +
    `<select id="hist-cmp-b" onchange="renderHistCompare()">${opts.replace('option value="' + ids[1], 'option selected value="' + ids[1])}</select>` +
    '</div><div id="hist-compare"></div>';
  h += '<h3>全部记录</h3><table class="ev"><tr><th>#</th><th>时间(UTC)</th><th>类型</th><th>关键指标</th><th>git</th><th></th></tr>' +
    runs.map(r => `<tr class="hrow" onclick="renderRunDetail(${r.id})">
      <td>${r.id}</td><td>${escapeHtml(r.ts_utc || '')}</td>
      <td><span class="chip-kind k-${escapeHtml(r.kind)}">${escapeHtml(r.kind)}</span></td>
      <td>${escapeHtml(histMetricPreview(r))}</td>
      <td class="mono">${escapeHtml(r.git_commit || '-')}</td>
      <td><a href="/api/eval/export/${r.id}?format=html" target="_blank" onclick="event.stopPropagation()">报告</a></td>
    </tr>`).join('') + '</table>';
  el.innerHTML = h;
  renderHistCompare();
  loadRegressionSection();
}

// ---- Regression baseline (stage 3.5) ----

async function loadRegressionSection() {
  const box = document.getElementById('reg-section');
  if (!box) return;
  try {
    const r = await fetch('/api/eval/regression/latest');
    const d = await r.json();
    if (!d.run) {
      box.innerHTML = '<div class="emp">尚无回归记录 — <button class="btn btn-gn" onclick="runRegression()">运行回归套件</button></div>';
      return;
    }
    const verdict = d.verdict || 'unknown';
    const badge = verdict === 'green' ? '<span class="badge badge-green">回归通过 ✓</span>'
      : verdict === 'red' ? '<span class="badge badge-red">回归异常 ✗</span>'
      : `<span class="badge badge-gray">${escapeHtml(verdict)}</span>`;
    box.innerHTML = `<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      ${badge}
      <span class="mono">#${d.run.id} · ${escapeHtml(d.run.ts_utc || '')}</span>
      <button class="btn btn-gn" onclick="runRegression()">重跑</button>
      <button class="btn btn-gy" onclick="saveRegressionBaseline(${d.run.id})">存为基线</button>
      <a class="btn btn-gy" href="/api/eval/export/${d.run.id}?format=html" target="_blank">报告</a>
    </div>`;
  } catch (e) {
    box.innerHTML = '<div class="bad">回归状态加载失败</div>';
  }
}

function runRegression() {
  const el = document.getElementById('eval-result');
  el.innerHTML = '<div class="running">启动回归套件（基准 + 固定 seed 自对弈）…</div>';
  postJob('/api/eval/regression', {}, el)
    .then(d => startJob(d.job_id, renderRegressionResult));
}

function renderRegressionResult(res) {
  const verdict = res.verdict || 'unknown';
  const badge = verdict === 'green' ? '<span class="badge badge-green">回归通过 ✓</span>'
    : verdict === 'red' ? '<span class="badge badge-red">回归异常 ✗</span>'
    : `<span class="badge badge-gray">${escapeHtml(verdict)}</span>`;
  const breaches = (res.compare && res.compare.breaches) || [];
  let h = `<h3>回归结果</h3><div>${badge} <span class="mono">对比基线 #${res.baseline_run_id ?? '-'}</span></div>`;
  if (breaches.length) {
    h += '<table class="ev"><tr><th>指标</th><th>当前</th><th>基线</th><th>偏差(pp)</th><th>容差</th></tr>' +
      breaches.map(b => `<tr><td>${escapeHtml(b.metric)}</td><td>${b.current}</td><td>${b.baseline}</td>` +
        `<td class="bad">${b.delta_pp > 0 ? '+' : ''}${b.delta_pp}</td><td>±${b.tolerance_pp}</td></tr>`).join('') + '</table>';
  }
  const bench = (res.metrics && res.metrics.benchmark) || {};
  h += '<h4>基准指标</h4><table class="ev"><tr><th>模型</th><th>准确率</th></tr>' +
    Object.entries(bench).map(([m, v]) => `<tr><td>${escapeHtml(m)}</td><td>${v.accuracy}%${v.wilson ? ` <span class="mono">[${v.wilson[0]}–${v.wilson[1]}]</span>` : ''}</td></tr>`).join('') + '</table>';
  const ms = (res.metrics && res.metrics.match) || {};
  h += `<h4>自对弈</h4><div class="mono">A ${ms.a_share ?? '-'}% : ${ms.b_share ?? '-'}% B（${ms.n_games ?? 0} 局）</div>`;
  h += `<div style="margin-top:10px"><button class="btn btn-gy" onclick="renderHistory()">← 返回历史</button>` +
    `<button class="btn btn-gn" onclick="saveRegressionBaseline(null)">存为基线</button></div>`;
  document.getElementById('eval-result').innerHTML = h;
}

async function saveRegressionBaseline(runId) {
  if (!runId) {
    // latest regression run
    try {
      const r = await fetch('/api/eval/regression/latest');
      const d = await r.json();
      runId = d.run && d.run.id;
    } catch (e) { /* fallthrough */ }
  }
  if (!runId) return;
  await fetch('/api/eval/regression/baseline', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ run_id: runId }) });
  loadRegressionSection();
}

function histMetricPreview(r) {
  const s = r.summary || {};
  if (r.kind === 'benchmark') {
    const sum = s.summary || [];
    return sum.map(x => `${x.model_name || x.model} ${x.accuracy}%`).join(' · ') || '-';
  }
  if (r.kind === 'match') {
    return `A ${s.a_wins} : ${s.b_wins} B (${s.n_games} 局)`;
  }
  if (r.kind === 'round_robin') {
    const rk = s.ranking || [];
    return rk.map(x => `${(x.model && (x.model.label || x.model.id)) || '?'} ${x.rating}`).join(' · ') || '-';
  }
  if (r.kind === 'regression') return s.verdict ? ('回归: ' + s.verdict) : '-';
  return Object.keys(s).slice(0, 3).map(k => `${k}=${JSON.stringify(s[k]).slice(0, 30)}`).join(' · ') || '-';
}

function renderEloTrend(runs) {
  // Chronological round_robin runs; one polyline per model (by model_id).
  const rr = runs.filter(r => r.kind === 'round_robin').slice().reverse();
  const byModel = {};
  rr.forEach(r => (r.models || []).forEach(m => {
    if (m.elo == null) return;
    (byModel[m.model_id] = byModel[m.model_id] || []).push(m.elo);
  }));
  const modelIds = Object.keys(byModel);
  if (rr.length < 2 || !modelIds.length) {
    return '<div class="emp">至少两次循环赛后显示 Elo 走势</div>';
  }
  const W = 640, H = 160, PAD = 28;
  const all = modelIds.flatMap(k => byModel[k]);
  const minY = Math.min(...all) - 5, maxY = Math.max(...all) + 5;
  const xAt = i => PAD + i * ((W - 2 * PAD) / Math.max(1, rr.length - 1));
  const yAt = v => H - PAD - (v - minY) * ((H - 2 * PAD) / (maxY - minY));
  let svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;background:#fafbff;border:1px solid #e5e8f0;border-radius:8px">`;
  for (let g = 0; g <= 2; g++) {
    const gy = PAD + g * ((H - 2 * PAD) / 2);
    const gv = Math.round(maxY - g * (maxY - minY) / 2);
    svg += `<line x1="${PAD}" y1="${gy}" x2="${W - PAD}" y2="${gy}" stroke="#e5e8f0"/>` +
      `<text x="2" y="${gy + 4}" font-size="10" fill="#889">${gv}</text>`;
  }
  modelIds.forEach((k, ki) => {
    const pts = byModel[k].map((v, i) => `${xAt(i)},${yAt(v)}`).join(' ');
    const color = HIST_COLORS[ki % HIST_COLORS.length];
    svg += `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2"/>`;
    svg += `<text x="${W - PAD - 40}" y="${14 + ki * 13}" font-size="11" fill="${color}">${escapeHtml(String(k))}</text>`;
  });
  svg += '</svg>';
  return svg;
}

async function renderHistCompare() {
  const aId = document.getElementById('hist-cmp-a')?.value;
  const bId = document.getElementById('hist-cmp-b')?.value;
  const box = document.getElementById('hist-compare');
  if (!aId || !bId || !box) return;
  try {
    const [ra, rb] = await Promise.all([
      fetch('/api/eval/history/' + aId).then(r => r.json()),
      fetch('/api/eval/history/' + bId).then(r => r.json()),
    ]);
    box.innerHTML = compareTable(ra, rb);
  } catch (e) {
    box.innerHTML = '<div class="bad">对比加载失败</div>';
  }
}

function compareTable(a, b) {
  const rows = [];
  const paramRows = new Set([...Object.keys(a.params || {}), ...Object.keys(b.params || {})]);
  paramRows.delete('models');
  rows.push('<tr><th colspan="3">参数</th></tr>');
  rows.push([...paramRows].map(k =>
    `<tr><td>${escapeHtml(k)}</td><td class="mono">${escapeHtml(JSON.stringify((a.params || {})[k]) ?? '')}</td>` +
    `<td class="mono">${escapeHtml(JSON.stringify((b.params || {})[k]) ?? '')}</td></tr>`).join(''));
  rows.push('<tr><th colspan="3">指标</th></tr>');
  rows.push(`<tr><td>类型</td><td>${escapeHtml(a.kind || '')}</td><td>${escapeHtml(b.kind || '')}</td></tr>`);
  rows.push(`<tr><td>时间(UTC)</td><td>${escapeHtml(a.ts_utc || '')}</td><td>${escapeHtml(b.ts_utc || '')}</td></tr>`);
  const am = a.models || [], bm = b.models || [];
  const maxLen = Math.max(am.length, bm.length);
  for (let i = 0; i < maxLen; i++) {
    const x = am[i] || {}, y = bm[i] || {};
    rows.push(`<tr><td>模型${i + 1}</td>` +
      `<td>${escapeHtml(String(x.model_id || '-'))} acc ${x.accuracy ?? '-'} elo ${x.elo ?? '-'} 胜负 ${x.wins ?? '-'}/${x.losses ?? '-'}</td>` +
      `<td>${escapeHtml(String(y.model_id || '-'))} acc ${y.accuracy ?? '-'} elo ${y.elo ?? '-'} 胜负 ${y.wins ?? '-'}/${y.losses ?? '-'}</td></tr>`);
  }
  return `<table class="ev"><tr><th>项</th><th>#${a.id}</th><th>#${b.id}</th></tr>${rows.join('')}</table>`;
}

async function renderRunDetail(id) {
  const el = document.getElementById('eval-result');
  el.innerHTML = '<div class="emp">加载详情…</div>';
  try {
    const r = await fetch('/api/eval/history/' + id);
    const run = await r.json();
    let h = `<div><button class="btn btn-gy" onclick="renderHistory()">← 返回列表</button></div>`;
    h += `<h3>#${run.id} · ${escapeHtml(run.kind)} · ${escapeHtml(run.ts_utc || '')}</h3>`;
    h += `<div class="mono" style="font-size:0.78em;color:#666">git ${escapeHtml(run.git_commit || '-')} · seed ${run.seed ?? '-'} · notes ${escapeHtml(run.notes || '-')}</div>`;
    h += '<h4>参数</h4><pre class="mono" style="font-size:0.75em;white-space:pre-wrap">' + escapeHtml(JSON.stringify(run.params, null, 2)) + '</pre>';
    h += '<h4>结果摘要</h4><pre class="mono" style="font-size:0.75em;white-space:pre-wrap">' + escapeHtml(JSON.stringify(run.summary, null, 2)) + '</pre>';
    if ((run.models || []).length) {
      h += '<h4>模型明细</h4><table class="ev"><tr><th>模型</th><th>准确率</th><th>Elo</th><th>胜/负</th><th>平均</th></tr>' +
        run.models.map(m => `<tr><td>${escapeHtml(String(m.model_name || m.model_id))}</td>` +
          `<td>${m.accuracy != null ? m.accuracy + '%' : '-'}</td><td>${m.elo != null ? m.elo : '-'}</td>` +
          `<td>${m.wins != null ? (m.wins + '/' + m.losses) : '-'}</td><td>${m.avg_ms != null ? m.avg_ms + 'ms' : '-'}</td></tr>`).join('') + '</table>';
    }
    h += '<h4>导出</h4><div style="display:flex;gap:8px">' +
      `<a class="btn btn-gy" href="/api/eval/export/${run.id}?format=json" target="_blank">JSON</a>` +
      `<a class="btn btn-gy" href="/api/eval/export/${run.id}?format=csv" target="_blank">CSV</a>` +
      `<a class="btn btn-gy" href="/api/eval/export/${run.id}?format=html" target="_blank">HTML 报告</a></div>`;
    el.innerHTML = h;
  } catch (e) {
    el.innerHTML = '<div class="bad">加载失败: ' + escapeHtml(e.message) + '</div>';
  }
}

init();
