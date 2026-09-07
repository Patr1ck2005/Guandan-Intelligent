// ==================================================================
// Card utilities
// ==================================================================
const SC = {0:'♣',1:'♦',2:'♥',3:'♠',4:''};
const SN = {0:'C',1:'D',2:'H',3:'S',4:''};
const PN = {0:'你',1:'右家',2:'对家',3:'左家'};

function ac(c, cls, attrs) {
  if (!c) return '';
  const sc = c.is_joker ? (c.rank_name==='SJ'?'joker-black':'joker-red') : 'suit-'+(c.suit_name||SN[c.suit]||'');
  let h = `<div class="card${c.is_wild?' wild':''} ${sc}${cls?' '+cls:''}"`;
  if (attrs) h += ` ${attrs}`;
  h += `><span class="card-rank">${c.rank_name}</span><span class="card-suit-big">${SC[c.suit]||''}</span></div>`;
  return h;
}
function scs(cs) { return [...cs].sort((a,b)=>a.rank!==b.rank?b.rank-a.rank:(a.suit||0)-(b.suit||0)); }

// ==================================================================
// Resizable sidebars
// ==================================================================
let dragState = null;
document.addEventListener('mousedown', e => {
  if (!e.target.classList.contains('resize-handle')) return;
  e.preventDefault(); dragState = {el: e.target, x: e.clientX}; e.target.classList.add('dragging');
});
document.addEventListener('mousemove', e => {
  if (!dragState) return;
  const dx = e.clientX - dragState.x; dragState.x = e.clientX;
  const side = dragState.el.dataset.side;
  const target = document.getElementById(dragState.el.dataset.target);
  if (!target) return;
  let w = target.offsetWidth + (side==='right' ? dx : -dx);
  w = Math.max(180, Math.min(500, w));
  target.style.width = w + 'px';
});
document.addEventListener('mouseup', () => { if (dragState) { dragState.el.classList.remove('dragging'); dragState = null; } });

// ==================================================================
// Shared state
// ==================================================================
let scens = [], cats = [], allM = {}, schema = {};
let P = { cat:'deduction', sid:null, simId:null, simState:null, profs:[], res:[], selected:new Set(), running:false, abort:null };
let T = { cat:'sampling', sid:null, simId:null, simState:null, profs:[], res:[], debug:false, persps:{}, persp:0, running:false, abort:null };
const CCN = {deduction:'确定推演',sampling:'不确定采样',endgame:'残局求解',opening:'开局评估'};

// ==================================================================
// Model algorithm descriptions and parameters
// ==================================================================
const MODEL_INFO = {
  informed: {
    name: 'Informed 控权评分',
    desc: '利用已知四家手牌信息，对每个候选牌型做加权评分，选出最优出牌。',
    flow: `1. 生成候选牌型
   · 首家时：枚举低单张、对子、三条、顺子、炸弹等
   · 跟牌时：枚举所有能压过桌面的同类型牌型 + 炸弹
2. 对每个候选计算评分（所有权重可调）：
   · 轮次节省 = (打出前手牌轮次 - 打出后手牌轮次) × round_weight
   · 检查下一家谁能压制此牌型：
     - 无人能压 → +no_counter_bonus
     - 队友能压 → +teammate_cover_bonus
     - 对手能压 → -opponent_counter_penalty
   · 炸弹检测：
     - 首家出炸弹 → -bomb_lead_penalty
     - 有更优非炸弹方案却出炸弹 → -bomb_overuse_penalty
3. 过牌评分：
   · 队友可接 → +pass_teammate_bonus
   · 本队已有控权 → +pass_control_bonus
   · 其他情况 → pass_neutral
4. 按总分降序排列，取最高分为推荐选择`,
    params: [
      {key:'round_weight', label:'轮次节省权重', type:'float', dflt:5.0, step:0.5},
      {key:'no_counter_bonus', label:'无人能压奖励', type:'float', dflt:8.0, step:0.5},
      {key:'teammate_cover_bonus', label:'队友可接奖励', type:'float', dflt:2.0, step:0.5},
      {key:'opponent_counter_penalty', label:'对手能压惩罚', type:'float', dflt:3.0, step:0.5},
      {key:'bomb_lead_penalty', label:'首家出炸弹惩罚', type:'float', dflt:5.0, step:0.5},
      {key:'bomb_overuse_penalty', label:'出炸弹却非必要惩罚', type:'float', dflt:4.0, step:0.5},
      {key:'pass_teammate_bonus', label:'过牌队友可接奖励', type:'float', dflt:3.0, step:0.5},
      {key:'pass_control_bonus', label:'过牌本队控权奖励', type:'float', dflt:2.0, step:0.5},
      {key:'pass_neutral', label:'过牌基线分', type:'float', dflt:0.0, step:0.5},
    ]
  },
  round: {
    name: 'Round 轮次评分',
    desc: '基于精确轮次估计(estimate_rounds)，评估每个候选对团队轮次差距的影响。',
    flow: `1. 计算当前团队轮次和对手轮次基线
   · 己方总轮次 = Σ队友 estimate_rounds(手牌)
   · 对手总轮次 = Σ对手 estimate_rounds(手牌)
   · 差距 = 对手轮次 - 己方轮次
2. 对每个候选模拟打出后的状态：
   · 己方轮次变化 = 打出前 - 打出后（正值=改善）
   · 差距变化 = 新差距 - 旧差距（负值=追近）
   · round_score = 轮次变化 × round_delta_weight
   · gap_score = 差距变化 × gap_improve_weight（追近为负→扣分或加分取方向）
3. 控权检查（同Informed）：
   · 无人能压 → +no_counter_bonus
   · 队友能压 → +teammate_cover_bonus
   · 对手能压 → -opponent_counter_penalty
4. 过牌评分：
   · 队友可接 → +pass_teammate_bonus
   · 其他 → pass_default
5. 按总分降序排列，取最高分`,
    params: [
      {key:'round_delta_weight', label:'轮次改善权重', type:'float', dflt:8.0, step:0.5},
      {key:'gap_improve_weight', label:'差距改善权重', type:'float', dflt:3.0, step:0.5},
      {key:'no_counter_bonus', label:'无人能压奖励', type:'float', dflt:6.0, step:0.5},
      {key:'teammate_cover_bonus', label:'队友可接奖励', type:'float', dflt:2.0, step:0.5},
      {key:'opponent_counter_penalty', label:'对手能压惩罚', type:'float', dflt:2.0, step:0.5},
      {key:'pass_teammate_bonus', label:'过牌队友可接奖励', type:'float', dflt:4.0, step:0.5},
      {key:'pass_default', label:'过牌基线', type:'float', dflt:1.0, step:0.5},
    ]
  },
  exact: {
    name: 'Exact 精确求解',
    desc: '终局(≤6张)极小化极大搜索。穷举所有可能的出牌序列，找理论最优解。无权重，纯计算。',
    flow: `1. 检查手牌总数：≤24张（每家≤6）才可求解
2. 生成候选牌型（同Informed）
3. 对每个候选（含过牌），递归极小化极大搜索：
   · 己方出牌后，轮到下一家
   · 己方队伍节点：选最大收益
   · 对手队伍节点：选最小收益（对我们最不利）
   · 终局条件：≥3人完成 → 根据名次判定胜负
   · 搜索深度限制：max_depth（默认20）
   · 超时保护：time_limit_ms
4. 返回每个候选的理论精确值（归一化到0-1）
5. 选最高分候选`,
    params: [
      {key:'max_depth', label:'最大搜索深度', type:'int', dflt:20, step:1},
      {key:'max_cards', label:'最大手牌数', type:'int', dflt:6, step:1},
      {key:'time_limit_ms', label:'超时限制(ms)', type:'int', dflt:20000, step:1000},
    ]
  },
  blind: {
    name: 'Blind 盲评',
    desc: '只看自己手牌，不看对手信息，用12个AIParams权重做启发式打分。作为AI智能的基线。',
    flow: `1. 生成候选牌型（同Informed/HeuristicAgent）
2. 对每个候选（含过牌）计算加权得分：
   · 效率分 = (牌型张数/总张数) × efficiency_weight
   · 炸弹处理：
     - 首家出炸 → bomb_lead_penalty（默认扣12分）
     - 炸非炸 → bomb_overuse_penalty（默认扣10分）
     - 炸对炸 → bomb_vs_bomb_bonus（+2分）
   · 位置分：首家 +lead_bonus，跟牌 +follow_bonus
   · 出牌利用率：牌型张数 × card_usage_weight
   · 特殊惩罚：Joker首发 -joker_lead_penalty，K+首发 penalty
3. 过牌判断：最优候选分 < pass_threshold → 过牌
4. 按总分降序输出`,
    params: [
      {key:'efficiency_weight', label:'效率权重', type:'float', dflt:20.0, step:1.0},
      {key:'round_weight', label:'轮次权重', type:'float', dflt:8.0, step:0.5},
      {key:'bomb_lead_penalty', label:'首家出炸弹惩罚', type:'float', dflt:-12.0, step:1.0},
      {key:'bomb_overuse_penalty', label:'有过炸惩罚', type:'float', dflt:-10.0, step:1.0},
      {key:'bomb_vs_bomb_bonus', label:'炸弹对炸弹奖励', type:'float', dflt:2.0, step:0.5},
      {key:'lead_bonus', label:'首先出牌奖励', type:'float', dflt:1.0, step:0.5},
      {key:'follow_bonus', label:'跟牌奖励', type:'float', dflt:1.0, step:0.5},
      {key:'joker_lead_penalty', label:'Joker首发惩罚', type:'float', dflt:-1.0, step:0.5},
      {key:'high_rank_lead_penalty', label:'高牌(K+)首发惩罚', type:'float', dflt:-0.3, step:0.1},
      {key:'card_usage_weight', label:'出牌利用率权重', type:'float', dflt:0.3, step:0.1},
      {key:'pass_threshold', label:'过牌阈值', type:'float', dflt:0.0, step:0.5},
      {key:'sim_pass_prob', label:'模拟过牌概率', type:'float', dflt:0.15, step:0.05},
    ]
  },
  mc: {
    name: 'MC 蒙特卡洛',
    desc: '采样对手手牌 → 内层模型模拟至终局 → 统计胜率。通过大量随机采样逼近真实期望。',
    flow: `1. 候选生成（由enumerator控制）：
   · full: 穷举所有合法牌型
   · top_n: 启发式预筛TopN
   · memory: 记忆感知排序
2. 对每个候选，执行 num_samples 次模拟：
   a. 采样(sampler)：从unseen牌堆随机分配对手手牌
      - random: 纯随机分配
      - constrained: 约束采样（利用已知信息）
   b. 模拟(inner)：用内层模型模拟剩余游戏
      - Blind/Informed/Round/Exact 逐步决策至终局
   c. 统计：我方队伍胜利 → win+1
3. 胜率 = wins / samples → 投票选出最优候选
4. 超时保护：time_limit_ms`,
    params: [
      {key:'num_samples', label:'采样次数', type:'int', dflt:128, step:16},
      {key:'time_limit_ms', label:'超时限制(ms)', type:'int', dflt:10000, step:1000},
      {key:'sampler', label:'采样器', type:'select', dflt:'random', opts:{random:'Random 纯随机',constrained:'Constrained 约束'}},
      {key:'inner', label:'内层模型', type:'ref', dflt:'informed', refs:['blind','informed','round','exact']},
      {key:'enumerator', label:'候选枚举', type:'ref', dflt:'full', refs:['full','top_n','memory']},
    ]
  },
  ismcts: {
    name: 'IS-MCTS 树搜索',
    desc: 'UCB 候选评审：对每个候选模拟计胜率，按访问次数选优。（如实说明：当前实现为 1 层 UCB 评审，未做深层树展开，行为接近逐候选 MC。）',
    flow: `1. 枚举根候选（由enumerator控制）并全部加入根节点
2. 循环 max_iterations 次（或超时）：
   a. 选择(Select): 按UCB公式挑一个候选子节点
   b. 模拟(Simulate): 用内层模型从该候选模拟至终局
   c. 回传(Backpropagate): 更新该候选的 wins/visits
3. 返回根候选的胜率统计
4. 选访问次数最多的候选作为推荐`,
    params: [
      {key:'max_iterations', label:'最大迭代', type:'int', dflt:500, step:50},
      {key:'time_limit_ms', label:'超时限制(ms)', type:'int', dflt:10000, step:1000},
      {key:'ucb_c', label:'UCB探索常数', type:'float', dflt:1.4, step:0.1},
      {key:'sampler', label:'采样器', type:'select', dflt:'random', opts:{random:'Random 纯随机',constrained:'Constrained 约束'}},
      {key:'inner', label:'内层模型', type:'ref', dflt:'informed', refs:['blind','informed','round','exact']},
      {key:'enumerator', label:'候选枚举', type:'ref', dflt:'full', refs:['full','top_n','memory']},
    ]
  },
  top_n: {
    name: 'TopN 预筛枚举',
    desc: '预评分后取前N个候选，减少MC采样空间。',
    flow: `1. 用预评分器(pre_scorer)对所有候选打分
2. 取前 top_n 个候选返回`,
    params: [
      {key:'top_n', label:'保留候选数', type:'int', dflt:5, step:1},
      {key:'pre_scorer', label:'预评分器', type:'select', dflt:'blind', opts:{blind:'Blind'}},
    ]
  },
  memory: {
    name: 'Memory 记忆感知枚举',
    desc: '根据过牌历史调整候选顺序，无额外参数。',
    flow: `1. 枚举所有合法候选
2. 根据历史过牌记录调整顺序（避免重复出已被压制的牌型）
3. 返回有序候选列表`,
    params: []
  },
  full: {
    name: 'Full 全面枚举',
    desc: '穷举所有同类型可出牌型+炸弹，无参数。',
    flow: `1. 枚举所有合法同类型跟牌
2. 附加炸弹选项
3. 返回完整候选列表`,
    params: []
  },
};

// ==================================================================
// Floating panel management
// ==================================================================
let _panelCtx = null;  // {tab:'P'|'T', profileIdx:number, modelId:string}

function _renderParamGrid(params, prof, prefix) {
  let html = '';
  for (const p of params) {
    const pkey = prefix ? prefix+'.'+p.key : p.key;
    const val = prof[pkey] !== undefined ? prof[pkey] : (prof[p.key] !== undefined ? prof[p.key] : p.dflt);
    if (p.type === 'select') {
      const opts = p.opts || {};
      html += `<div class="param-row"><span class="pk">${p.label}</span><select onchange="updatePanelParam('${pkey}',this.value)">`;
      for (const [v,label] of Object.entries(opts)) {
        html += `<option value="${v}"${String(val)===v?' selected':''}>${label}</option>`;
      }
      html += `</select></div>`;
    } else if (p.type === 'ref') {
      const refs = p.refs || [];
      const refLabels = {blind:'Blind',informed:'Informed',round:'Round',exact:'Exact',full:'全面枚举',top_n:'预筛TopN',memory:'记忆感知'};
      html += `<div class="param-row"><span class="pk">${p.label}</span><select onchange="updatePanelParam('${pkey}',this.value)">`;
      for (const r of refs) {
        html += `<option value="${r}"${String(val)===r?' selected':''}>${refLabels[r]||r}</option>`;
      }
      html += `</select></div>`;
    } else {
      const step = p.step || (p.type==='int'?1:0.5);
      html += `<div class="param-row"><span class="pk">${p.label}</span>`;
      html += `<button class="step" onclick="stepPanelParam('${pkey}',${-step})">−</button>`;
      html += `<input type="number" value="${val}" step="${step}" onchange="updatePanelParam('${pkey}',parseFloat(this.value))" id="pp-${pkey.replace('.','-')}">`;
      html += `<button class="step" onclick="stepPanelParam('${pkey}',${step})">+</button>`;
      html += `</div>`;
    }
  }
  return html;
}

function openModelPanel(tab, idx, modelId) {
  const info = MODEL_INFO[modelId];
  if (!info) return;
  _panelCtx = {tab, idx, modelId};

  const prof = (tab==='P'?P:T).profs[idx] || {};

  let html = `<h2>${info.name}<button onclick="closeModelPanel()">✕</button></h2>`;
  html += `<div style="color:#888;font-size:0.78em;margin-bottom:6px">${info.desc}</div>`;
  html += `<h3>算法流程</h3><div class="alg-flow">${info.flow}</div>`;
  html += `<h3>可调参数</h3><div class="param-grid">`;
  html += _renderParamGrid(info.params, prof, '');
  html += `</div>`;

  // For MC/ISMCTS, also show inner model and enumerator params
  if (modelId === 'mc' || modelId === 'ismcts') {
    const innerId = prof.inner || 'informed';
    const innerInfo = MODEL_INFO[innerId];
    if (innerInfo) {
      html += `<h3>内层模型: ${innerInfo.name} 参数</h3><div class="param-grid">`;
      html += _renderParamGrid(innerInfo.params, prof, 'inner');
      html += `</div>`;
    }
    const enumId = prof.enumerator || 'full';
    const enumInfo = MODEL_INFO[enumId];
    if (enumInfo && enumInfo.params && enumInfo.params.length) {
      html += `<h3>枚举器: ${enumInfo.name} 参数</h3><div class="param-grid">`;
      html += _renderParamGrid(enumInfo.params, prof, 'enumerator');
      html += `</div>`;
    }
  }

  html += `<div class="panel-actions">
    <button class="btn btn-gy btn-sm" onclick="resetPanelParams()">重置默认</button>
    <button class="btn btn-gn btn-sm" onclick="applyPanelParams()">应用</button>
    <button class="btn btn-gy btn-sm" onclick="closeModelPanel()">关闭</button>
  </div>`;

  document.getElementById('mdl-panel').innerHTML = html;
  document.getElementById('mdl-overlay').classList.add('on');
}

function closeModelPanel() {
  document.getElementById('mdl-overlay').classList.remove('on');
  _panelCtx = null;
}

function updatePanelParam(key, val) {
  if (!_panelCtx) return;
  const prof = (_panelCtx.tab==='P'?P:T).profs[_panelCtx.idx];
  if (!prof) return;
  // Handle dotted keys like "inner.round_weight"
  const parts = key.split('.');
  let target = prof;
  for (let i = 0; i < parts.length - 1; i++) {
    if (target[parts[i]] === undefined) target[parts[i]] = {};
    target = target[parts[i]];
  }
  target[parts[parts.length - 1]] = val;
}

function stepPanelParam(key, delta) {
  if (!_panelCtx) return;
  const prof = (_panelCtx.tab==='P'?P:T).profs[_panelCtx.idx];
  if (!prof) return;
  // Handle dotted keys like "inner.round_weight"
  const parts = key.split('.');
  let target = prof;
  for (let i = 0; i < parts.length - 1; i++) {
    if (target[parts[i]] === undefined) target[parts[i]] = {};
    target = target[parts[i]];
  }
  const lastKey = parts[parts.length - 1];
  // Find default from MODEL_INFO
  let dflt = 0;
  if (parts.length === 1) {
    dflt = MODEL_INFO[_panelCtx.modelId]?.params.find(p=>p.key===key)?.dflt || 0;
  } else {
    const subInfo = MODEL_INFO[parts[0]];
    if (subInfo) dflt = subInfo.params.find(p=>p.key===lastKey)?.dflt || 0;
  }
  const cur = target[lastKey] !== undefined ? target[lastKey] : dflt;
  target[lastKey] = Math.round((cur + delta) * 100) / 100;
  const inpId = 'pp-' + key.replace('.','-');
  const inp = document.getElementById(inpId);
  if (inp) inp.value = target[lastKey];
}

function applyPanelParams() {
  if (!_panelCtx) return;
  // Re-render profiles to show updated param preview
  if (_panelCtx.tab === 'P') renderProfsP(); else renderProfsT();
  closeModelPanel();
}

function _resetParamsForInfo(info, prof, prefix) {
  if (!info) return;
  for (const p of info.params) {
    const pkey = prefix ? prefix+'.'+p.key : p.key;
    const parts = pkey.split('.');
    let target = prof;
    for (let i = 0; i < parts.length - 1; i++) {
      if (target[parts[i]] === undefined) target[parts[i]] = {};
      target = target[parts[i]];
    }
    target[parts[parts.length - 1]] = p.dflt;
    const inpId = 'pp-' + pkey.replace('.','-');
    const inp = document.getElementById(inpId);
    if (inp) inp.value = p.dflt;
  }
}

function resetPanelParams() {
  if (!_panelCtx) return;
  const info = MODEL_INFO[_panelCtx.modelId];
  if (!info) return;
  const prof = (_panelCtx.tab==='P'?P:T).profs[_panelCtx.idx];
  if (!prof) return;
  _resetParamsForInfo(info, prof, '');
  // Also reset inner/enumerator params for MC/ISMCTS
  const modelId = _panelCtx.modelId;
  if (modelId === 'mc' || modelId === 'ismcts') {
    const innerInfo = MODEL_INFO[prof.inner || 'informed'];
    _resetParamsForInfo(innerInfo, prof, 'inner');
    const enumInfo = MODEL_INFO[prof.enumerator || 'full'];
    _resetParamsForInfo(enumInfo, prof, 'enumerator');
  }
}

// ==================================================================
// Init
// ==================================================================
async function init() {
  try {
    const [mr, sr] = await Promise.all([fetch('/api/arena/models'), fetch('/api/arena/scenarios')]);
    const md = await mr.json(), sd = await sr.json();
  schema = {decider:{},inner_model:{},enumerator:{}};
  (md.outer||[]).forEach(m => { schema.decider[m.id]=m; allM[m.id]=m; });
  (md.inner||[]).forEach(m => { schema.inner_model[m.id]=m; allM[m.id]=m; });
  (md.enumerator||[]).forEach(m => schema.enumerator[m.id]=m);
  scens = (sd.scenarios||[]).sort((a,b)=>a.category.localeCompare(b.category)||a.name.localeCompare(b.name));
  cats = sd.categories||[];

  // Init custom tab
  switchTab('custom');
  setProfsC();
  renderCardPool();

  // Tab 1 — 完全信息：仅确定推演
  const ps = document.getElementById('pcat');
  ps.innerHTML = cats.filter(c=>c==='deduction').map(c=>`<option value="${c}">${CCN[c]||c}</option>`).join('');
  ps.value='deduction'; loadScP(); setProfsP();
  const firstP = scens.find(s=>s.category==='deduction') || scens[0];
  if (firstP) selP(firstP.id);

  // Tab 2 — 真实牌桌：目前题库只有 deduction 场景；类别下拉改为展示
  // 后端真实存在的类别（空壳类别曾让场景列表恒「无场景」、基准恒「无结果」）。
  const ts = document.getElementById('tcat');
  const tCats = cats.length ? cats : ['deduction'];
  ts.innerHTML = tCats.map(c=>`<option value="${c}">${CCN[c]||c}</option>`).join('');
  ts.value = tCats.includes('sampling') ? 'sampling' : tCats[0];
  T.cat = ts.value; loadScT(); setProfsT();
  const firstT = scens.find(s=>s.category===ts.value) || scens[0];
  if (firstT) selT(firstT.id);

  document.getElementById('persp-tabs').innerHTML = [0,1,2,3].map(i=>`<button class="ptab${i===0?' on':''}" onclick="swPersp(${i})">${PN[i]}</button>`).join('');
  } catch (e) {
    // A dead server used to leave the whole page silently uninitialized.
    const el = document.getElementById('arena-app') || document.body;
    el.insertAdjacentHTML('afterbegin',
      `<div style="background:#5c1a1a;color:#fff;padding:8px 14px;border-radius:6px;margin:8px">测试场初始化失败: ${e.message} — 请确认服务器正在运行后刷新页面</div>`);
  }
}

function switchTab(tab) {
  const tabMap = {custom:0, perfect:1, table:2};
  document.querySelectorAll('#arena-tabs .atab').forEach((b,i)=>b.classList.toggle('on',i===tabMap[tab]));
  document.querySelectorAll('.arena-row').forEach(el=>el.classList.toggle('on',el.id==='row-'+tab));
  document.getElementById('btn-dbg2').style.display = tab==='table'?'':'none';
}

// ==================================================================
// Scenario list helper
// ==================================================================
function renderScList(cid, cat, sid, fn) {
  const list = cat ? scens.filter(s=>s.category===cat) : scens;
  document.getElementById(cid).innerHTML = list.map(s=>
    `<div class="sc-item${sid===s.id?' sel':''}" onclick="${fn}('${s.id}')"><span class="sc">${CCN[s.category]||s.category}</span><span class="sn">${s.name}</span><span class="si">${s.hand_size}张${s.has_table?' · 桌面':''}</span></div>`).join('')||'<span class="emp">无场景</span>';
}

// ==================================================================
// Game-table renderer (reuses game.html zone layout)
//
// Two modes:
//   - sim (tab 确定推演): st is a simulator state; the center panel offers
//     manual play/pass + 分析 + 编辑.
//   - scenario (tab 真实牌桌): st is synthesized from the scenario detail;
//     the center panel offers 分析 only (manual steps never made sense here
//     and used to act on the OTHER tab's simulator).
// ==================================================================
function buildTable(containerId, st, opts) {
  const o = opts || {mode: 'sim'};
  const el = document.getElementById(containerId);
  if (!el || !st) return;
  const pls = st.players||[];
  const names = ['你','右家','对家','左家'];
  const th = st.trick_history||[];
  const cp = st.current_player;
  const isMyTurn = cp === 0;

  // Build per-player latest action from trick_history
  const lastAction = {};
  for (const e of th) { lastAction[e.player] = e; }

  let html = '';
  for (let p = 0; p <= 3; p++) {
    const pos = p===0?'you':p===1?'right':p===2?'partner':'left';
    const pi = pls[p]||{};
    const cards = pi.hand||[];
    const thinking = cp===p ? ' thinking-zone' : '';

    html += `<div class="player-zone ${pos}${thinking}" id="az-${containerId}-${p}">`;
    html += `<div class="zone-header"><span class="name${p===0?' you':''}">${names[p]}</span><span class="hand-size">${pi.hand_size||cards.length}</span>张${pi.finished?' <span class="finished-tag" style="display:inline">已完成</span>':''}</div>`;
    if (cards.length) {
      html += `<div class="ahc" id="ahc-${containerId}-${p}">${scs(cards).map(c => {
        const sel = P.selected.has(c.id) ? ' selected' : '';
        const clickable = (o.mode === 'sim' && cp===p) ? ' clickable' : '';
        const click = (o.mode === 'sim' && cp===p) ? ` data-cid="${c.id}" onclick="toggleArenaCard(${c.id})"` : '';
        return ac(c, sel+clickable, click);
      }).join('')}</div>`;
    }
    html += `</div>`;
  }

  // Play areas (状态区)
  const paPrefix = containerId === 'tbl-p' ? 'p' : 't';
  html += `<div class="cz-play-area you" id="${paPrefix}a-0"><span class="cz-pcards" id="${paPrefix}pcard-0"></span></div>`;
  html += `<div class="cz-play-area right" id="${paPrefix}a-1"><span class="cz-pcards" id="${paPrefix}pcard-1"></span></div>`;
  html += `<div class="cz-play-area partner" id="${paPrefix}a-2"><span class="cz-pcards" id="${paPrefix}pcard-2"></span></div>`;
  html += `<div class="cz-play-area left" id="${paPrefix}a-3"><span class="cz-pcards" id="${paPrefix}pcard-3"></span></div>`;

  // Center: mode-aware
  const infoBlock = `<div class="tinfo">级牌 <strong>Lv${st.level||2}</strong> · 回合${st.trick_number||1}</div>
    <div style="font-size:0.75em;color:#888">轮到: <b style="color:#daa520">${names[cp]}</b></div>
    ${st.can_pass ? '<div style="font-size:0.65em;color:#888">可过牌</div>' : ''}`;
  let centerActions;
  if (o.mode === 'scenario') {
    centerActions = `
    <div style="display:flex;gap:6px;justify-content:center;margin-top:4px">
      <button class="btn btn-g btn-sm" onclick="runT()">分析</button>
    </div>`;
  } else {
    centerActions = `
    <div style="display:flex;gap:6px;justify-content:center;margin-top:4px">
      <button class="btn btn-gn btn-sm" onclick="manualPlay()">出牌</button>
      ${st.can_pass ? '<button class="btn btn-gy btn-sm" onclick="manualPass()">过牌</button>' : ''}
    </div>
    <div style="display:flex;gap:6px;justify-content:center;margin-top:4px">
      <button class="btn btn-g btn-sm" onclick="runP()">分析</button>
      <button class="btn btn-gy btn-sm" onclick="customLoadFromSim(P.simState)">编辑</button>
    </div>`;
  }
  html += `<div class="tbl-ctr"><div style="background:rgba(0,0,0,0.3);border-radius:8px;padding:6px 8px;display:flex;flex-direction:column;align-items:center;gap:4px;font-size:0.78em">
    ${infoBlock}
    ${centerActions}
  </div></div>`;
  el.innerHTML = html;

  // Update play areas (状态区)
  for (let p = 0; p <= 3; p++) {
    renderPlayState(p, lastAction[p], cp, paPrefix + 'pcard-' + p);
  }
}

function toggleArenaCard(cid) {
  if (P.selected.has(cid)) P.selected.delete(cid); else P.selected.add(cid);
  document.querySelectorAll(`.card[data-cid="${cid}"]`).forEach(el => {
    el.classList.toggle('selected', P.selected.has(cid));
  });
}

function manualPlay() {
  if (!P.simId || P.selected.size===0) return;
  stepP(Array.from(P.selected), false);
}

function manualPass() {
  if (!P.simId) return;
  stepP([], true);
}

// ==================================================================
// Tab 1 — Profiles
// ==================================================================
function setProfsP() {
  P.profs = [
    {id:'informed',label:'Informed'},
    {id:'round',label:'Round'},
    {id:'exact',label:'Exact',time_limit_ms:20000},
    {id:'blind',label:'Blind'},
  ];
  renderProfsP();
}
function addProfP() { P.profs.push({id:'informed',label:'New'}); renderProfsP(); }
function rmProfP(i) { P.profs.splice(i,1); renderProfsP(); }
function upProfP(i,k,v) { P.profs[i][k]=v; renderProfsP(); }

function _paramPreview(prof) {
  const info = MODEL_INFO[prof.id];
  if (!info) return '';
  const parts = [];
  for (const p of info.params) {
    const val = prof[p.key];
    if (val !== undefined && val !== p.dflt) {
      parts.push(`${p.label}=${val}`);
    }
  }
  // Include inner model params for MC/ISMCTS
  if (prof.id === 'mc' || prof.id === 'ismcts') {
    const innerId = prof.inner || 'informed';
    const innerInfo = MODEL_INFO[innerId];
    if (innerInfo) {
      for (const p of innerInfo.params) {
        const val = prof['inner.'+p.key];
        if (val !== undefined && val !== p.dflt) {
          parts.push(`内.${p.label}=${val}`);
        }
      }
    }
  }
  return parts.length ? parts.slice(0,5).join(', ') + (parts.length>5?'…':'') : '';
}

function renderProfsP() {
  const ids = ['informed','round','exact','blind'];
  const opts = ids.map(id=>`<option value="${id}">${(MODEL_INFO[id]||{}).name||id}</option>`).join('');
  document.getElementById('profs-p').innerHTML = P.profs.map((p,i)=>{
    const sel = opts.replace(`value="${p.id}"`,`value="${p.id}" selected`);
    const preview = _paramPreview(p);
    return `<div class="prow"><div class="pn"><input type="text" value="${p.label||''}" onchange="upProfP(${i},'label',this.value)" placeholder="名称"><select onchange="upProfP(${i},'id',this.value);renderProfsP()">${sel}</select><button class="btn btn-gy btn-sm" style="padding:0 5px;font-size:0.7em" onclick="openModelPanel('P',${i},'${p.id}')" title="算法介绍与参数">ⓘ</button><button onclick="rmProfP(${i})">×</button></div>${preview?`<div class="param-preview">${preview}</div>`:''}</div>`;
  }).join('')+`<button class="btn btn-gy btn-sm" style="margin-top:2px" onclick="addProfP()">+ 模型</button>`;
}

// ==================================================================
// Tab 1 — Select scenario
// ==================================================================
// Tab 1 — Scenario select → init simulator
// ==================================================================
async function loadScP() { P.cat=document.getElementById('pcat').value; renderScList('sc-list',P.cat,P.sid,'selP'); }
async function selP(id) {
  P.sid=id; P.res=[]; P.selected.clear(); loadScP();
  // Kill any in-flight analysis and invalidate stale responses: results
  // from the previous scenario used to render under the new one.
  if (P.running) stopP();
  P.reqSeq = (P.reqSeq||0) + 1;
  const seq = P.reqSeq;
  const pres = document.getElementById('pres');
  pres.innerHTML='<span class="emp">初始化模拟...</span>'; document.getElementById('prcnt').textContent='';
  try {
    const r = await fetch('/api/arena/sim/init',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario_id:id})});
    const j = await r.json();
    if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
    if (seq !== P.reqSeq) return;   // superseded by a newer selection
    P.simState = j;
    P.simId = j.sim_id;
    P.res=[];
    pres.innerHTML='<span class="emp">点击分析</span>';
    buildTable('tbl-p', P.simState);
    // Scenario info
    const s = await (await fetch('/api/arena/scenarios/'+id)).json();
    document.getElementById('dbg-sc-p').innerHTML=`<div><b>${s.name}</b> <span style="color:#888">${s.category}</span></div><div style="color:#aaa;white-space:pre-wrap">${s.description||''}</div>`+(s.reasoning?`<div style="color:#daa520;margin-top:2px">预期: ${s.reasoning}</div>`:'');
  } catch (e) {
    if (seq !== P.reqSeq) return;
    P.simState = null; P.simId = null;
    pres.innerHTML = `<span style="color:#e74c3c;font-size:0.82em">初始化失败: ${e.message}</span>`;
  }
}

// ==================================================================
// Tab 1 — Step (execute chosen play/pass)
// ==================================================================
async function stepP(cardIds, pass) {
  if (!P.simId) return;
  if (P.stepping) return;   // in-flight guard: double-click used to apply two steps
  const hadRunning = !!(P.running && P.abort);
  if (hadRunning) stopP();
  P.stepping = true;
  P._suppressRender = true;
  P.res = [];
  const presEl = document.getElementById('pres');
  presEl.innerHTML = hadRunning ? '<div style="color:#e67e22;font-size:0.78em;padding:4px;margin-bottom:4px;background:rgba(230,126,34,0.08);border-radius:4px">已放弃等待中的分析</div>' : '<span class="emp">已执行，点击分析继续</span>';
  document.getElementById('prcnt').textContent = '';

  try {
    const body = {sim_id:P.simId, pass:!!pass};
    if (!pass) body.card_ids = cardIds;
    const r = await fetch('/api/arena/sim/step',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const newState = await r.json();
    if (newState.error) {
      presEl.innerHTML = `<span style="color:#e74c3c;font-size:0.82em">${newState.error}</span>` + presEl.innerHTML;
      return;
    }
    P.simState = newState;
    P.selected.clear();
    buildTable('tbl-p', P.simState);
  } catch (e) {
    presEl.innerHTML = `<span style="color:#e74c3c;font-size:0.82em">请求失败: ${e.message}</span>` + presEl.innerHTML;
  } finally {
    P.stepping = false;
    P._suppressRender = false;
  }
}

// ==================================================================
// Tab 1 — Analysis (non-blocking, with stop)
// ==================================================================
async function runP() {
  if (!P.simId) return;
  if (P.running) { stopP(); return; }
  P.running=true; P.res=[]; P.abort=new AbortController();
  P.runSeq = (P.runSeq||0) + 1;
  const seq = P.runSeq;
  document.getElementById('pres').innerHTML=''; const btn=document.getElementById('btn-ap');
  btn.textContent='停止'; btn.className='btn btn-red btn-sm';
  // Pass all profile params through to API
  const models = P.profs.map(p=>{const m={id:p.id,label:p.label};for(const k of Object.keys(p)){if(k!=='id'&&k!=='label')m[k]=p[k];}return m;});
  document.getElementById('prcnt').textContent=`(0/${models.length})`;
  for (const m of models) {   // serial: analyze clears a global log buffer per request, parallel calls clobbered each other's debug logs
    try {
      const r = await fetch('/api/arena/sim/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sim_id:P.simId,models:[m]}),signal:P.abort.signal});
      if (seq !== P.runSeq) return;    // scene changed mid-run
      if (!r.ok) P.res.push({model_id:m.id,model_name:m.label||m.id,error:'HTTP '+r.status});
      else { const d=await r.json(); if(d.results)P.res.push(...d.results); }
    } catch(e) {
      if (e.name==='AbortError') break;
      if (seq !== P.runSeq) return;
      P.res.push({model_id:m.id,model_name:m.label||m.id,error:e.message});
    }
    if (seq !== P.runSeq) return;
    renderResP(); document.getElementById('prcnt').textContent=`(${P.res.length}/${models.length})`;
  }
  if (seq !== P.runSeq) return;
  finishP();
}
function stopP() { if(P.abort){P.abort.abort();P.abort=null;} finishP(); }
function finishP() { P.running=false; const btn=document.getElementById('btn-ap'); btn.textContent='分析'; btn.className='btn btn-gn btn-sm'; }
function _renderDetail(detail, modelId) {
  if (!detail) return '';
  let h = '';
  // Score breakdown (Informed/Round/Blind)
  if (detail.round_score !== undefined || detail.total_score !== undefined) {
    h += '<div style="font-size:0.95em;color:#ddd;margin-top:4px;margin-bottom:2px;border-top:1px solid rgba(255,255,255,0.08);padding-top:4px;line-height:1.5">';
    if (detail.rounds_before !== undefined) h += `轮次:${detail.rounds_before}→${detail.rounds_after}  `;
    if (detail.round_score !== undefined) h += `轮次分:<b style="color:#f1c40f">${detail.round_score}</b>  `;
    if (detail.gap_score !== undefined) h += `差距分:<b style="color:#f1c40f">${detail.gap_score}</b>  `;
    if (detail.counter_label) h += `控权:<b style="color:${detail.counter_score>0?'#27ae60':'#e74c3c'}">${detail.counter_label}(${detail.counter_score})</b>  `;
    if (detail.bomb_label) h += `炸弹:<b style="color:#e67e22">${detail.bomb_label}(${detail.bomb_penalty})</b>  `;
    if (detail.total_score !== undefined) h += `→ 总分:<b style="color:#27ae60;font-size:1.05em">${detail.total_score}</b>`;
    h += '</div>';
  }
  // Team/opp round info (RoundScorer specific)
  if (detail.team_before !== undefined) {
    h += `<div style="font-size:0.92em;color:#bbb;line-height:1.5">队轮:<b style="color:#f1c40f">${detail.team_before}→${detail.team_after}</b>  敌轮:${detail.opp_before}→${detail.opp_after}  差距:<b style="color:${detail.gap_after>0?'#e74c3c':'#27ae60'}">${detail.gap_before}→${detail.gap_after}</b></div>`;
  }
  // Exact solver stats
  if (detail.nodes_searched !== undefined) {
    h += `<div style="font-size:0.95em;color:#ddd;margin-top:4px;border-top:1px solid rgba(255,255,255,0.08);padding-top:4px;line-height:1.5">搜索:<b style="color:#f1c40f">${detail.nodes_searched}</b>节点  深度:<b style="color:#f1c40f">${detail.max_depth_reached}</b>  终局:<b style="color:#f1c40f">${detail.terminal_evals}</b>${detail.timed_out?'  ⚠超时':''}  精确值:<b style="color:#27ae60;font-size:1.05em">${detail.exact_value!==undefined?detail.exact_value:'?'}</b></div>`;
  }
  // MC sample stats
  if (detail.samples !== undefined && detail.wins !== undefined) {
    const wr = detail.samples>0?Math.round(detail.wins/detail.samples*100):0;
    h += `<div style="font-size:0.95em;color:#ddd;line-height:1.5">采样:<b style="color:#27ae60">${detail.wins}/${detail.samples}</b> (胜率<b style="color:#f1c40f">${wr}%</b>)  引擎:${detail.sim_agent||'?'}</div>`;
  }
  // ISMCTS visit stats
  if (detail.visits !== undefined) {
    const wr = detail.visits>0?Math.round(detail.wins/detail.visits*100):0;
    h += `<div style="font-size:0.95em;color:#ddd;line-height:1.5">访问:<b style="color:#27ae60">${detail.wins}/${detail.visits}</b> (胜率<b style="color:#f1c40f">${wr}%</b>)  UCB C=${detail.ucb_c}</div>`;
  }
  return h;
}

function renderResP() {
  if (P._suppressRender) return;
  const el=document.getElementById('pres'); if(!P.res.length){el.innerHTML='<span class="emp">无结果</span>';return;}
  // Remember which model detail sections are open
  const openModels = new Set();
  el.querySelectorAll('.mres .mcand[open]').forEach(d => {
    const mn = d.closest('.mres')?.querySelector('.mn')?.textContent;
    if (mn) openModels.add(mn);
  });
  el.innerHTML = P.res.map(r=>{
    if(r.error) return `<div class="mres"><div class="mn">${r.model_name||r.model_id}</div><div class="me">${r.error}</div></div>`;
    const m=r.metrics||{},ch=r.choice;
    const isPass = r.pass_chosen;
    const chCards = ch?ch.card_ids||[]:[];
    let chH = isPass?'<span style="color:#e67e22;font-weight:bold">过牌</span>':(ch?`<span class="combo-tag" style="background:#daa520;color:#222;padding:1px 4px;border-radius:3px;font-size:0.7em">${ch.combo_type||'?'}</span> ${(ch.cards||[]).join(' ')}`:'?');
    let stepBtn = `<button class="btn btn-gn btn-sm" onclick="stepP(${JSON.stringify(chCards)},${isPass})" style="margin-left:6px">执行</button>`;
    const cds=r.candidates||[];
    const mn = r.model_name||r.model_id;
    const openAttr = openModels.has(mn) ? ' open' : '';
    let cdHtml = cds.length?`<details class="mcand"${openAttr}><summary>候选 (${cds.length})</summary>`+cds.map((c,i)=>{const best=i===0;const cad=c.card_ids||[];const isCp=c.combo_type==='PASS';
      const detHtml = _renderDetail(c.detail, r.model_id);
      return `<div class="crow${best?' best':''}"><div class="cr-main">${isCp?'<span style="color:#e67e22;font-weight:bold">过牌</span>':`<span class="combo-tag" style="background:#daa520;color:#222;padding:1px 2px;border-radius:2px;font-size:0.62em">${c.combo_type||'?'}</span> ${(c.cards||[]).join(' ')}`}<span style="color:#aaa;font-size:0.78em;margin-left:auto">${c.score!=null?c.score.toFixed(1):''}${c.win_rate!=null?' | '+(c.win_rate*100).toFixed(1)+'%':''}</span><button class="btn btn-gn btn-sm" onclick="stepP(${JSON.stringify(cad)},${isCp})" style="margin-left:4px;font-size:0.65em;padding:1px 5px">执行</button></div>${detHtml?`<div class="cr-detail">${detHtml}</div>`:''}</div>`}).join('')+'</details>':'';
    return `<div class="mres"><div class="mh"><span class="mn">${mn}</span><span class="mt">${m.elapsed_ms!=null?m.elapsed_ms.toFixed(0)+'ms':''}${m.timed_out?' ⚠':''}</span></div><div class="mc">${chH} ${ch&&ch.score!=null?`<span class="mw">${ch.score.toFixed(1)}</span>`:''} ${ch&&ch.win_rate!=null?`<span class="mw">${(ch.win_rate*100).toFixed(1)}%</span>`:''} ${stepBtn}</div>${cdHtml}</div>`;
  }).join('');
}

// ==================================================================
// Tab 2 — Profiles
// ==================================================================
function setProfsT() {
  T.profs = [
    {id:'mc',label:'MC+Informed+Full',num_samples:100,time_limit_ms:8000,inner:'informed',enumerator:'full',sampler:'random'},
    {id:'blind',label:'Blind默认'},
  ];
  renderProfsT();
}
function addProfT() { T.profs.push({id:'mc',label:'New',num_samples:100,time_limit_ms:8000,inner:'informed',enumerator:'full',sampler:'random'}); renderProfsT(); }
function rmProfT(i) { T.profs.splice(i,1); renderProfsT(); }
function upProfT(i,k,v) { T.profs[i][k]=v; renderProfsT(); }

function renderProfsT() {
  const dOpts = ['mc','ismcts','blind'].map(id=>`<option value="${id}">${(MODEL_INFO[id]||{}).name||id}</option>`).join('');
  document.getElementById('profs-t').innerHTML = T.profs.map((p,i)=>{
    const sel = dOpts.replace(`value="${p.id}"`,`value="${p.id}" selected`);
    const isMC = p.id==='mc'||p.id==='ismcts';
    const preview = _paramPreview(p);
    let xtra = '';
    if (isMC) {
      const innerName = (MODEL_INFO[p.inner||'informed']||{}).name||p.inner||'Informed';
      const enumName = {full:'全面枚举',top_n:'预筛TopN',memory:'记忆感知'}[p.enumerator]||p.enumerator||'full';
      xtra = `<span style="font-size:0.68em;color:#666;margin-left:2px">N=${p.num_samples||128}ms=${p.time_limit_ms||10000}内=${innerName}枚=${enumName}</span>`;
    }
    return `<div class="prow"><div class="pn"><input type="text" value="${p.label||''}" onchange="upProfT(${i},'label',this.value)" placeholder="名称"><select onchange="upProfT(${i},'id',this.value);renderProfsT()">${sel}</select><button class="btn btn-gy btn-sm" style="padding:0 5px;font-size:0.7em" onclick="openModelPanel('T',${i},'${p.id}')" title="算法介绍与参数">ⓘ</button><button onclick="rmProfT(${i})">×</button>${xtra}</div>${preview?`<div class="param-preview">${preview}</div>`:''}</div>`;
  }).join('')+`<button class="btn btn-gy btn-sm" style="margin-top:2px" onclick="addProfT()">+ 模型</button>`;
}
function collectProfsT() {
  return T.profs.map(p=>{const e={};for(const k of Object.keys(p)){if(k!=='label')e[k]=p[k];}e.label=p.label;return e;});
}

// ==================================================================
// Tab 2 — Select scenario + render table
// ==================================================================
function loadScT() { T.cat=document.getElementById('tcat').value; renderScList('sc-list2',T.cat,T.sid,'selT'); }
async function selT(id) {
  T.sid=id; loadScT();
  // Invalidate stale responses + abort in-flight analysis on scene change.
  if (T.running) stopT();
  T.reqSeq = (T.reqSeq||0) + 1;
  const seq = T.reqSeq;
  try {
    const [r1,r2] = await Promise.all([fetch('/api/arena/scenarios/'+id),fetch('/api/arena/scenarios/'+id+'/perspectives')]);
    const d1 = await r1.json(); const d2 = await r2.json();
    if (!r1.ok || d1.error) throw new Error(d1.error || ('HTTP ' + r1.status));
    if (seq !== T.reqSeq) return;
    T.detail=d1; T.persps=d2.perspectives||{};
    T.res=[]; document.getElementById('tres').innerHTML='<span class="emp">点击分析</span>'; document.getElementById('trcnt').textContent='';
    renderTableT(); updPerspInfo();
    const s=T.detail;
    document.getElementById('dbg-sc').innerHTML=`<div><b>${s.name}</b> <span style="color:#888">${s.category}</span></div><div style="color:#aaa;white-space:pre-wrap">${s.description||''}</div>`+(s.reasoning?`<div style="color:#daa520;margin-top:2px">预期: ${s.reasoning}</div>`:'');
    document.getElementById('dbg-ai').innerHTML=''; document.getElementById('dbg-uk').innerHTML='';
  } catch (e) {
    if (seq !== T.reqSeq) return;
    T.detail = null;
    document.getElementById('tres').innerHTML = `<span style="color:#e74c3c;font-size:0.82em">场景加载失败: ${e.message}</span>`;
  }
}

function scenarioTableState(detail) {
  // Synthesize a buildTable-shaped state from a scenario detail (the
  // analyzer's perspective is always seat 0; the table combo, if any, is
  // shown as the latest trick action of table_player).
  const players = [
    {hand: detail.hand || [], hand_size: (detail.hand || []).length, finished: false},
    {hand: (detail.opponents||{})['1'] || [], hand_size: ((detail.opponents||{})['1'] || []).length, finished: false},
    {hand: (detail.opponents||{})['2'] || [], hand_size: ((detail.opponents||{})['2'] || []).length, finished: false},
    {hand: (detail.opponents||{})['3'] || [], hand_size: ((detail.opponents||{})['3'] || []).length, finished: false},
  ];
  const trick_history = [];
  const tp = detail.table_player;
  if (detail.table && detail.table.length && tp != null && tp >= 0) {
    trick_history.push({player: tp, pass: false,
      combo: {type: 'TABLE', type_cn: '桌面', cards: detail.table,
              card_ids: detail.table.map(c => c.id), length: detail.table.length}});
  }
  return {
    players, current_player: 0, trick_history,
    can_pass: false, level: detail.level || 2, trick_number: 1,
  };
}

function renderTableT() {
  if (!T.detail) return;
  buildTable('tbl-t', scenarioTableState(T.detail), {mode: 'scenario'});
  // Unknown cards
  if (T.debug||T.persp>0) {
    const s=T.detail; if(!s) return;
    const seen=new Set();(s.hand||[]).forEach(c=>seen.add(c.id));
    const opp=s.opponents||{}; for(let p=1;p<=3;p++) (opp[String(p)]||[]).forEach(c=>seen.add(c.id));
    (s.played_cards||[]).forEach(c=>seen.add(c.id));(s.table||[]).forEach(c=>seen.add(c.id));
    const uk=[]; for(let i=0;i<108;i++) if(!seen.has(i)) uk.push(i);
    document.getElementById('dbg-uk').innerHTML=`<div style="color:#888">未知牌: ${uk.length}张 (总额108)</div><div style="font-size:0.7em;color:#666;margin-top:1px">ID: ${uk.slice(0,20).join(',')}${uk.length>20?'...':''}</div>`;
  }
}

// ==================================================================
// Tab 2 — Perspective, Debug, Analysis
// ==================================================================
function swPersp(pid) { T.persp=pid; document.querySelectorAll('#persp-tabs .ptab').forEach((b,i)=>b.classList.toggle('on',i===pid)); updPerspInfo(); renderTableT(); }
function updPerspInfo() { const pv=T.persps[String(T.persp)]; if(pv) document.getElementById('persp-info').textContent=`${pv.player_name||PN[T.persp]} · 手牌${pv.my_hand_size||0}张 · ${pv.table||'桌面空'}`; }
function toggleDebugT() { T.debug=!T.debug; const b=document.getElementById('btn-dbg2'); b.style.background=T.debug?'#27ae60':'#555'; b.style.color='#fff'; renderTableT(); }

async function runT() {
  if (!T.sid) return;
  if (T.running) { stopT(); return; }
  T.running=true; T.res=[]; T.abort=new AbortController();
  T.runSeq = (T.runSeq||0) + 1;
  const seq = T.runSeq;
  document.getElementById('tres').innerHTML=''; const btn=document.getElementById('btn-at');
  btn.textContent='停止'; btn.className='btn btn-red btn-sm';
  const models = collectProfsT();
  document.getElementById('trcnt').textContent=`(0/${models.length})`;
  for (const m of models) {   // serial: per-request game_logger.clear() clobbered parallel debug logs
    try {
      const r = await fetch('/api/arena/analyze/scenario',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario_id:T.sid,models:[m],perspective:T.persp,debug:T.debug}),signal:T.abort.signal});
      if (seq !== T.runSeq) return;
      if (!r.ok) T.res.push({model_id:m.id,model_name:m.label||m.id,error:'HTTP '+r.status});
      else { const d=await r.json(); if(d.results)T.res.push(...d.results); if(T.debug) renderDbgT(d.results); }
    } catch(e) {
      if (e.name==='AbortError') break;
      if (seq !== T.runSeq) return;
      T.res.push({model_id:m.id,model_name:m.label||m.id,error:e.message});
    }
    if (seq !== T.runSeq) return;
    renderResT(); document.getElementById('trcnt').textContent=`(${T.res.length}/${models.length})`;
  }
  if (seq !== T.runSeq) return;
  finishT();
}
function stopT() { if(T.abort){T.abort.abort();T.abort=null;} finishT(); }
function finishT() { T.running=false; const btn=document.getElementById('btn-at'); btn.textContent='分析'; btn.className='btn btn-gn btn-sm'; }

function renderResT() {
  const el=document.getElementById('tres'); if(!T.res.length){el.innerHTML='<span class="emp">无结果</span>';return;}
  // Remember which model detail sections are open
  const openModels = new Set();
  el.querySelectorAll('.mres .mcand[open]').forEach(d => {
    const mn = d.closest('.mres')?.querySelector('.mn')?.textContent;
    if (mn) openModels.add(mn);
  });
  el.innerHTML = T.res.map(r=>{
    if(r.error) return `<div class="mres"><div class="mn">${r.model_name||r.model_id}</div><div class="me">${r.error}</div></div>`;
    const m=r.metrics||{},ch=r.choice; const wr=ch&&ch.win_rate!=null?(ch.win_rate*100).toFixed(1)+'%':'';
    let chH = r.pass_chosen?'<span style="color:#e67e22;font-weight:bold">过牌</span>':(ch?`<span class="combo-tag" style="background:#daa520;color:#222;padding:1px 4px;border-radius:3px;font-size:0.7em">${ch.combo_type||'?'}</span> ${(ch.cards||[]).join(' ')}`:'?');
    const cds=r.candidates||[];
    const mn = r.model_name||r.model_id;
    const openAttr = openModels.has(mn) ? ' open' : '';
    let cdHtml = cds.length?`<details class="mcand"${openAttr}><summary>候选 (${cds.length})</summary>`+cds.map((c,i)=>{const best=i===0;const cwr=c.win_rate!=null?Math.round(c.win_rate*100):0;const detHtml=_renderDetail(c.detail,r.model_id);return `<div class="crow${best?' best':''}"><div class="cr-main">${c.combo_type==='PASS'?'<span style="color:#e67e22;font-weight:bold">过牌</span>':`<span class="combo-tag" style="background:#daa520;color:#222;padding:1px 2px;border-radius:2px;font-size:0.62em">${c.combo_type||'?'}</span> ${(c.cards||[]).join(' ')}`}<span class="cwr">${cwr?`<span class="wbar" style="width:${Math.max(cwr,1)}px"></span><span class="wtxt">${cwr}%</span>`:''}</span></div>${detHtml?`<div class="cr-detail">${detHtml}</div>`:''}</div>`}).join('')+'</details>':'';
    return `<div class="mres"><div class="mh"><span class="mn">${mn}</span><span class="mt">${m.elapsed_ms!=null?m.elapsed_ms.toFixed(0)+'ms':''}${m.timed_out?' ⚠':''}</span></div><div class="mc">${chH} ${wr?`<span class="mw">${wr}</span>`:''}</div>${cdHtml}</div>`;
  }).join('');
}

function renderDbgT(results) {
  const wd = results.find(r=>r.debug&&r.debug.ai_log);
  if (wd) {
    const entries = wd.debug.ai_log||[]; let html='';
    for (const e of entries) {
      const pn=PN[e.player]||('P'+e.player);
      if (e.type==='decision_start') html+=`<div style="margin-bottom:2px"><b style="color:#daa520">${pn}</b> ${e.data.agent||'?'} · ${e.data.hand_size||'?'}张</div>`;
      else if (e.type==='decision_end') html+=`<div style="font-size:0.76em;color:#aaa;margin-bottom:2px">选:<b style="color:#27ae60">${e.data.choice||'?'}</b>${e.data.choice_win_rate!=null?` · <b style="color:#f1c40f">${(e.data.choice_win_rate*100).toFixed(1)}%</b>`:''} · ${e.data.elapsed_ms||0}ms${e.data.timed_out?' ⚠':''}</div>`;
    }
    document.getElementById('dbg-ai').innerHTML = html||'<span style="color:#666">无日志</span>';
  }
}

// ==================================================================
// Tab 2 — Benchmark
// ==================================================================
async function benchT() {
  const cat=document.getElementById('tcat').value||''; const models=collectProfsT();
  document.getElementById('bsect').style.display='block'; document.getElementById('bsum').innerHTML='<span style="color:#888;font-size:0.76em">运行中...</span>';
  const btn=document.getElementById('btn-bt');
  btn.textContent='...'; btn.disabled=true;
  try {
    const r=await fetch('/api/arena/benchmark',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({category:cat,models})});
    const d=await r.json().catch(()=>({}));
    if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
    const sum=d.summary||[];
    document.getElementById('bsum').innerHTML=sum.length?`<h4 style="font-size:0.76em;color:#daa520;margin:3px 0">排行榜</h4><table style="width:100%;font-size:0.74em;border-collapse:collapse"><tr style="color:#888"><th>模型</th><th>准确率</th><th>正确/总数</th><th>平均</th></tr>${sum.map(s=>`<tr style="border-bottom:1px solid rgba(255,255,255,0.02)"><td>${s.model_name}</td><td style="color:${s.accuracy>=80?'#27ae60':'#e67e22'}">${s.accuracy}%</td><td style="color:#aaa">${s.correct}/${s.total}</td><td style="color:#888">${s.avg_ms}ms</td></tr>`).join('')}</table>`:'<span style="color:#888">无结果</span>';
    const det=d.results||[];
    document.getElementById('bdet').innerHTML=det.length?`<h4 style="font-size:0.76em;color:#daa520;margin:3px 0">详情</h4><table style="width:100%;font-size:0.68em;border-collapse:collapse"><tr style="color:#888"><th>场景</th><th>模型</th><th>选择</th></tr>${det.map(r=>`<tr style="border-bottom:1px solid rgba(255,255,255,0.02)"><td style="color:#aaa">${r.scenario_name||''}</td><td>${r.model_name||''}</td><td style="color:${r.correct?'#27ae60':'#e74c3c'}">${r.choice_type||'?'} ${(r.chosen_cards||[]).join(' ')}${r.pass_chosen?'过':''} ${r.correct?'✓':'✗'}</td></tr>`).join('')}</table>`:'';
  } catch (e) {
    document.getElementById('bsum').innerHTML=`<span style="color:#e74c3c;font-size:0.76em">基准失败: ${e.message}</span>`;
  } finally {
    // try/finally: a single 500 used to leave the button disabled forever
    btn.textContent='基准'; btn.disabled=false;
  }
}
// ==================================================================
// Custom scenario state
// ==================================================================
let C = {
  hands: {0:[], 1:[], 2:[], 3:[]},
  zone_play: {0:{action:'turn',cards:[]}, 1:{action:'pass',cards:[]}, 2:{action:'pass',cards:[]}, 3:{action:'pass',cards:[]}},
  level: 2,
  focus_player: 0,
  focus_play: null,
  simId: null,
  simState: null,
  profs: [],
  res: [],
  selected: new Set(),
  running: false,
  abort: null,
  _suppressRender: false,
  sim_mode: false,
  _history: [],
  _maxHistory: 50,
};

// Undo helpers
function pushUndoState() {
  if (C.sim_mode) return;
  C._history.push({
    hands: {0:[...C.hands[0]], 1:[...C.hands[1]], 2:[...C.hands[2]], 3:[...C.hands[3]]},
    zone_play: {
      0:{action:C.zone_play[0].action, cards:[...C.zone_play[0].cards]},
      1:{action:C.zone_play[1].action, cards:[...C.zone_play[1].cards]},
      2:{action:C.zone_play[2].action, cards:[...C.zone_play[2].cards]},
      3:{action:C.zone_play[3].action, cards:[...C.zone_play[3].cards]},
    },
    level: C.level,
    focus_player: C.focus_player,
    focus_play: C.focus_play,
  });
  if (C._history.length > C._maxHistory) C._history.shift();
}
function undoAction() {
  if (C.sim_mode) return;   // sim mode has its own state — undo used to rewrite builder hands underneath a live sim view
  if (C._history.length === 0) return;
  const s = C._history.pop();
  C.hands = s.hands;
  C.zone_play = s.zone_play;
  C.level = s.level;
  C.focus_player = s.focus_player;
  C.focus_play = s.focus_play;
  renderCustomAll();
}
function setLevel(val) {
  const clamped = Math.max(2, Math.min(14, val));
  if (clamped === C.level) return;   // no-op at the boundary: no empty undo entry
  pushUndoState();
  C.level = clamped;
  renderCustomAll();
}

// Ctrl+Z undo
document.addEventListener('keydown', e => {
  if (e.ctrlKey && e.key === 'z' && !e.target.closest('input,select,textarea')) {
    e.preventDefault(); undoAction();
  }
});

// Helpers
C.getCurrentPlayer = () => { for (let p=0;p<=3;p++) if (C.zone_play[p].action==='turn') return p; return 0; };
C.getTableCards = () => {
  // Find last 'play' action BEFORE 'turn' in counterclockwise order
  const turnP = C.getCurrentPlayer();
  for (let d=1; d<=3; d++) {
    const p = (turnP - d + 4) % 4;
    if (C.zone_play[p].action === 'play') return C.zone_play[p].cards;
    if (C.zone_play[p].action === 'turn') break;
  }
  return [];
};
C.getTablePlayer = () => {
  const turnP = C.getCurrentPlayer();
  for (let d=1; d<=3; d++) {
    const p = (turnP - d + 4) % 4;
    if (C.zone_play[p].action === 'play') return p;
    if (C.zone_play[p].action === 'turn') break;
  }
  return -1;
};
C.allAssignedCards = () => {
  const s = new Set();
  for (let p=0;p<=3;p++) for (const cid of C.hands[p]) s.add(cid);
  for (let p=0;p<=3;p++) for (const cid of (C.zone_play[p].cards||[])) s.add(cid);
  return s;
};
C.buildTrickActions = () => {
  // Build trick_actions array from zone_play for backend
  const turnP = C.getCurrentPlayer();
  const actions = [];
  // Walk backward from turnP to find leader
  let leader = turnP;
  for (let d=1; d<=3; d++) {
    const p = (turnP - d + 4) % 4;
    if (C.zone_play[p].action === 'turn') break;
    leader = p;
  }
  // Walk forward from leader to turnP
  for (let d=0; d<4; d++) {
    const p = (leader + d) % 4;
    actions.push({player:p, action:C.zone_play[p].action, cards:[...C.zone_play[p].cards]});
    if (p === turnP) break;
  }
  return actions;
};

// Pre-build card lookup: suit -> rank -> [card objects]
let allCardsBySR = {};
let allCardsById = {};

function buildCardLookup() {
  allCardsBySR = {};
  allCardsById = {};
  for (let i = 0; i < 108; i++) {
    const deckId = Math.floor(i / 54);
    const idx = i % 54;
    let suit, rankVal, rankName, suitName, isJoker;
    if (idx === 52) { suit = 4; rankVal = 15; rankName = 'SJ'; suitName = ''; isJoker = true; }
    else if (idx === 53) { suit = 4; rankVal = 16; rankName = 'BJ'; suitName = ''; isJoker = true; }
    else { suit = Math.floor(idx / 13); rankVal = (idx % 13) + 2; rankName = rankVal <= 10 ? String(rankVal) : {11:'J',12:'Q',13:'K',14:'A'}[rankVal]; suitName = {0:'C',1:'D',2:'H',3:'S'}[suit]; isJoker = false; }
    const card = {
      id: i, rank: rankVal, rank_name: rankName,
      suit: suit, suit_name: suitName,
      display: rankName + (suitName || ''),
      is_joker: isJoker, is_wild: false, deck: deckId,
    };
    if (!allCardsBySR[suit]) allCardsBySR[suit] = {};
    if (!allCardsBySR[suit][rankVal]) allCardsBySR[suit][rankVal] = [];
    allCardsBySR[suit][rankVal].push(card);
    allCardsById[i] = card;
  }
}
buildCardLookup();

function acCard(c) {
  const SC = {0:'♣',1:'♦',2:'♥',3:'♠',4:''};
  const sc = c.is_joker ? (c.rank_name==='SJ'?'joker-black':'joker-red') : 'suit-'+(c.suit_name || '');
  return `<div class="card ${sc}"><span class="card-rank">${c.rank_name}</span><span class="card-suit-big">${SC[c.suit]||''}</span></div>`;
}

// ==================================================================
// Card pool rendering
// ==================================================================
function renderCardPool() {
  const container = document.getElementById('card-pool');
  const suits = [
    {id: 3, name: '♠'},
    {id: 2, name: '♥'},
    {id: 1, name: '♦'},
    {id: 0, name: '♣'},
  ];
  const ranks = [14,13,12,11,10,9,8,7,6,5,4,3,2];
  const assigned = C.allAssignedCards();
  const tableCards = new Set(C.getTableCards());

  let html = '';
  for (const suit of suits) {
    html += `<div class="pool-suit-hdr">${suit.name}</div>`;
    for (const rank of ranks) {
      const cards = allCardsBySR[suit.id] && allCardsBySR[suit.id][rank] || [];
      for (const c of cards) {
        const isAssigned = assigned.has(c.id);
        const isTable = tableCards.has(c.id);
        const cls = 'pool-card' + (isAssigned ? (isTable ? ' table-card' : ' assigned') : '');
        const attrs = isAssigned ? '' : `data-cid="${c.id}" onclick="poolClick(${c.id},event)"`;
        html += `<span class="${cls}" ${attrs}>${acCard(c)}</span>`;
      }
    }
  }
  // Jokers
  html += '<div class="pool-suit-hdr">🃏</div>';
  for (const rank of [16, 15]) {
    const cards = allCardsBySR[4] && allCardsBySR[4][rank] || [];
    for (const c of cards) {
      const isAssigned = assigned.has(c.id);
      const isTable = tableCards.has(c.id);
      const cls = 'pool-card' + (isAssigned ? (isTable ? ' table-card' : ' assigned') : '');
      const attrs = isAssigned ? '' : `data-cid="${c.id}" onclick="poolClick(${c.id},event)"`;
      html += `<span class="${cls}" ${attrs}>${acCard(c)}</span>`;
    }
  }
  container.innerHTML = html;

  // Update stats
  let totalAssigned = assigned.size;
  let parts = [];
  for (let p = 0; p <= 3; p++) {
    parts.push('P'+p+'='+C.hands[p].length);
  }
  document.getElementById('pool-stats').textContent =
    '已分配 ' + parts.join(' ') + ' | ' + (108 - totalAssigned) + '/108';
}

// ==================================================================
// Assignment logic
// ==================================================================
function poolClick(cid, event) {
  if (C.allAssignedCards().has(cid)) return;
  if (C.focus_play === null && C.focus_player < 0) {
    // No target selected — the old code pushed an undo snapshot and did
    // nothing visible, so Ctrl+Z felt broken.
    alert('请先点击一个玩家区或出牌区，再从牌池选牌');
    return;
  }
  pushUndoState();

  if (C.focus_play !== null) {
    C.zone_play[C.focus_play].action = 'play';
    C.zone_play[C.focus_play].cards.push(cid);
  } else if (C.focus_player >= 0) {
    C.hands[C.focus_player].push(cid);
  }
  renderCustomAll();
}

function customFocus(pid, event) {
  event.stopPropagation();
  autoClearEmptyPlay();
  validatePlayCombos();
  C.focus_player = pid;
  C.focus_play = null;
  document.querySelectorAll('#custom-builder .player-zone').forEach(el => el.classList.remove('focused'));
  document.querySelectorAll('.cz-play-area').forEach(el => el.classList.remove('focused'));
  const zone = document.getElementById('cz-' + pid);
  if (zone) zone.classList.add('focused');
}

function customFocusPlay(pid, event) {
  event.stopPropagation();
  autoClearEmptyPlay();
  validatePlayCombos();
  C.focus_play = pid;
  C.focus_player = -1;
  if (C.zone_play[pid].action !== 'play') czSetPlay(pid, 'play');
  document.querySelectorAll('#custom-builder .player-zone').forEach(el => el.classList.remove('focused'));
  document.querySelectorAll('.cz-play-area').forEach(el => el.classList.remove('focused'));
  const pa = document.getElementById('czpa-' + pid);
  if (pa) pa.classList.add('focused');
}

function removeFromPlayer(pid, cid, event) {
  event.stopPropagation();
  pushUndoState();
  const idx = C.hands[pid].indexOf(cid);
  if (idx >= 0) C.hands[pid].splice(idx, 1);
  // Also remove from any zone's play cards
  for (let p = 0; p <= 3; p++) {
    const ai = (C.zone_play[p].cards||[]).indexOf(cid);
    if (ai >= 0) C.zone_play[p].cards.splice(ai, 1);
  }
  renderCustomAll();
}

function czSetPlay(pid, mode) {
  pushUndoState();
  // Clicking the active button toggles it off -> pass
  if (C.zone_play[pid].action === mode) mode = 'pass';
  C.zone_play[pid].action = mode;
  if (mode === 'turn') {
    // Only one 'turn' — clear all others
    for (let p = 0; p <= 3; p++) {
      if (p !== pid) C.zone_play[p].action = (C.zone_play[p].action === 'turn' ? 'pass' : C.zone_play[p].action);
    }
  }
  if (mode === 'pass' || mode === 'turn') C.zone_play[pid].cards = [];
  if (mode !== 'play' && C.focus_play === pid) C.focus_play = null;
  if (mode === 'play' && C.focus_play === null) C.focus_play = pid;
  renderCustomAll();
}

function czRemovePlayCard(pid, cid, event) {
  event.stopPropagation();
  pushUndoState();
  const cards = C.zone_play[pid].cards;
  const idx = cards.indexOf(cid);
  if (idx >= 0) cards.splice(idx, 1);
  renderCustomAll();
}

function czValidate() {
  // Find the 'turn' player
  let turnP = -1;
  for (let p = 0; p <= 3; p++) {
    if (C.zone_play[p].action === 'turn') {
      if (turnP >= 0) return {valid:false, reason:'多个玩家标记为轮到'};
      turnP = p;
    }
  }
  if (turnP < 0) return {valid:false, reason:'没有玩家标记为轮到'};

  // Walk backward from turnP to find non-turn actions
  let hasActions = false;
  for (let d = 1; d <= 3; d++) {
    const p = (turnP - d + 4) % 4;
    const a = C.zone_play[p].action;
    if (a === 'turn') return {valid:false, reason:'动作序列不连续'};
    if (a === 'play' || a === 'pass') hasActions = true;
  }
  // If no non-turn actions, it's a new trick — valid
  return {valid:true, reason: hasActions ? '' : '新一轮'};
}

function validatePlayCombos() {
  for (let p = 0; p <= 3; p++) {
    const zp = C.zone_play[p];
    if (zp.action !== 'play' || !zp.cards || zp.cards.length === 0) continue;
    fetch('/api/arena/check_combo', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({card_ids: [...zp.cards], level: C.level}),
    }).then(r => r.json()).then(data => {
      if (!data.valid) {
        const pa = document.getElementById('czpa-' + p);
        if (pa) {
          pa.classList.add('combo-error');
          setTimeout(() => pa.classList.remove('combo-error'), 1500);
        }
      }
    }).catch(() => {});   // best-effort visual hint — no unhandled rejection
  }
}

function autoClearEmptyPlay() {
  for (let p = 0; p <= 3; p++) {
    if (C.zone_play[p].action === 'play' && (!C.zone_play[p].cards || C.zone_play[p].cards.length === 0)) {
      C.zone_play[p].action = 'pass';
    }
  }
}

function customClearFocus(event) {
  autoClearEmptyPlay();
  validatePlayCombos();
  C.focus_player = -1;
  C.focus_play = null;
  document.querySelectorAll('#custom-builder .player-zone').forEach(el => el.classList.remove('focused'));
  document.querySelectorAll('.cz-play-area').forEach(el => el.classList.remove('focused'));
  renderCustomAll();
}

function customClearTrick() {
  pushUndoState();
  for (let p = 0; p <= 3; p++) {
    C.zone_play[p] = {action: (p === C.getCurrentPlayer() ? 'turn' : 'pass'), cards: []};
  }
  C.focus_play = null;
  renderCustomAll();
}

// ==================================================================
// Render helpers
// ==================================================================
function renderPlayAreas() {
  if (C.sim_mode) return;
  // Ensure toggle buttons are visible (undo sim mode hide)
  for (let p = 0; p <= 3; p++) {
    for (const mode of ['play','pass','turn']) {
      const btn = document.getElementById('czpa-' + mode + '-' + p);
      if (btn) btn.style.display = '';
    }
  }
  for (let p = 0; p <= 3; p++) {
    const zp = C.zone_play[p] || {action:'pass', cards:[]};
    // Toggle buttons
    for (const mode of ['play','pass','turn']) {
      const btn = document.getElementById('czpa-' + mode + '-' + p);
      if (btn) btn.classList.toggle('active', zp.action === mode);
    }
    // Play cards
    let pcHtml = '';
    if (zp.cards && zp.cards.length > 0) {
      const sorted = [...zp.cards].sort((a,b)=>{
        const ca=allCardsById[a],cb=allCardsById[b];
        if(!ca||!cb)return 0;
        if(ca.rank!==cb.rank)return cb.rank-ca.rank;
        return ca.suit-cb.suit;
      });
      for (const cid of sorted) {
        const c = allCardsById[cid];
        if (c) pcHtml += `<span onclick="czRemovePlayCard(${p},${cid},event)" title="点击移除">${acCard(c)}</span>`;
      }
    }
    document.getElementById('czpcard-' + p).innerHTML = pcHtml;
  }
}

function renderCustomAll() {
  renderCardPool();
  renderCustomZones();
  renderPlayAreas();
  renderCustomConfig();
}

function renderCustomZones() {
  if (C.sim_mode) return;
  for (let p = 0; p <= 3; p++) {
    const hand = C.hands[p] || [];
    document.getElementById('czcnt-' + p).textContent = hand.length + '张';
    const sorted = [...hand].sort((a, b) => {
      const ca = allCardsById[a], cb = allCardsById[b];
      if (!ca || !cb) return 0;
      if (ca.rank !== cb.rank) return cb.rank - ca.rank;
      return ca.suit - cb.suit;
    });
    let html = '';
    for (const cid of sorted) {
      const c = allCardsById[cid];
      if (c) {
        html += `<span onclick="removeFromPlayer(${p},${cid},event)" title="点击移除" style="display:inline-block">${acCard(c)}</span>`;
      }
    }
    document.getElementById('czhand-' + p).innerHTML = html;
  }
}

function renderCustomConfig() {
  // In sim mode the builder config panel no longer exists (renderSimState
  // replaced the center panel) — touching its nodes used to TypeError.
  if (C.sim_mode) return;
  document.getElementById('custom-level').textContent = C.level;
  // Validation indicator
  const v = czValidate();
  const vel = document.getElementById('cz-valid');
  if (vel) {
    vel.innerHTML = v.valid
      ? '<span style="color:#27ae60">✔ ' + (v.reason || '合法') + '</span>'
      : '<span style="color:#e74c3c">✘ ' + v.reason + '</span>';
  }
}

// ==================================================================
// Quick actions
// ==================================================================
function customRandomDeal() {
  pushUndoState();
  customClear(true);   // internal push skipped — one shuffle = one undo step
  const allIds = [];
  for (let i = 0; i < 108; i++) allIds.push(i);
  for (let i = allIds.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [allIds[i], allIds[j]] = [allIds[j], allIds[i]];
  }
  for (let p = 0; p <= 3; p++) {
    C.hands[p] = allIds.slice(p * 27, (p + 1) * 27);
  }
  renderCustomAll();
}

function customDealRemaining() {
  pushUndoState();
  const assigned = C.allAssignedCards();
  const remaining = [];
  for (let i = 0; i < 108; i++) {
    if (!assigned.has(i)) remaining.push(i);
  }
  for (let i = remaining.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [remaining[i], remaining[j]] = [remaining[j], remaining[i]];
  }
  const deficits = [];
  for (let p = 0; p <= 3; p++) {
    deficits.push(Math.max(0, 27 - C.hands[p].length));
  }
  let idx = 0;
  for (let p = 0; p <= 3; p++) {
    const take = Math.min(deficits[p], remaining.length - idx);
    if (take > 0) {
      C.hands[p].push(...remaining.slice(idx, idx + take));
      idx += take;
    }
  }
  renderCustomAll();
}

function customClear(skipUndo) {
  if (!skipUndo) pushUndoState();
  C.hands = {0:[], 1:[], 2:[], 3:[]};
  for (let p = 0; p <= 3; p++) C.zone_play[p] = {action:(p===0?'turn':'pass'), cards:[]};
  C.focus_play = null;
  renderCustomAll();
}

// ==================================================================
// Profiles
// ==================================================================
function setProfsC() {
  C.profs = [
    {id:'informed',label:'Informed'},
    {id:'round',label:'Round'},
    {id:'exact',label:'Exact',time_limit_ms:20000},
    {id:'blind',label:'Blind'},
  ];
  renderProfsC();
}
function addProfC() { C.profs.push({id:'informed',label:'New'}); renderProfsC(); }
function rmProfC(i) { C.profs.splice(i,1); renderProfsC(); }
function upProfC(i,k,v) { C.profs[i][k]=v; renderProfsC(); }

function renderProfsC() {
  const ids = ['informed','round','exact','blind'];
  const opts = ids.map(id=>`<option value="${id}">${(MODEL_INFO[id]||{}).name||id}</option>`).join('');
  document.getElementById('profs-c').innerHTML = C.profs.map((p,i)=>{
    const sel = opts.replace(`value="${p.id}"`,`value="${p.id}" selected`);
    const preview = _paramPreview(p);
    return `<div class="prow"><div class="pn"><input type="text" value="${p.label||''}" onchange="upProfC(${i},'label',this.value)" placeholder="名称"><select onchange="upProfC(${i},'id',this.value);renderProfsC()">${sel}</select><button class="btn btn-gy btn-sm" style="padding:0 5px;font-size:0.7em" onclick="openModelPanel('C',${i},'${p.id}')" title="算法介绍与参数">ⓘ</button><button onclick="rmProfC(${i})">×</button></div>${preview?`<div class="param-preview">${preview}</div>`:''}</div>`;
  }).join('')+`<button class="btn btn-gy btn-sm" style="margin-top:2px" onclick="addProfC()">+ 模型</button>`;
}

// ==================================================================
// Model panel overrides for C tab
// ==================================================================
const _origOpenModelPanel = openModelPanel;
openModelPanel = function(tab, idx, modelId) {
  if (tab === 'C') {
    const info = MODEL_INFO[modelId];
    if (!info) return;
    _panelCtx = {tab: 'C', idx, modelId};
    const prof = C.profs[idx] || {};
    let html = `<h2>${info.name}<button onclick="closeModelPanel()">✕</button></h2>`;
    html += `<div style="color:#888;font-size:0.78em;margin-bottom:6px">${info.desc}</div>`;
    html += `<h3>算法流程</h3><div class="alg-flow">${info.flow}</div>`;
    html += `<h3>可调参数</h3><div class="param-grid">`;
    html += _renderParamGrid(info.params, prof, '');
    html += `</div>`;
    if (modelId === 'mc' || modelId === 'ismcts') {
      const innerId = prof.inner || 'informed';
      const innerInfo = MODEL_INFO[innerId];
      if (innerInfo) {
        html += `<h3>内层模型: ${innerInfo.name} 参数</h3><div class="param-grid">`;
        html += _renderParamGrid(innerInfo.params, prof, 'inner');
        html += `</div>`;
      }
      const enumId = prof.enumerator || 'full';
      const enumInfo = MODEL_INFO[enumId];
      if (enumInfo && enumInfo.params && enumInfo.params.length) {
        html += `<h3>枚举器: ${enumInfo.name} 参数</h3><div class="param-grid">`;
        html += _renderParamGrid(enumInfo.params, prof, 'enumerator');
        html += `</div>`;
      }
    }
    html += `<div class="panel-actions">
      <button class="btn btn-gy btn-sm" onclick="resetPanelParams()">重置默认</button>
      <button class="btn btn-gn btn-sm" onclick="applyPanelParams()">应用</button>
      <button class="btn btn-gy btn-sm" onclick="closeModelPanel()">关闭</button>
    </div>`;
    document.getElementById('mdl-panel').innerHTML = html;
    document.getElementById('mdl-overlay').classList.add('on');
  } else {
    _origOpenModelPanel(tab, idx, modelId);
  }
};

const _origApplyPanelParams = applyPanelParams;
applyPanelParams = function() {
  if (!_panelCtx) return;
  if (_panelCtx.tab === 'C') { renderProfsC(); closeModelPanel(); }
  else _origApplyPanelParams();
};

const _origUpdatePanelParam = updatePanelParam;
updatePanelParam = function(key, val) {
  if (!_panelCtx) return;
  if (_panelCtx.tab === 'C') {
    const prof = C.profs[_panelCtx.idx];
    if (!prof) return;
    const parts = key.split('.');
    let target = prof;
    for (let i = 0; i < parts.length - 1; i++) {
      if (target[parts[i]] === undefined) target[parts[i]] = {};
      target = target[parts[i]];
    }
    target[parts[parts.length - 1]] = val;
  } else _origUpdatePanelParam(key, val);
};

const _origStepPanelParam = stepPanelParam;
stepPanelParam = function(key, delta) {
  if (!_panelCtx) return;
  if (_panelCtx.tab === 'C') {
    const prof = C.profs[_panelCtx.idx];
    if (!prof) return;
    const parts = key.split('.');
    let target = prof;
    for (let i = 0; i < parts.length - 1; i++) {
      if (target[parts[i]] === undefined) target[parts[i]] = {};
      target = target[parts[i]];
    }
    const lastKey = parts[parts.length - 1];
    let dflt = 0;
    if (parts.length === 1) {
      dflt = MODEL_INFO[_panelCtx.modelId]?.params.find(p=>p.key===key)?.dflt || 0;
    } else {
      const subInfo = MODEL_INFO[parts[0]];
      if (subInfo) dflt = subInfo.params.find(p=>p.key===lastKey)?.dflt || 0;
    }
    const cur = target[lastKey] !== undefined ? target[lastKey] : dflt;
    target[lastKey] = Math.round((cur + delta) * 100) / 100;
    const inpId = 'pp-' + key.replace(/\./g, '-');
    const inp = document.getElementById(inpId);
    if (inp) inp.value = target[lastKey];
  } else _origStepPanelParam(key, delta);
};

const _origResetPanelParams = resetPanelParams;
resetPanelParams = function() {
  if (!_panelCtx) return;
  if (_panelCtx.tab === 'C') {
    const info = MODEL_INFO[_panelCtx.modelId];
    if (!info) return;
    const prof = C.profs[_panelCtx.idx];
    if (!prof) return;
    _resetParamsForInfo(info, prof, '');
    const modelId = _panelCtx.modelId;
    if (modelId === 'mc' || modelId === 'ismcts') {
      const innerInfo = MODEL_INFO[prof.inner || 'informed'];
      _resetParamsForInfo(innerInfo, prof, 'inner');
      const enumInfo = MODEL_INFO[prof.enumerator || 'full'];
      _resetParamsForInfo(enumInfo, prof, 'enumerator');
    }
  } else _origResetPanelParams();
};

// ==================================================================
// Load state from any simulator into the custom builder
// ==================================================================
function customLoadFromSim(simState) {
  // Kill any in-flight analysis first: its late finish used to clobber the
  // builder button labels and write results into the next sim's panel.
  if (C.running) stopC();
  // Extract hands from sim state
  C.hands = {0:[], 1:[], 2:[], 3:[]};
  if (simState.players) {
    for (let p = 0; p <= 3; p++) {
      const hand = simState.players[p]?.hand || [];
      C.hands[p] = hand.map(c => c.id);
    }
  }

  // Build zone_play from trick_history + current state
  for (let p = 0; p <= 3; p++) C.zone_play[p] = {action:'pass', cards:[]};
  const th = simState.trick_history || [];
  for (const entry of th) {
    if (entry.pass) {
      C.zone_play[entry.player] = {action:'pass', cards:[]};
    } else if (entry.combo) {
      C.zone_play[entry.player] = {action:'play', cards: entry.combo.cards.map(c => c.id)};
    }
  }
  const cp = simState.current_player || 0;
  C.zone_play[cp] = {action:'turn', cards:[]};

  C.level = simState.level || 2;
  C.sim_mode = false;
  C.focus_play = null;
  C.focus_player = 0;

  // Restore builder center panel
  const ctr = document.querySelector('#custom-builder .tbl-ctr > div');
  if (ctr && C._builder_panel) ctr.innerHTML = C._builder_panel;
  // Restore the start button label (finishC used to leave it as 分析)
  const btnAc = document.getElementById('btn-ac');
  if (btnAc) { btnAc.textContent = '开始推演'; btnAc.className = 'btn btn-gn'; btnAc.disabled = false; }

  switchTab('custom');
  renderCustomAll();
}

// ==================================================================
// Render simulator state into builder layout
// ==================================================================
function renderPlayState(pid, lastAction, currentPlayer, pcardId) {
  const pc = document.getElementById(pcardId);
  if (!pc) return;
  if (!lastAction) {
    pc.innerHTML = currentPlayer === pid
      ? '<span style="color:#27ae60;font-size:0.72em">轮到</span>' : '';
  } else if (lastAction.pass) {
    pc.innerHTML = '<span style="color:#e67e22;font-size:0.72em">过</span>';
  } else if (lastAction.combo) {
    pc.innerHTML = '<span style="color:#daa520;font-size:0.65em">'
      + (lastAction.combo.type_cn||lastAction.combo.type) + '</span> '
      + (lastAction.combo.cards||[]).map(ac).join('');
  }
}

function renderSimState(state) {
  const names = ['你','右家','对家','左家'];
  const pls = state.players || [];
  const th = state.trick_history || [];
  const cp = state.current_player;

  // Build last action per player from trick_history
  const lastAction = {};
  for (const e of th) { lastAction[e.player] = e; }

  // Update hand zones
  for (let p = 0; p <= 3; p++) {
    const pi = pls[p] || {};
    const cards = pi.hand || [];
    document.getElementById('czcnt-' + p).textContent = (pi.hand_size || cards.length) + '张';

    // Render hand cards
    const container = document.getElementById('czhand-' + p);
    if (cards.length) {
      const sorted = [...cards].sort((a,b)=>b.rank-a.rank||(a.suit||0)-(b.suit||0));
      let html = '';
      for (const c of sorted) {
        const sel = C.selected.has(c.id) ? ' selected' : '';
        const playable = cp===p ? ' clickable' : '';
        const click = cp===p ? ` data-cid="${c.id}" onclick="toggleArenaCardC(${c.id})"` : '';
        html += ac(c, sel+playable, click);
      }
      container.innerHTML = html;
    } else {
      container.innerHTML = '';
    }
  }

  // Update play areas with game state
  for (let p = 0; p <= 3; p++) {
    const pa = document.getElementById('czpa-' + p);
    if (!pa) continue;

    // Hide builder buttons in sim mode
    for (const mode of ['play','pass','turn']) {
      const btn = document.getElementById('czpa-' + mode + '-' + p);
      if (btn) btn.style.display = 'none';
    }
    pa.querySelectorAll('.czpa-btn').forEach(b => b.style.display = 'none');

    renderPlayState(p, lastAction[p], cp, 'czpcard-' + p);
  }

  // Update center panel for sim mode
  // Save builder panel HTML first time only
  if (C._builder_panel === undefined) C._builder_panel = document.querySelector('#custom-builder .tbl-ctr > div')?.innerHTML;
  const ctr = document.querySelector('#custom-builder .tbl-ctr > div');
  if (ctr) {
    ctr.innerHTML = `<div class="tinfo">级牌 <strong>Lv${state.level||2}</strong> · 回合${state.trick_number||1}</div>
      <div style="font-size:0.75em;color:#888;margin-top:2px">轮到: <b style="color:#daa520">${names[cp]}</b></div>
      ${state.can_pass ? '<div style="font-size:0.65em;color:#888;margin-bottom:3px">可过牌</div>' : ''}
      <div style="display:flex;gap:6px;justify-content:center;margin-top:4px">
        <button class="btn btn-gn btn-sm" onclick="manualPlayC()">出牌</button>
        ${state.can_pass ? '<button class="btn btn-gy btn-sm" onclick="manualPassC()">过牌</button>' : ''}
      </div>
      <div style="margin-top:6px;display:flex;gap:6px;justify-content:center">
        <button class="btn btn-g btn-sm" onclick="customAnalyze()">分析</button>
        <button class="btn btn-gy btn-sm" onclick="customLoadFromSim(C.simState)">编辑</button>
      </div>`;
  }
}

// ==================================================================
// Start simulation
// ==================================================================
// The side button is mode-dispatching: in builder mode it starts the sim,
// in sim mode it toggles the analysis (its label mirrors the center panel
// but they are separate DOM nodes — the label change alone used to leave
// it dead, clicking "分析" did nothing).
async function customStart() {
  if (C.sim_mode) { runC(); return; }
  if (C.starting) return;   // in-flight guard: double-click created orphan simulators
  if (C.running) stopC();   // old analysis must not leak into the new sim
  // Hard validation BEFORE submit: an illegal table combo used to be
  // silently dropped by the backend (its cards vanished from the game).
  const v = czValidate();
  if (!v.valid) { alert('无法开始推演: ' + v.reason); return; }
  for (let p = 0; p <= 3; p++) {
    const zp = C.zone_play[p];
    if (zp.action !== 'play' || !zp.cards || zp.cards.length === 0) continue;
    const ok = await new Promise(res => {
      fetch('/api/arena/check_combo', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({card_ids: [...zp.cards], level: C.level}),
      }).then(r => r.json()).then(d => res(!!d.valid)).catch(() => res(false));
    });
    if (!ok) { alert(`玩家 ${p + 1} 的出牌区不是合法牌型，请修正后再开始`); return; }
  }
  // Duplicate-card check (hands + trick action cards)
  const assigned = new Set();
  for (let p = 0; p <= 3; p++) {
    for (const cid of C.hands[p]) {
      if (assigned.has(cid)) {
        alert('错误: 牌 ' + cid + ' 重复分配!'); return;
      }
      assigned.add(cid);
    }
  }
  for (let p = 0; p <= 3; p++) {
    for (const cid of (C.zone_play[p].cards || [])) {
      if (assigned.has(cid)) {
        alert('错误: 桌面牌 ' + cid + ' 已分配!'); return;
      }
      assigned.add(cid);
    }
  }
  // Show what will happen to the unassigned pool cards
  const unassigned = 108 - assigned.size;
  // C.hands is a {0:[],1:[],2:[],3:[]} object — .map() on it threw a
  // TypeError before confirm() ever showed, bricking this button entirely.
  const handSummary = [0, 1, 2, 3].map(p => C.hands[p].length).join('/');
  if (!confirm(`开始推演？\n手牌: ${handSummary} 张\n未分配的 ${unassigned} 张牌将被视为「已打出」。\n级牌 Lv${C.level}`)) return;

  const body = {
    hands: {'0': C.hands[0], '1': C.hands[1], '2': C.hands[2], '3': C.hands[3]},
    table: C.getTableCards(),
    table_player: Math.max(0, C.getTablePlayer()),
    current_player: C.getCurrentPlayer(),
    level: C.level,
  };

  C.starting = true;
  const startBtn = document.getElementById('btn-ac');
  if (startBtn) { startBtn.disabled = true; startBtn.textContent = '初始化…'; }
  try {
    const r = await fetch('/api/arena/sim/init_custom', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const state = await r.json().catch(() => ({}));
    if (!r.ok || state.error) { C.sim_mode = false; alert('初始化失败: ' + (state.error || ('HTTP ' + r.status))); return; }
    C.simState = state;
    C.simId = state.sim_id;
    C.res = [];
    C.selected.clear();
    document.getElementById('cres').innerHTML = '<span class="emp">点击分析</span>';
    document.getElementById('crcnt').textContent = '';

    // Switch to sim mode — reuse builder layout
    C.sim_mode = true;
    renderSimState(C.simState);
  } catch (e) {
    C.sim_mode = false;
    alert('请求失败: ' + e.message);
  } finally {
    C.starting = false;
    if (startBtn) {
      if (C.sim_mode) {
        // Sim mode reuses this button as 分析/停止 — the stale disabled
        // "初始化…" label used to survive the mode switch.
        startBtn.disabled = false;
        startBtn.textContent = '分析';
        startBtn.className = 'btn btn-g btn-sm';
      } else {
        startBtn.disabled = false;
        startBtn.textContent = '开始推演';
      }
    }
  }
}

// ==================================================================
// Sim step and analysis for custom tab
// ==================================================================
function toggleArenaCardC(cid) {
  if (C.selected.has(cid)) C.selected.delete(cid); else C.selected.add(cid);
  document.querySelectorAll(`#custom-builder .card[data-cid="${cid}"]`).forEach(el => {
    el.classList.toggle('selected', C.selected.has(cid));
  });
}

function manualPlayC() {
  if (!C.simId || C.selected.size===0) return;
  stepC(Array.from(C.selected), false);
}

function manualPassC() {
  if (!C.simId) return;
  stepC([], true);
}

async function stepC(cardIds, pass) {
  if (!C.simId) return;
  if (C.stepping) return;   // in-flight guard
  const hadRunning = !!(C.running && C.abort);
  if (hadRunning) stopC();
  C.stepping = true;
  C._suppressRender = true;
  C.res = [];
  const presEl = document.getElementById('cres');
  presEl.innerHTML = hadRunning ? '<div style="color:#e67e22;font-size:0.78em;padding:4px;margin-bottom:4px;background:rgba(230,126,34,0.08);border-radius:4px">已放弃等待中的分析</div>' : '<span class="emp">已执行，点击分析继续</span>';
  const crcntEl = document.getElementById('crcnt');
  if (crcntEl) crcntEl.textContent = '';

  try {
    const body = {sim_id: C.simId, pass: !!pass};
    if (!pass) body.card_ids = cardIds;
    const r = await fetch('/api/arena/sim/step', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const newState = await r.json().catch(() => ({}));
    if (r.status === 404 || newState.error === 'Simulator not found') {
      // The sim expired or was evicted — clear the dead handle so the UI
      // stops firing every later action at a ghost (permanent mismatch).
      C.simId = null; C.simState = null;
      presEl.innerHTML = `<span style="color:#e74c3c;font-size:0.82em">模拟器已过期，请重新开始推演</span>`;
      return;
    }
    if (newState.error) {
      presEl.innerHTML = `<span style="color:#e74c3c;font-size:0.82em">${newState.error}</span>` + presEl.innerHTML;
      return;
    }
    C.simState = newState;
    C.selected.clear();
    renderSimState(C.simState);
  } catch (e) {
    presEl.innerHTML = `<span style="color:#e74c3c;font-size:0.82em">请求失败: ${e.message}</span>` + presEl.innerHTML;
  } finally {
    C.stepping = false;
    C._suppressRender = false;
  }
}

async function runC() {
  if (!C.simId) return;
  if (C.running) { stopC(); return; }
  C.running=true; C.res=[]; C.abort=new AbortController();
  // Epoch guard: rebuilding the sim (编辑→重新开始推演) invalidates results
  // from the old one — late responses used to pollute the new sim's panel.
  C.runSeq = (C.runSeq||0) + 1;
  const seq = C.runSeq;
  document.getElementById('cres').innerHTML=''; const btn=document.getElementById('btn-ac');
  if (btn) { btn.textContent='停止'; btn.className='btn btn-red btn-sm'; }
  const models = C.profs.map(p=>{const m={id:p.id,label:p.label};for(const k of Object.keys(p)){if(k!=='id'&&k!=='label')m[k]=p[k];}return m;});
  const crcntEl = document.getElementById('crcnt');
  if (crcntEl) crcntEl.textContent=`(0/${models.length})`;
  // Serial like runT: per-request game_logger.clear() clobbers parallel
  // debug logs; parallelism also made stale-response ordering unpredictable.
  for (const m of models) {
    try {
      const r = await fetch('/api/arena/sim/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sim_id:C.simId,models:[m]}),signal:C.abort.signal});
      if (seq !== C.runSeq) return;
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        C.res.push({model_id:m.id,model_name:m.label||m.id,error:d.error||('HTTP '+r.status)});
      }
      else { const d=await r.json(); if(d.results)C.res.push(...d.results); }
    } catch(e) { if(e.name!=='AbortError') C.res.push({model_id:m.id,model_name:m.label||m.id,error:e.message}); }
    if (seq !== C.runSeq) return;
    renderResC(); const c2=document.getElementById('crcnt'); if (c2) c2.textContent=`(${C.res.length}/${models.length})`;
  }
  if (seq !== C.runSeq) return;
  finishC();
}
function stopC() { C.runSeq = (C.runSeq||0) + 1; if(C.abort){C.abort.abort();C.abort=null;} finishC(); }
function finishC() {
  C.running=false;
  const btn=document.getElementById('btn-ac');
  if (!btn) return;   // sim mode replaced the panel — no button to fix
  if (C.sim_mode) { btn.textContent='分析'; btn.className='btn btn-gn btn-sm'; btn.disabled=false; }
  else { btn.textContent='开始推演'; btn.className='btn btn-gn'; btn.disabled=false; }
}

function renderResC() {
  if (C._suppressRender) return;
  const el=document.getElementById('cres'); if(!C.res.length){el.innerHTML='<span class="emp">无结果</span>';return;}
  const openModels = new Set();
  el.querySelectorAll('.mres .mcand[open]').forEach(d => {
    const mn = d.closest('.mres')?.querySelector('.mn')?.textContent;
    if (mn) openModels.add(mn);
  });
  el.innerHTML = C.res.map(r=>{
    if(r.error) return `<div class="mres"><div class="mn">${r.model_name||r.model_id}</div><div class="me">${r.error}</div></div>`;
    const m=r.metrics||{},ch=r.choice;
    const isPass = r.pass_chosen;
    const chCards = ch?ch.card_ids||[]:[];
    let chH = isPass?'<span style="color:#e67e22;font-weight:bold">过牌</span>':(ch?`<span class="combo-tag" style="background:#daa520;color:#222;padding:1px 4px;border-radius:3px;font-size:0.7em">${ch.combo_type||'?'}</span> ${(ch.cards||[]).join(' ')}`:'?');
    let stepBtn = `<button class="btn btn-gn btn-sm" onclick="stepC(${JSON.stringify(chCards)},${isPass})" style="margin-left:6px">执行</button>`;
    const cds=r.candidates||[];
    const mn = r.model_name||r.model_id;
    const openAttr = openModels.has(mn) ? ' open' : '';
    let cdHtml = cds.length?`<details class="mcand"${openAttr}><summary>候选 (${cds.length})</summary>`+cds.map((c,i)=>{const best=i===0;const cad=c.card_ids||[];const isCp=c.combo_type==='PASS';
      const detHtml = _renderDetail(c.detail, r.model_id);
      return `<div class="crow${best?' best':''}"><div class="cr-main">${isCp?'<span style="color:#e67e22;font-weight:bold">过牌</span>':`<span class="combo-tag" style="background:#daa520;color:#222;padding:1px 2px;border-radius:2px;font-size:0.62em">${c.combo_type||'?'}</span> ${(c.cards||[]).join(' ')}`}<span style="color:#aaa;font-size:0.78em;margin-left:auto">${c.score!=null?c.score.toFixed(1):''}${c.win_rate!=null?' | '+(c.win_rate*100).toFixed(1)+'%':''}</span><button class="btn btn-gn btn-sm" onclick="stepC(${JSON.stringify(cad)},${isCp})" style="margin-left:4px;font-size:0.65em;padding:1px 5px">执行</button></div>${detHtml?`<div class="cr-detail">${detHtml}</div>`:''}</div>`}).join('')+'</details>':'';
    return `<div class="mres"><div class="mh"><span class="mn">${mn}</span><span class="mt">${m.elapsed_ms!=null?m.elapsed_ms.toFixed(0)+'ms':''}${m.timed_out?' ⚠':''}</span></div><div class="mc">${chH} ${ch&&ch.score!=null?`<span class="mw">${ch.score.toFixed(1)}</span>`:''} ${ch&&ch.win_rate!=null?`<span class="mw">${(ch.win_rate*100).toFixed(1)}%</span>`:''} ${stepBtn}</div>${cdHtml}</div>`;
  }).join('');
}

function customAnalyze() {
  runC();
}

// ==================================================================


init();
