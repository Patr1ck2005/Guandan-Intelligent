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
  models: [{ id: 'blind', label: 'Blind', params: {} }, { id: 'mc', label: 'MC', params: {} }],
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
  if (E.jobId) stopJob();  // switching modes cancels any running job + stops its polling
  E.mode = mode;
  document.querySelectorAll('.etab').forEach(b => b.classList.toggle('on', b.dataset.mode === mode));
  document.getElementById('match-pick').style.display = (mode === 'match') ? '' : 'none';
  if (mode === 'match') renderMatchPicker();
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
  let h = '';
  if (E.mode === 'match' || E.mode === 'roundrobin') {
    h += `<div class="param-row"><span>局数</span><input type="number" value="${E.nGames}" onchange="E.nGames=+this.value"></div>`;
    h += `<div class="param-row"><span>种子</span><input type="number" value="${E.seed}" onchange="E.seed=+this.value"></div>`;
    h += `<div class="param-row"><span>超时ms</span><input type="number" value="${E.timeoutMs}" onchange="E.timeoutMs=+this.value"></div>`;
    h += `<div class="param-row"><span>并行</span><input type="number" value="${E.maxWorkers}" onchange="E.maxWorkers=+this.value"></div>`;
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
  let h = '<h3>排行榜</h3><table class="ev"><tr><th>模型</th><th>准确率</th><th>正确/总数</th><th>平均</th></tr>' +
    sum.map(s => `<tr><td>${escapeHtml(s.model_name)}</td><td class="${s.accuracy >= 80 ? 'ok' : 'bad'}">${s.accuracy}%</td><td>${s.correct}/${s.total}</td><td>${s.avg_ms}ms</td></tr>`).join('') + '</table>';
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
    E.jobTimer = setTimeout(() => pollJob(jobId, renderFn), 1000);
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

function renderJobProgress(d) {
  const p = Math.max(0, Math.min(1, d.progress || 0));
  const pct = Math.round(p * 100);
  const msg = d.message || '运行中…';
  const elapsed = E.jobStart ? fmtDuration((Date.now() - E.jobStart) / 1000) : '';
  const eta = E.jobStart ? computeEta(p) : '';
  return `<div class="job-running">
    <div class="pbar"><div class="pbar-fill" style="width:${pct}%"></div></div>
    <div class="job-msg">${escapeHtml(msg)}</div>
    <div class="job-eta">已运行 ${elapsed}${eta ? ' · 预计剩余 ' + eta : ''}</div>
  </div>`;
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

init();
