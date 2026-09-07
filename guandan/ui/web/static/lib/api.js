/* ================================================================
   Guandan Web UI — Game-page API client
   Loaded as a regular <script> by game.html only.
   arena.js / eval.js are self-contained and call fetch() directly:
   arena's analysis calls need a caller-owned AbortController signal
   (start/stop buttons) and multi-minute time budgets, which don't fit
   this wrapper's default timeout — unifying them was evaluated and
   rejected in the round-4 architecture audit.
   ================================================================ */

var _API_TIMEOUT = 15000;

function _apiRequest(path, opts) {
  opts = opts || {};
  var method = opts.method || 'GET';
  var body = opts.body;
  var timeout = opts.timeout || _API_TIMEOUT;

  var controller = new AbortController();
  var timeoutId = setTimeout(function() { controller.abort(); }, timeout);
  if (opts.signal) opts.signal.addEventListener('abort', function() { controller.abort(); });

  var fetchOpts = { method: method, signal: controller.signal };
  if (body) {
    fetchOpts.headers = { 'Content-Type': 'application/json' };
    fetchOpts.body = JSON.stringify(body);
  }
  return fetch('/' + path.replace(/^\//, ''), fetchOpts).then(function(res) {
    clearTimeout(timeoutId);
    if (!res.ok) {
      var err = new Error('API ' + res.status + ': ' + path);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }).catch(function(e) {
    clearTimeout(timeoutId);
    if (e.name === 'AbortError') {
      var err2 = new Error('请求超时: ' + path);
      err2.isTimeout = true;
      throw err2;
    }
    throw e;
  });
}

window.gameApi = {
  // getState drives the poll loop: an AI step may legitimately take up to
  // agent_timeout_ms (75s default), so the 15s default timeout used to
  // abort mid-step and burn all 5 poll retries on a perfectly healthy
  // server ("服务器无响应" false alarm). Same for suggest/evaluate, which
  // run a full MC/LLM analysis server-side.
  getState:      function()     { return _apiRequest('/api/state', { timeout: 90000 }); },
  playCards:     function(ids)  { return _apiRequest('/api/play', { method: 'POST', body: { card_ids: ids } }); },
  passTurn:      function()     { return _apiRequest('/api/pass', { method: 'POST' }); },
  startGame:     function()     { return _apiRequest('/api/start_game', { method: 'POST' }); },
  newGame:       function()     { return _apiRequest('/api/new_game', { method: 'POST' }); },
  newRound:      function()     { return _apiRequest('/api/new_round', { method: 'POST' }); },
  tributeReturn: function(ids)  { return _apiRequest('/api/tribute_return', { method: 'POST', body: { card_ids: ids } }); },
  getHint:       function()     { return _apiRequest('/api/hint'); },
  getDebug:      function()     { return _apiRequest('/api/debug'); },
  getAILog:      function()     { return _apiRequest('/api/ai_log'); },
  clearAILog:    function()     { return _apiRequest('/api/ai_log/clear', { method: 'POST' }); },
  getConfig:     function()     { return _apiRequest('/api/config'); },
  saveConfig:    function(cfg)  { return _apiRequest('/api/config', { method: 'POST', body: cfg }); },
  getSuggest:    function()     { return _apiRequest('/api/suggest', { timeout: 60000 }); },
  evaluate:      function(ids)  { return _apiRequest('/api/evaluate', { method: 'POST', body: { card_ids: ids }, timeout: 60000 }); },
  toggleAutoPlay:function(en)   { return _apiRequest('/api/auto_play', { method: 'POST', body: { enabled: en } }); },
};
