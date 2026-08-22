/* ================================================================
   Guandan Web UI — Frontend Logic
   ================================================================ */

let gameState = null;
let selectedCards = new Set();       // card IDs selected in main hand
let activeStrip = null;              // index of selected combo strip (or null)
let debugMode = true;                // default ON
let flatLayout = false;              // false = vertical columns, true = horizontal
let comboStrips = [];                // [{cardIds: [id,...]}, ...]
let aiDebugOpen = false;             // AI debug panel
let showWinRate = true;              // win rate overlay — default ON
let _playerWinRates = {};            // {player_id: win_rate}
let _playerTimedOut = {};            // {player_id: bool} timeout flag
let pollTimer = null;                // polling interval ID

const SUIT_CHARS = {0: '♣', 1: '♦', 2: '♥', 3: '♠', 4: ''};
const SUIT_NAMES = {0: 'C', 1: 'D', 2: 'H', 3: 'S', 4: ''};
const PLAYER_NAMES = ['你', 'AI-右家', 'AI-对家', 'AI-左家'];

// ==================================================================
// API
// ==================================================================

async function fetchState() {
  gameState = await gameApi.getState();
  render();
  if (debugMode) fetchDebug();
  if (aiDebugOpen || showWinRate) fetchAILog();
}

function schedulePoll() {
  pollTimer = setTimeout(async () => {
    gameState = await gameApi.getState();
    render();
    if (debugMode) fetchDebug();
    if (aiDebugOpen || showWinRate) fetchAILog();
    if (llmThinkingOpen) fetchLLMReasoning();
    if (gameState.my_turn || gameState.game_over || gameState.round_over) {
      stopPolling();
    } else {
      schedulePoll();  // re-schedule after current request completes
    }
  }, 400);
}

function startPolling() {
  if (pollTimer) return;
  schedulePoll();
}

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

async function playCards() {
  if (!myTurn) return;
  let cardIds;

  if (activeStrip !== null && activeStrip < comboStrips.length) {
    cardIds = comboStrips[activeStrip].cardIds;
  } else if (selectedCards.size > 0) {
    cardIds = Array.from(selectedCards);
  } else {
    return;
  }

  gameState = await gameApi.playCards(cardIds);
  selectedCards.clear();
  activeStrip = null;
  if (cardIds) {
    const playedSet = new Set(cardIds);
    comboStrips = comboStrips.filter(s => {
      s.cardIds = s.cardIds.filter(id => !playedSet.has(id));
      return s.cardIds.length > 0;
    });
  }
  // Auto-close suggestion panel after playing
  document.getElementById('suggest-panel').style.display = 'none';
  render();
  if (debugMode) fetchDebug();
}

async function passTurn() {
  if (!myTurn) return;
  gameState = await gameApi.passTurn();
  selectedCards.clear();
  activeStrip = null;
  document.getElementById('suggest-panel').style.display = 'none';
  render();
  if (debugMode) fetchDebug();
}

async function newGame() {
  gameState = await gameApi.newGame();
  selectedCards.clear();
  activeStrip = null;
  comboStrips = [];
  clearPlayZones();
  document.getElementById('suggest-panel').style.display = 'none';
  render();
  if (debugMode) fetchDebug();
}

async function newRound() {
  gameState = await gameApi.newRound();
  selectedCards.clear();
  activeStrip = null;
  comboStrips = [];
  clearPlayZones();
  document.getElementById('suggest-panel').style.display = 'none';
  render();
  if (debugMode) fetchDebug();
}

function clearPlayZones() {
  for (let p = 0; p < 4; p++) {
    document.getElementById('play-' + p).innerHTML = '';
  }
}

async function showHint() {
  if (!myTurn || !gameState) return;
  const data = await gameApi.getHint();
  selectedCards.clear();
  activeStrip = null;
  if (data.card_ids) {
    for (const id of data.card_ids) selectedCards.add(id);
  }
  render();
}

async function fetchDebug() {
  const data = await gameApi.getDebug();
  if (data.hands) renderDebugHands(data.hands);
}

// ==================================================================
// Combo organizer
// ==================================================================

function organizeCombo() {
  if (selectedCards.size === 0) return;
  const cardIds = Array.from(selectedCards);
  comboStrips.push({cardIds: cardIds});
  selectedCards.clear();
  activeStrip = comboStrips.length - 1;
  render();
}

function selectStrip(index) {
  if (activeStrip === index) {
    // Deselect
    activeStrip = null;
  } else {
    activeStrip = index;
    selectedCards.clear();
  }
  render();
}

function removeStrip(index) {
  comboStrips.splice(index, 1);
  if (activeStrip === index) activeStrip = null;
  else if (activeStrip > index) activeStrip--;
  render();
}

// ==================================================================
// Layout toggle
// ==================================================================

function toggleLayout() {
  flatLayout = !flatLayout;
  const btn = document.getElementById('btn-layout');
  btn.textContent = flatLayout ? '纵排' : '横排';
  render();
}

// ==================================================================
// Debug toggle
// ==================================================================

function toggleDebug() {
  debugMode = !debugMode;
  const btn = document.getElementById('btn-debug');
  btn.className = debugMode ? '' : 'off';
  btn.textContent = debugMode ? '隐藏牌面' : '显示牌面';

  if (debugMode) {
    fetchDebug();
  } else {
    for (let p = 0; p < 4; p++) {
      const el = document.getElementById('debug-hand-' + p);
      if (el) el.style.display = 'none';
    }
  }
}

// ==================================================================
// Render
// ==================================================================

function render() {
  if (!gameState) return;
  myTurn = gameState.my_turn;

  // Start overlay
  const started = gameState.game_started;
  const overlay = document.getElementById('start-overlay');
  if (overlay) overlay.style.display = started ? 'none' : 'block';
  document.getElementById('controls').style.display = started ? 'block' : 'none';
  if (!started) {
    document.getElementById('message').textContent = gameState.message || '点击「开始游戏」';
    for (let p = 0; p < 4; p++) {
      const info = gameState.players[p];
      document.getElementById('size-' + p).textContent = info.hand_size;
      renderDeciderBadge(p, info.decider);
    }
    renderHand(gameState.my_hand);
    if (debugMode) fetchDebug();
    return;
  }

  document.getElementById('info-level').textContent = gameState.level;
  document.getElementById('info-round').textContent = gameState.round;
  document.getElementById('info-trick').textContent = gameState.trick;
  document.getElementById('info-team0').textContent = gameState.team_level_0 || gameState.level;
  document.getElementById('info-team1').textContent = gameState.team_level_1 || gameState.level;

  for (let p = 0; p < 4; p++) {
    const info = gameState.players[p];
    document.getElementById('size-' + p).textContent = info.hand_size;
    const ft = document.getElementById('finish-' + p);
    ft.style.display = info.finished ? 'inline' : 'none';
    const zone = document.getElementById('zone-' + p);
    zone.classList.toggle('finished', info.finished);
    // Highlight thinking AI
    const thinking = gameState.ai_thinking_player;
    zone.classList.toggle('thinking-zone', thinking >= 0 && thinking === p);
    // Decider badge
    renderDeciderBadge(p, info.decider);
  }

  renderTrickHistory(gameState.trick_history || []);
  renderHand(gameState.my_hand);
  renderComboStrips();

  document.getElementById('btn-play').disabled = !myTurn || (selectedCards.size === 0 && activeStrip === null);
  document.getElementById('btn-pass').disabled = !myTurn || !gameState.can_pass;
  document.getElementById('btn-hint').disabled = !myTurn;
  document.getElementById('btn-suggest').disabled = !myTurn;
  document.getElementById('btn-organize').disabled = selectedCards.size === 0;
  // Auto-play button state
  const autoBtn = document.getElementById('btn-auto-play');
  const isAuto = gameState.auto_play;
  autoBtn.textContent = isAuto ? '停止自动' : '自动对局';
  autoBtn.style.background = isAuto ? '#c0392b' : '#555';
  document.getElementById('btn-new-round').style.display =
    (gameState.round_over && !gameState.game_over) ? 'inline-block' : 'none';

  const msgEl = document.getElementById('message');
  msgEl.textContent = gameState.message || '';
  msgEl.className = gameState.error ? 'error' : '';
  if (gameState.message === 'AI思考中...' || gameState.ai_running) {
    msgEl.classList.add('thinking');
    document.getElementById('btn-play').disabled = true;
    document.getElementById('btn-pass').disabled = true;
    startPolling();
  } else {
    stopPolling();
  }

  renderHistory(gameState.round_results || []);
}

function renderDeciderBadge(p, decider) {
  const el = document.getElementById('decider-' + p);
  if (!el) return;
  // Human player always shows '你'
  if (p === 0) {
    el.textContent = '你';
    el.className = 'decider-badge human';
    el.title = '人类玩家';
    return;
  }
  if (!decider) { decider = { id: '?', label: '?', icon: '?', params: [], description: '' }; }
  el.textContent = decider.icon;
  el.className = 'decider-badge';
  if (decider.id === 'llm') el.classList.add('decider-llm');
  else if (decider.id === 'mc') el.classList.add('decider-mc');
  else if (decider.id === 'ismcts') el.classList.add('decider-ismcts');
  else if (decider.id === 'blind') el.classList.add('decider-blind');
  // Build tooltip
  var parts = [decider.label];
  if (decider.description) parts.push(decider.description);
  if (decider.params && decider.params.length) {
    parts.push('---');
    parts = parts.concat(decider.params);
  }
  el.title = parts.join('\n');
}

function renderTrickHistory(history) {
  // Preserve last display when trick completes and history is briefly empty
  if (!history || history.length === 0) return;

  for (let p = 0; p < 4; p++) {
    document.getElementById('play-' + p).innerHTML = '';
  }

  for (const entry of history) {
    const zone = document.getElementById('play-' + entry.player);
    if (!zone) continue;
    let wrLabel = '';
    if (showWinRate) {
      let wr = null;
      if (entry.player === 0) {
        wr = gameState.human_win_rate;
      } else {
        wr = _playerWinRates[entry.player];
      }
      if (wr != null) {
        wrLabel = ' <span style="font-size:0.65em;color:#f1c40f;font-weight:bold">' + (wr * 100).toFixed(1) + '%</span>';
        if (_playerTimedOut[entry.player]) {
          wrLabel += ' <span style="font-size:0.6em;color:#e67e22">⚠</span>';
        }
      }
    }
    if (entry.pass) {
      zone.innerHTML = '<span class="pass-text">过</span>' + wrLabel;
    } else if (entry.combo) {
      let html = '<span class="combo-tag">' + (entry.combo.type_cn || entry.combo.type) + '</span> ';
      for (const c of entry.combo.cards) html += cardHTML(c, true);
      zone.innerHTML = html + wrLabel;
    }
  }
}

function renderHand(hand) {
  const container = document.getElementById('my-hand');
  container.innerHTML = '';
  container.className = flatLayout ? 'flat-layout' : '';

  if (!hand || hand.length === 0) {
    container.innerHTML = '<span style="color:#888;padding:20px">手牌已出完</span>';
    return;
  }

  // Remove cards that are in combo strips from the main hand display
  const stripCardIds = new Set();
  for (const strip of comboStrips) {
    for (const id of strip.cardIds) stripCardIds.add(id);
  }
  const displayHand = hand.filter(c => !stripCardIds.has(c.id));

  if (!flatLayout) {
    // Column layout: group by rank
    const byRank = {};
    for (const c of displayHand) {
      if (!byRank[c.rank]) byRank[c.rank] = [];
      byRank[c.rank].push(c);
    }
    const ranks = Object.keys(byRank).map(Number).sort((a, b) => a - b);

    for (const rank of ranks) {
      const cards = byRank[rank];
      cards.sort((a, b) => a.suit - b.suit);

      const col = document.createElement('div');
      col.className = 'rank-column';
      col.title = cards.length + '张 ' + cards[0].rank_name;
      col.onclick = (e) => {
        e.stopPropagation();
        const allSel = cards.every(c => selectedCards.has(c.id));
        if (allSel) cards.forEach(c => selectedCards.delete(c.id));
        else cards.forEach(c => selectedCards.add(c.id));
        activeStrip = null;
        render();
      };

      for (const c of cards) {
        col.appendChild(createCardElement(c));
      }
      container.appendChild(col);
    }
  } else {
    // Flat layout
    const sorted = [...displayHand].sort((a, b) => {
      if (a.rank !== b.rank) return a.rank - b.rank;
      return a.suit - b.suit;
    });
    for (const c of sorted) {
      container.appendChild(createCardElement(c));
    }
  }
}

function createCardElement(c) {
  const sel = selectedCards.has(c.id) ? ' selected' : '';
  const wild = c.is_wild ? ' wild' : '';
  let suitClass;
  if (c.is_joker) {
    suitClass = c.rank_name === 'SJ' ? 'joker-black' : 'joker-red';
  } else {
    suitClass = 'suit-' + SUIT_NAMES[c.suit];
  }
  const div = document.createElement('div');
  div.className = 'card' + sel + wild + ' ' + suitClass;
  div.setAttribute('data-card-id', c.id);
  div.onclick = (e) => {
    e.stopPropagation();
    toggleCard(c.id);
  };
  div.innerHTML =
    '<span class="card-rank">' + c.rank_name + '</span>' +
    '<span class="card-suit-big">' + SUIT_CHARS[c.suit] + '</span>';
  return div;
}

function cardHTML(c, small) {
  let suitClass;
  if (c.is_joker) {
    suitClass = c.rank_name === 'SJ' ? 'joker-black' : 'joker-red';
  } else {
    suitClass = 'suit-' + SUIT_NAMES[c.suit];
  }
  const wild = c.is_wild ? ' wild' : '';
  return '<div class="card' + wild + ' ' + suitClass + '">' +
    '<span class="card-rank">' + c.rank_name + '</span>' +
    '<span class="card-suit-big">' + SUIT_CHARS[c.suit] + '</span>' +
    '</div>';
}

function renderComboStrips() {
  const container = document.getElementById('combo-strips');
  container.innerHTML = '';

  for (let i = 0; i < comboStrips.length; i++) {
    const strip = comboStrips[i];
    const selClass = (activeStrip === i) ? ' selected-strip' : '';

    // Build the strip
    const stripEl = document.createElement('div');
    stripEl.className = 'combo-strip' + selClass;
    stripEl.title = '点击选中出牌，悬停显示删除';
    stripEl.onclick = (e) => {
      e.stopPropagation();
      selectStrip(i);
    };

    // Get card objects from gameState for display
    const handMap = {};
    if (gameState && gameState.my_hand) {
      for (const c of gameState.my_hand) handMap[c.id] = c;
    }

    for (const id of strip.cardIds) {
      const c = handMap[id];
      if (c) {
        stripEl.innerHTML += cardHTML(c, true);
      }
    }

    // Close button
    const closeBtn = document.createElement('button');
    closeBtn.className = 'strip-close';
    closeBtn.textContent = '×';
    closeBtn.onclick = (e) => {
      e.stopPropagation();
      removeStrip(i);
    };
    stripEl.appendChild(closeBtn);

    container.appendChild(stripEl);
  }
}

function renderHistory(roundResults) {
  const hist = document.getElementById('history');
  if (!roundResults || roundResults.length === 0) { hist.innerHTML = ''; return; }
  hist.innerHTML = roundResults.map((rr, i) => {
    const names = rr.positions.map(p => p.name).join(' → ');
    const winLabel = rr.winning_team === 0 ? '我方' : '对方';
    return '<span class="history-item">#' + (i + 1) + ': ' + names + ' | ' + winLabel + '+' + rr.level_change + '级</span>';
  }).join('');
}

function renderDebugHands(hands) {
  if (!hands || !debugMode) return;
  for (const h of hands) {
    const el = document.getElementById('debug-hand-' + h.player);
    if (!el) continue;
    el.style.display = 'flex';
    let html = '';
    for (const c of h.cards) html += cardHTML(c, true);
    el.innerHTML = html;
  }
}

// ==================================================================
// Interaction
// ==================================================================

function toggleCard(cardId) {
  if (selectedCards.has(cardId)) {
    selectedCards.delete(cardId);
  } else {
    selectedCards.add(cardId);
  }
  activeStrip = null;  // deselect strip when manually selecting cards
  render();
}

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    if (myTurn && (selectedCards.size > 0 || activeStrip !== null)) playCards();
  } else if (e.key === ' ') {
    e.preventDefault();
    if (myTurn && gameState && gameState.can_pass) passTurn();
  } else if (e.key === 'd' && e.ctrlKey) {
    e.preventDefault();
    toggleDebug();
  }
});

// ==================================================================
// AI Debug Panel
// ==================================================================

function toggleAIDebug() {
  aiDebugOpen = !aiDebugOpen;
  const panel = document.getElementById('ai-debug-panel');
  const btn = document.getElementById('btn-ai-debug');
  if (aiDebugOpen) {
    panel.classList.add('show');
    btn.classList.add('active');
    fetchAILog();
  } else {
    panel.classList.remove('show');
    btn.classList.remove('active');
  }
}

async function fetchAILog() {
  const data = await gameApi.getAILog();
  renderAILog(data);
}

async function clearAILog() {
  await gameApi.clearAILog();
  document.getElementById('ai-debug-content').innerHTML =
    '<div style="color:#888;padding:10px">日志已清空</div>';
}

// LLM Thought Bubbles — per-player floating bubbles on the table
let llmThinkingOpen = true;
let _llmBubbleData = {};  // {pid: {choice, reasoning, ...}}

function toggleLLMBubbles() {
  llmThinkingOpen = !llmThinkingOpen;
  const btn = document.getElementById('btn-llm-think');
  if (llmThinkingOpen) {
    btn.style.background = '#9b59b6'; btn.style.color = '#fff';
    for (let p = 1; p <= 3; p++) {
      const b = document.getElementById('llm-bubble-' + p);
      if (b && _llmBubbleData[p]) b.style.display = 'block';
    }
    fetchLLMReasoning();
  } else {
    btn.style.background = '#555'; btn.style.color = '#999';
    for (let p = 1; p <= 3; p++) {
      const b = document.getElementById('llm-bubble-' + p);
      if (b) b.style.display = 'none';
    }
  }
}

async function fetchLLMReasoning() {
  if (!llmThinkingOpen) return;
  try {
    const resp = await fetch('/api/llm_reasoning');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    renderLLMReasoning(data);
  } catch (e) { /* silent */ }
}

function renderLLMReasoning(data) {
  const entries = data.entries || [];
  // Index by player
  const byPlayer = {};
  for (const e of entries) { byPlayer[e.player] = e; }
  _llmBubbleData = byPlayer;

  for (let p = 1; p <= 3; p++) {
    const bubble = document.getElementById('llm-bubble-' + p);
    if (!bubble) continue;
    const e = byPlayer[p];
    if (!e) {
      bubble.style.display = 'none';
      continue;
    }
    if (!llmThinkingOpen) continue;
    bubble.style.display = 'block';
    bubble.classList.remove('stale');

    const choice = e.choice || '?';
    const cards = (e.cards || []).join(' ');
    const elapsed = e.elapsed_ms ? (e.elapsed_ms / 1000).toFixed(1) + 's' : '';
    const reasoning = e.reasoning || '';
    const isFallback = choice.startsWith('FALLBACK');
    const shortReasoning = reasoning.length > 80 ? reasoning.slice(0, 80) + '...' : reasoning;
    const sc = e.success_count != null ? ' ok' + e.success_count + '/fb' + (e.fallback_count||0) : '';

    let html = '';
    if (isFallback) {
      html += '<div class="bubble-choice" style="color:#e67e22">' + escapeHtml(choice) + '</div>';
    } else {
      html += '<div class="bubble-choice">' + escapeHtml(choice) + '</div>';
      if (cards) html += '<div class="bubble-choice" style="font-size:0.85em;color:#aaa">' + escapeHtml(cards) + '</div>';
    }
    if (reasoning && !isFallback) {
      const full = reasoning.length > 1500 ? reasoning.slice(0, 1500) + '\n\n... (truncated)' : reasoning;
      html += '<div class="bubble-reason" data-full="' + escapeAttr(full) + '" data-short="' + escapeAttr(shortReasoning) + '">' + escapeHtml(shortReasoning) + '</div>';
    }
    html += '<div class="bubble-meta">' + elapsed + ' ' + sc + '</div>';
    bubble.innerHTML = html;

    // Click to expand/collapse reasoning
    const reasonDiv = bubble.querySelector('.bubble-reason');
    if (reasonDiv) {
      bubble.onclick = function() {
        const expanded = reasonDiv.classList.toggle('expanded');
        reasonDiv.textContent = expanded
          ? (reasonDiv.getAttribute('data-full') || '')
          : (reasonDiv.getAttribute('data-short') || '');
      };
    }
  }
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
function escapeAttr(text) {
  return text.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderAILog(data) {
  const container = document.getElementById('ai-debug-content');

  // Update per-player win rates from latest decision_end entries
  if (data && data.entries) {
    for (const e of data.entries) {
      if (e.type === 'decision_end' && e.data.choice_win_rate != null) {
        _playerWinRates[e.player] = e.data.choice_win_rate;
        _playerTimedOut[e.player] = e.data.timed_out || false;
      }
    }
    // Re-render trick history so win rates appear immediately
    if (gameState && gameState.trick_history) {
      renderTrickHistory(gameState.trick_history);
    }
  }

  if (!data || !data.entries || data.entries.length === 0) {
    container.innerHTML = '<div style="color:#888;padding:10px">暂无AI决策记录</div>';
    return;
  }

  // Config summary line
  let configLine = '';
  if (currentConfig) {
    const labels = {blind:'Blind', informed:'Informed', round:'Round', exact:'Exact', mc:'MC'};
    configLine = '<div style="font-size:0.75em;color:#888;margin-bottom:6px;padding-bottom:4px;border-bottom:1px solid #333">全局: ' +
      (labels[currentConfig.global_decider] || currentConfig.global_decider || '?');
    for (const p of [1,2,3]) {
      const pc = (currentConfig.players || {})[p] || {};
      if (pc.decider) configLine += ' | ' + ['','右','对','左'][p] + '家: ' + (labels[pc.decider]||pc.decider);
    }
    configLine += '</div>';
  }

  // Group entries by decision cycle
  let html = '';
  let currentCycle = null;

  for (const entry of data.entries) {
    const playerName = ['你', '右AI', '对AI', '左AI'][entry.player] || 'P' + entry.player;

    if (entry.type === 'decision_start') {
      // Start a new decision block
      if (currentCycle) html += '</div>';
      html += '<div class="ai-log-entry">';
      html += '<div class="entry-header">';
      html += '<span class="entry-player">' + playerName + '</span>';
      html += '<span class="entry-type">' + (entry.data.agent || 'Heuristic') + '</span>';
      html += '<span class="entry-time">手牌' + (entry.data.hand_size || '?') + '张</span>';
      html += '</div>';
      currentCycle = entry;
    } else if (entry.type === 'candidates') {
      // Render candidate table
      const candidates = entry.data.candidates || [];
      if (candidates.length > 0) {
        html += '<table class="ai-candidates-table">';
        html += '<tr><th>牌型</th><th>牌</th><th>张数</th><th class="score-col">评分</th></tr>';
        let bestScore = -Infinity;
        for (const c of candidates) {
          if (c.score > bestScore) bestScore = c.score;
        }
        for (const c of candidates) {
          const isBest = c.score === bestScore;
          const rowClass = isBest ? 'best-row' : '';
          html += '<tr class="' + rowClass + '">';
          html += '<td>' + c.type + '</td>';
          html += '<td>' + (c.cards || []).join(' ') + '</td>';
          html += '<td>' + c.length + '</td>';
          html += '<td class="score-col">' + c.score.toFixed(1) + '</td>';
          html += '</tr>';
        }
        html += '</table>';
      }
    } else if (entry.type === 'decision_end') {
      const choice = entry.data.choice || '?';
      const choiceWR = entry.data.choice_win_rate;
      const elapsed = entry.data.elapsed_ms || 0;
      html += '<div style="font-size:0.78em;color:#aaa">';
      html += '选择: <b style="color:#27ae60">' + choice + '</b>';
      if (choiceWR != null) {
        html += ' | 胜率: <b style="color:#f1c40f">' + (choiceWR * 100).toFixed(1) + '%</b>';
      }
      html += ' <span style="color:#666">(' + elapsed + 'ms)</span>';
      if (entry.data.timed_out) {
        html += ' <span style="color:#e67e22;font-size:0.75em" title="超时截断，胜率基于部分采样">⚠超时</span>';
      }
      html += '</div>';
      if (entry.data.candidates_scored) {
        html += '<table class="ai-candidates-table">';
        html += '<tr><th>候选</th><th class="score-col">胜率</th></tr>';
        for (const c of entry.data.candidates_scored) {
          html += '<tr>';
          html += '<td>' + (c.type || '?') + ' ' + (c.cards || []).join(' ') + '</td>';
          html += '<td class="score-col">' + (c.win_rate * 100).toFixed(1) + '%</td>';
          html += '</tr>';
        }
        html += '</table>';
      }
    }
  }
  if (currentCycle) html += '</div>';

  container.innerHTML = configLine + html;
  container.scrollTop = container.scrollHeight;
}

// ==================================================================
// Win Rate overlay toggle
// ==================================================================

function toggleWinRate() {
  showWinRate = !showWinRate;
  const btn = document.getElementById('btn-winrate');
  btn.style.background = showWinRate ? '#27ae60' : '#555';
  btn.style.color = showWinRate ? '#fff' : '#999';
  if (showWinRate) fetchAILog();
  render();
}

// ==================================================================
// Start Game
// ==================================================================

async function startGame() {
  gameState = await gameApi.startGame();
  render();
  if (debugMode) fetchDebug();
}

// ==================================================================
// Auto-play toggle
// ==================================================================

async function toggleAutoPlay() {
  const btn = document.getElementById('btn-auto-play');
  const isAuto = btn.textContent.includes('停止');
  const d = await gameApi.toggleAutoPlay(!isAuto);
  btn.textContent = d.auto_play ? '停止自动' : '自动对局';
  btn.style.background = d.auto_play ? '#c0392b' : '#555';
  if (d.auto_play) {
    selectedCards.clear();
    activeStrip = null;
    fetchState();  // will start auto-play polling
  }
}

// ==================================================================
// AI Suggestion (semi-auto human assist)
// ==================================================================

async function aiSuggest() {
  if (!myTurn || !gameState) return;
  const panel = document.getElementById('suggest-panel');
  const content = document.getElementById('suggest-content');
  panel.style.display = 'block';
  content.innerHTML = '<span style="color:#aaa">AI 正在分析...</span>';

  const d = await gameApi.getSuggest();
  const hasOnlyPass = (!d.candidates || d.candidates.length === 0) ||
    (d.candidates.length === 1 && d.candidates[0].type === 'PASS');
  if (hasOnlyPass) {
    if (d.candidates && d.candidates.length === 1 && d.candidates[0].type === 'PASS') {
      // Show pass win rate
      const wr = (d.candidates[0].win_rate * 100).toFixed(1);
      content.innerHTML = '<span style="color:#e67e22">手牌中没有任何能压过牌桌的牌型</span>' +
        '<br><span style="font-size:0.85em;color:#f1c40f">过牌胜率: ' + wr + '%</span>' +
        '<br><button onclick="passTurn()" style="background:#e67e22;color:#fff;border:none;padding:2px 12px;border-radius:4px;cursor:pointer;font-size:0.8em;margin-top:4px">过牌</button>';
      return;
    }
    const canPass = gameState && gameState.can_pass;
    content.innerHTML = '<span style="color:#e67e22">' +
      (canPass ? '手牌中没有任何能压过牌桌的牌型，建议过牌' : '你是首家，但AI未找到合适的出牌建议') +
      '</span>';
    return;
  }

  let html = '<table style="width:100%;font-size:0.82em;border-collapse:collapse">';
  html += '<tr style="color:#999"><th>牌型</th><th>牌面</th><th style="text-align:right">胜率</th><th></th></tr>';
  let bestRate = d.candidates[0]?.win_rate || 0;
  let rowIdx = 0;
  for (const c of d.candidates.slice(0, 8)) {
    const isBest = c.win_rate === bestRate;
    const bg = isBest ? 'background:rgba(39,174,96,0.15)' : '';
    const color = isBest ? 'color:#27ae60' : 'color:#ccc';
    const rate = (c.win_rate * 100).toFixed(1) + '%';
    const cardsStr = (c.cards || []).join(',');
    const isPass = c.type === 'PASS';
    if (isPass) {
      html += `<tr style="${bg};${color};border-bottom:1px solid rgba(255,255,255,0.04)">
        <td>过牌</td><td style="color:#e67e22">—</td><td style="text-align:right;font-weight:bold">${rate}</td>
        <td style="white-space:nowrap;padding-left:12px">
          <button data-action="pass" style="background:#e67e22;color:#fff;border:none;padding:1px 8px;border-radius:3px;cursor:pointer;font-size:0.75em">过牌</button>
        </td>
      </tr>`;
    } else {
      html += `<tr style="${bg};${color};border-bottom:1px solid rgba(255,255,255,0.04)">
        <td>${c.type_cn||c.type||'?'}</td><td>${(c.cards||[]).join(' ')}</td><td style="text-align:right;font-weight:bold">${rate}</td>
        <td style="white-space:nowrap;padding-left:12px">
          <button data-action="select" data-cards="${cardsStr}" style="background:#555;color:#ccc;border:none;padding:1px 6px;border-radius:3px;cursor:pointer;font-size:0.7em">选中</button>
          <button data-action="play" data-cards="${cardsStr}" style="background:#27ae60;color:#fff;border:none;padding:1px 8px;border-radius:3px;cursor:pointer;font-size:0.75em;margin-left:2px">出牌</button>
        </td>
      </tr>`;
    }
  }
  html += '</table>';
  content.innerHTML = html;

  // Attach event listeners (more reliable than inline onclick)
  content.querySelectorAll('button[data-action="pass"]').forEach(btn => {
    btn.addEventListener('click', () => passTurn());
  });
  content.querySelectorAll('button[data-action="select"]').forEach(btn => {
    btn.addEventListener('click', function() {
      selectSuggested(this.dataset.cards, this);
    });
  });
  content.querySelectorAll('button[data-action="play"]').forEach(btn => {
    btn.addEventListener('click', function() {
      playSuggested(this.dataset.cards);
    });
  });
}

let _lastSuggestBtns = [];

let _activeSuggestBtn = null;

function selectSuggested(displayStrs, btnEl) {
  if (_activeSuggestBtn && _activeSuggestBtn !== btnEl) {
    _activeSuggestBtn.textContent = '选中';
  }
  const displays = displayStrs.split(',');
  const hand = gameState.my_hand;
  const neededIds = [];
  const usedIds = new Set();
  for (const d of displays) {
    const card = hand.find(c => c.display === d.trim() && !usedIds.has(c.id));
    if (card) { neededIds.push(card.id); usedIds.add(card.id); }
  }
  const allSelected = neededIds.length > 0 && neededIds.every(id => selectedCards.has(id));
  if (allSelected) {
    neededIds.forEach(id => selectedCards.delete(id));
    if (btnEl) btnEl.textContent = '选中';
    _activeSuggestBtn = null;
  } else {
    selectedCards.clear();
    neededIds.forEach(id => selectedCards.add(id));
    if (btnEl) btnEl.textContent = '取消';
    _activeSuggestBtn = btnEl;
    activeStrip = null;
  }
  render();
}

async function playSuggested(displayStrs) {
  if (!myTurn) return;
  const displays = displayStrs.split(',');
  selectedCards.clear();
  const hand = gameState.my_hand;
  const usedIds = new Set();
  for (const d of displays) {
    const card = hand.find(c => c.display === d.trim() && !usedIds.has(c.id));
    if (card) { selectedCards.add(card.id); usedIds.add(card.id); }
  }
  if (selectedCards.size > 0) {
    await playCards();
  } else {
    // Couldn't match — just try playing whatever is selected
    if (selectedCards.size === 0) {
      // Reload state and retry once
      await fetchState();
      if (myTurn && gameState) {
        const h = gameState.my_hand;
        selectedCards.clear();
        for (const d of displays) {
          const card = h.find(c => c.display === d.trim() && !usedIds.has(c.id));
          if (card) { selectedCards.add(card.id); usedIds.add(card.id); }
        }
        if (selectedCards.size > 0) await playCards();
      }
    }
  }
}

function updatePlayButton() {
  document.getElementById('btn-play').disabled = !myTurn || selectedCards.size === 0;
}

// ==================================================================
// AI Evaluate (analyze currently selected cards)
// ==================================================================

async function aiEvaluate() {
  const panel = document.getElementById('suggest-panel');
  const content = document.getElementById('suggest-content');
  panel.style.display = 'block';

  const cardIds = selectedCards ? Array.from(selectedCards) : [];
  content.innerHTML = '<span style="color:#aaa">AI 分析中...</span>';

  const d = await gameApi.evaluate(cardIds);

  if (d.info) {
    content.innerHTML = `<span style="color:#daa520">${d.info}</span>`;
    return;
  }
  if (d.error) {
    content.innerHTML = `<span style="color:#e74c3c">${d.error}</span>`;
    return;
  }

  const rateStr = d.win_rate != null ? ` | 胜率: <b style="color:#27ae60">${(d.win_rate*100).toFixed(1)}%</b>` : '';
  const rDiff = d.rounds_before - d.rounds_after;
  content.innerHTML = `
    <div style="font-size:0.85em">
      <span style="color:#daa520">${d.combo_type}</span>
      <span> ${(d.cards||[]).join(' ')}</span>
      ${d.is_bomb ? '<span style="color:#e74c3c"> 炸弹</span>' : ''}
    </div>
    <div style="font-size:0.78em;color:#aaa;margin-top:4px">
      轮次: ${d.rounds_before} → ${d.rounds_after} (${rDiff>=0?'-'+rDiff:'+'+Math.abs(rDiff)}轮)${rateStr}
    </div>`;
}

// ==================================================================
// Page init — use embedded state for synchronous first render (no flash)
// ==================================================================

if (window.__INITIAL_STATE__) {
  gameState = window.__INITIAL_STATE__;
  render();
  if (debugMode) fetchDebug();
  if (llmThinkingOpen) fetchLLMReasoning();
  // Sync button states with default-on flags
  const wrBtn = document.getElementById('btn-winrate');
  if (wrBtn) { wrBtn.style.background = '#27ae60'; wrBtn.style.color = '#fff'; }
}

// ==================================================================
// AI Config Panel (schema-driven — add new models in Python only)
// ==================================================================

let configOpen = false;
let schema = {};
let currentConfig = {};
let configTab = 'global';

function switchConfigTab(tab) {
  configTab = tab;
  document.querySelectorAll('.cfg-tab').forEach(b => b.classList.remove('active'));
  const tabBtn = document.getElementById('cfg-tab-' + tab);
  if (tabBtn) tabBtn.classList.add('active');
  ['global','p0','p1','p2','p3'].forEach(t => {
    const el = document.getElementById('cfg-tabcontent-' + t);
    if (el) el.style.display = t === tab ? 'block' : 'none';
  });
}

async function toggleConfig() {
  configOpen = !configOpen;
  document.getElementById('config-panel').style.display = configOpen ? 'block' : 'none';
  if (configOpen) { await fetchConfig(); switchConfigTab('global'); }
}

async function fetchConfig() {
  const d = await gameApi.getConfig();
  schema = d.schema || {};
  currentConfig = d.config || {};
  buildConfigTab('global', null);
  for (const p of [0,1,2,3]) { buildConfigTab('p' + p, p); }
}

function cfgChanged() {
  const playerId = configTab === 'global' ? null : parseInt(configTab[1]);
  // Save current form values to currentConfig before rebuild
  saveToConfig(playerId);
  buildConfigTab(configTab, playerId);
}

function saveToConfig(playerId) {
  const prefix = playerId === null ? 'g' : ('p' + playerId);
  const deciderSel = document.getElementById('cfg-' + prefix + 'decider');
  if (!deciderSel) return;
  const deciderId = deciderSel.value;
  if (playerId === null) {
    if (!currentConfig.decider) currentConfig.decider = {default_model:'mc',model_params:{}};
    currentConfig.decider.default_model = deciderId;
  } else {
    if (!currentConfig.players[playerId]) currentConfig.players[playerId] = {decider:null,params:{}};
    currentConfig.players[playerId].decider = deciderId;
  }
  // Save all param values from DOM
  const deciderCat = (schema.decider || {}).models || {};
  const deciderDef = deciderCat[deciderId] || {};
  for (const [k, pdef] of Object.entries(deciderDef.params || {})) {
    const el = document.getElementById('cfg-' + prefix + 'd-' + k);
    if (!el) continue;
    const val = (pdef.type === 'select' || pdef.type === 'ref') ? el.value : parseFloat(el.value);
    if (playerId === null) {
      if (!currentConfig.decider.model_params[deciderId]) currentConfig.decider.model_params[deciderId] = {};
      currentConfig.decider.model_params[deciderId][k] = val;
    } else {
      currentConfig.players[playerId].params[k] = val;
    }
  }
}

function renderParam(k, pdef, prefix, curVal) {
  const label = pdef.label || k;
  let inputHtml;
  if (pdef.type === 'select') {
    const opts = pdef.options || {};
    inputHtml = `<select id="cfg-${prefix}d-${k}" onchange="cfgChanged()" style="background:#222;color:#ddd;border:1px solid #555;padding:4px 8px;border-radius:4px;font-size:0.95em;width:160px">
      ${Object.entries(opts).map(([v,l]) => `<option value="${v}" ${curVal==v?'selected':''}>${l}</option>`).join('')}</select>`;
  } else if (pdef.type === 'ref') {
    const refCat = (schema[pdef.ref_category] || {}).models || {};
    inputHtml = `<select id="cfg-${prefix}d-${k}" onchange="cfgChanged()" style="background:#222;color:#ddd;border:1px solid #555;padding:4px 8px;border-radius:4px;font-size:0.95em;width:180px">
      ${Object.entries(refCat).map(([mid,m]) => `<option value="${mid}" ${curVal==mid?'selected':''}>${m.name}</option>`).join('')}</select>`;
  } else {
    const step = pdef.step || 1;
    inputHtml = `<input type="number" value="${curVal}" step="${step}" id="cfg-${prefix}d-${k}" onchange="cfgChanged()" style="width:72px;background:#222;color:#ddd;border:1px solid #555;border-radius:4px;padding:5px 8px;font-size:0.95em">`;
  }
  return `<div style="display:flex;align-items:center;gap:10px;margin-bottom:5px">
    <span style="width:90px;text-align:right;font-size:0.95em;color:#aaa">${label}</span>
    ${inputHtml}
  </div>`;
}

function buildConfigTab(tabId, playerId) {
  const container = document.getElementById('cfg-tabcontent-' + tabId);
  if (!container) return;
  const isGlobal = playerId === null;
  const prefix = isGlobal ? 'g' : ('p' + playerId);

  // Determine current decider
  const deciderCat = (schema.decider || {}).models || {};
  let deciderId = isGlobal
    ? ((currentConfig.decider || {}).default_model || 'mc')
    : ((currentConfig.players[playerId] || {}).decider || (currentConfig.decider || {}).default_model || 'mc');

  const dOpts = Object.entries(deciderCat).map(([mid, m]) =>
    `<option value="${mid}" ${deciderId===mid?'selected':''}>${m.name}</option>`).join('');

  const deciderDef = deciderCat[deciderId] || {};
  const curParams = isGlobal
    ? ((currentConfig.decider || {}).model_params || {})[deciderId] || {}
    : (currentConfig.players[playerId] || {}).params || {};

  let dHtml = '';
  for (const [k, pdef] of Object.entries(deciderDef.params || {})) {
    const curVal = curParams[k] ?? pdef.default;
    dHtml += renderParam(k, pdef, prefix, curVal);
  }

  // Also show inner model params if this decider has a "ref" to inner_model
  const innerRef = Object.entries(deciderDef.params || {}).find(([k,p]) => p.ref_category === 'inner_model');
  let iHtml = '';
  if (innerRef) {
    const innerId = curParams[innerRef[0]] || innerRef[1].default || 'informed';
    const innerCat = (schema.inner_model || {}).models || {};
    const innerDef = innerCat[innerId] || {};
    iHtml = '<div style="color:#888;font-size:0.9em;max-height:260px;overflow-y:auto;margin-top:6px;border-top:1px solid #333;padding-top:6px">内层模型参数 (' + (innerDef.name||innerId) + '):';
    for (const [k, pdef] of Object.entries(innerDef.params || {})) {
      const curVal = curParams[k] ?? pdef.default;
      iHtml += renderParam(k, pdef, prefix, curVal);
    }
    iHtml += '</div>';
  }

  // Player enable checkbox
  const isEnabled = isGlobal ? true : !!(currentConfig.players[playerId] || {}).decider;
  const enabledCb = !isGlobal ? `
    <label style="color:#aaa;font-size:0.95em;margin-bottom:8px;cursor:pointer;display:block">
      <input type="checkbox" id="cfg-penabled-${playerId}" ${isEnabled?'checked':''} onchange="onPlayerEnable(${playerId})"> 启用独立配置
    </label>` : '';

  container.innerHTML = `
    ${enabledCb}
    <div style="margin-bottom:10px">
      <span style="color:#daa520;margin-right:8px;font-size:1em">决策器</span>
      <select id="cfg-${prefix}decider" onchange="cfgChanged()" style="background:#222;color:#ddd;border:1px solid #555;padding:5px 14px;border-radius:4px;font-size:1em">${dOpts}</select>
      <span style="color:#888;font-size:0.85em;margin-left:8px">${deciderDef.description||''}</span>
    </div>
    ${dHtml ? '<div style="color:#aaa;font-size:0.95em">'+dHtml+'</div>' : ''}
    ${iHtml}`;
}

function onPlayerEnable(playerId) {
  const cb = document.getElementById('cfg-penabled-' + playerId);
  if (!cb.checked) currentConfig.players[playerId] = {decider: null, params: {}};
  buildConfigTab('p' + playerId, playerId);
}

async function applyConfig() {
  saveToConfig(null);  // save global
  for (const p of [0,1,2,3]) saveToConfig(p);  // save each player

  // Clean null players
  for (const p of [0,1,2,3]) {
    const cb = document.getElementById('cfg-penabled-' + p);
    if (!cb || !cb.checked) currentConfig.players[p] = {decider: null, params: {}};
  }

  await gameApi.saveConfig(currentConfig);
  const msg = document.getElementById('message'); msg.textContent = 'AI设置已保存'; msg.style.color = '#27ae60';
  setTimeout(() => { msg.textContent = ''; msg.style.color = ''; }, 1500);
  await fetchConfig();
  // Immediately refresh game state so decider badges update
  fetchState();
}
