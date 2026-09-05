/* ================================================================
   Guandan Web UI — Centralized API Client
   Loaded as regular <script> before game.js and arena.js
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
  getState:      function()     { return _apiRequest('/api/state'); },
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
  getSuggest:    function()     { return _apiRequest('/api/suggest'); },
  evaluate:      function(ids)  { return _apiRequest('/api/evaluate', { method: 'POST', body: { card_ids: ids } }); },
  toggleAutoPlay:function(en)   { return _apiRequest('/api/auto_play', { method: 'POST', body: { enabled: en } }); },
};

window.arenaApi = {
  getModels:       function()       { return _apiRequest('/api/arena/models'); },
  getScenarios:    function(cat)    { return _apiRequest('/api/arena/scenarios' + (cat ? '?category=' + cat : '')); },
  getScenario:     function(id)     { return _apiRequest('/api/arena/scenarios/' + id); },
  getPerspectives: function(id)     { return _apiRequest('/api/arena/scenarios/' + id + '/perspectives'); },
  analyzeScenario: function(data)   { return _apiRequest('/api/arena/analyze/scenario', { method: 'POST', body: data, timeout: 60000 }); },
  benchmark:       function(data)   { return _apiRequest('/api/arena/benchmark', { method: 'POST', body: data, timeout: 120000 }); },
  simInit:         function(data)   { return _apiRequest('/api/arena/sim/init', { method: 'POST', body: data }); },
  simInitCustom:   function(data)   { return _apiRequest('/api/arena/sim/init_custom', { method: 'POST', body: data }); },
  simState:        function(simId)  { return _apiRequest('/api/arena/sim/state', { method: 'POST', body: { sim_id: simId } }); },
  simStep:         function(simId, d){ return _apiRequest('/api/arena/sim/step', { method: 'POST', body: Object.assign({ sim_id: simId }, d) }); },
  simAnalyze:      function(simId, m){ return _apiRequest('/api/arena/sim/analyze', { method: 'POST', body: { sim_id: simId, models: m }, timeout: 60000 }); },
  checkCombo:      function(ids, lv) { return _apiRequest('/api/arena/check_combo', { method: 'POST', body: { card_ids: ids, level: lv } }); },
};
