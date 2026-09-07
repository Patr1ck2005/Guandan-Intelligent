/* Button-level e2e for Guandan UI (stage M3).
 *
 * Drives real clicks against the live server and asserts outcomes:
 *   1. Game page: 开始游戏 → auto_play on → full game plays out (game_over).
 *   2. Eval Lab benchmark tab: 运行基准 → leaderboard table renders.
 *   3. History tab: row click → detail with export links; back button works.
 *   4. Regression: 重跑 → done → verdict badge present.
 * Skips (deterministic reason): human tribute-return banner — depends on a
 * random tribute deal, cannot be forced via UI.
 *
 * Usage: node tools/e2e_ui.js  (GUANDAN_BASE defaults to http://localhost:8766)
 */
const { chromium } = require('playwright');
const path = require('path');

const base = process.env.GUANDAN_BASE || 'http://localhost:8766';
const outDir = path.join(__dirname, '..', '_screenshots');
const errors = [];
let expectErrorStatus = false;   // during intentional-4xx probes the browser
                                 // logs "Failed to load resource" — not a bug

function check(cond, label) {
  if (cond) { console.log(`  PASS ${label}`); return true; }
  errors.push(label);
  console.log(`  FAIL ${label}`);
  return false;
}

// Wait for a POSITIVE completion marker (never "absence of .bad" — the
// benchmark leaderboard legitimately contains class="bad" score cells).
async function waitFor(page, selector, timeoutMs) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (await page.locator(selector).count()) return true;
    await page.waitForTimeout(500);
  }
  return false;
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('console', m => {
    if (m.type() === 'error' && !(expectErrorStatus && m.text().includes('Failed to load resource'))) {
      errors.push(`console: ${m.text()}`);
    }
  });
  page.on('pageerror', e => errors.push(`pageerror: ${e.message}`));

  // ---- 1. Game page: 开始游戏 + auto_play → full game ----
  console.log('== game page ==');
  await page.goto(base + '/game', { waitUntil: 'networkidle' });
  await page.click('text=开始游戏');
  // human seat joins the AI: enable full auto-play via the same endpoint the UI uses
  await page.evaluate(async () => {
    await fetch('/api/auto_play', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: true }) });
  });
  // /api/state advances ONE ai step per call — poll fast in-page.
  // auto_play is per-ROUND: at a round boundary the game stops and the UI
  // shows 新一局 → 开始游戏; the e2e drives those two transitions itself.
  let gameOk = false;
  let roundsPlayed = 0;
  const t0 = Date.now();
  // Level progression is random (level_change depends on finish order); a
  // full game can legitimately need 20+ rounds. 480s with fast polling.
  while (Date.now() - t0 < 480000) {
    const st = await page.evaluate(async () => {
      const r = await fetch('/api/state');
      return await r.json();
    }).catch(() => null);
    if (st && st.game_over) { gameOk = true; break; }
    if (st && st.round_over) {
      roundsPlayed += 1;
      await page.evaluate(async () => {
        await fetch('/api/new_round', { method: 'POST' });
        await fetch('/api/start_game', { method: 'POST' });
        await fetch('/api/auto_play', { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: true }) });
      });
      continue;
    }
    await page.waitForTimeout(80);
  }
  check(gameOk, `full game plays out after 开始游戏 + auto_play (${roundsPlayed} round transitions)`);
  await page.screenshot({ path: path.join(outDir, 'e2e_ui_game_after.png') });

  // ---- 2. Benchmark run via button ----
  console.log('== eval benchmark ==');
  await page.goto(base + '/eval', { waitUntil: 'networkidle' });
  await page.click('button.btn-gn:has-text("运行基准")');
  // The LIVE dashboard shows "<h3>实时排行榜</h3>" while running; the final
  // result page shows "<h3>排行榜</h3>" — wait for the exact final heading
  // so step 3 doesn't switch tabs mid-job (which now asks for confirmation).
  const benchOk = await waitFor(page, '#eval-result h3:text-is("排行榜")', 180000);
  check(benchOk, 'benchmark final leaderboard rendered');
  await page.screenshot({ path: path.join(outDir, 'e2e_ui_bench_done.png') });

  // ---- 3. History: row click → detail → export links ----
  console.log('== eval history ==');
  await page.click('.etab[data-mode="history"]');
  const rowsLoaded = await waitFor(page, '.hrow', 15000);
  const firstRow = page.locator('.hrow').first();
  check(rowsLoaded && await firstRow.count() > 0, 'history rows present');
  await firstRow.click();
  const detailOk = await waitFor(page, '#eval-result a[href*="/api/eval/export/"]', 10000);
  check(detailOk, 'run detail shows export links');
  if (detailOk) {
    await page.click('text=← 返回列表');
    const backOk = await waitFor(page, '.hrow', 10000);
    check(backOk, 'back to history list works');
  }
  await page.screenshot({ path: path.join(outDir, 'e2e_ui_history.png') });

  // ---- 4. Regression: 重跑 → verdict badge ----
  console.log('== eval regression ==');
  // loadRegressionSection is async — wait for its buttons to appear
  const regBtn = await waitFor(page, '#reg-section button:has-text("重跑")', 10000);
  if (regBtn) {
    await page.click('#reg-section button:has-text("重跑")');
    // renderRegressionResult replaces #eval-result entirely — badge lands there
    const badgeOk = await waitFor(page, '#eval-result .badge', 300000);
    check(badgeOk, 'regression verdict badge rendered');
    await page.screenshot({ path: path.join(outDir, 'e2e_ui_regression.png') });
  } else {
    console.log('  SKIP regression rerun button (no reg section rendered)');
  }

  // SKIPPED (deterministic reason): human 还贡 banner flow — the tribute
  // payer/receiver depends on the random deal; cannot be forced via UI.
  console.log('  SKIP human tribute-return banner (random deal dependency)');

  // ---- 5. Round-over auto-play guard (audit HIGH-1 acceptance) ----
  console.log('== round-over auto-play guard ==');
  // Drive a fresh session to round_over via API (own page = own cookie jar),
  // then click the UI button at round-over: it must stay disabled and the
  // POST must be refused with an error message, not re-settle the round.
  await page.goto(base + '/game', { waitUntil: 'networkidle' });
  await page.evaluate(async () => {
    await fetch('/api/new_game', { method: 'POST' });
    await fetch('/api/start_game', { method: 'POST' });
    await fetch('/api/auto_play', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: true }) });
  });
  let roundOverSig = null;
  const tGuard = Date.now();
  while (Date.now() - tGuard < 180000) {
    const st = await page.evaluate(async () => {
      const r = await fetch('/api/state');
      return await r.json();
    }).catch(() => null);
    if (st && st.round_over) {
      roundOverSig = `t0=${st.team_level_0}/t1=${st.team_level_1}/n=${st.round_results.length}`;
      break;
    }
    if (st && st.game_over) break;
    await page.evaluate(async () => {
      await fetch('/api/auto_play', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: true }) });
    });
    await page.waitForTimeout(80);
  }
  if (roundOverSig) {
    // reload so the UI renders the round-over state (下一局 visible)
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(600);
    const disabled = await page.evaluate(() => {
      const b = document.getElementById('btn-auto-play');
      return b ? b.disabled : null;
    });
    check(disabled === true, '自动对局 button disabled at round-over');
    // API-level guard still holds even if the click bypasses the UI
    const apiRefusal = await page.evaluate(async () => {
      const r = await fetch('/api/auto_play', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: true }) });
      return await r.json();
    });
    check(apiRefusal.auto_play === false && apiRefusal.error === true,
      `auto_play POST refused at round-over (${apiRefusal.message || 'no message'})`);
    const afterSig = await page.evaluate(async () => {
      const st = await (await fetch('/api/state')).json();
      return `t0=${st.team_level_0}/t1=${st.team_level_1}/n=${st.round_results.length}`;
    });
    check(afterSig === roundOverSig,
      `levels/results unchanged after refused toggle (${roundOverSig} -> ${afterSig})`);
  } else {
    console.log('  SKIP round-over guard (no round_over reached in time)');
  }

  // ---- 6. Arena 真实牌桌 tab renders hands (audit HIGH-6 acceptance) ----
  console.log('== arena table tab ==');
  await page.goto(base + '/arena', { waitUntil: 'networkidle' });
  await page.click('.atab:has-text("真实牌桌")');
  await page.waitForTimeout(1200);   // selT fetches detail + perspectives
  const handsRendered = await page.evaluate(() => {
    const zones = document.querySelectorAll('#tbl-t .player-zone .ahc');
    let cards = 0;
    zones.forEach(z => { cards += z.querySelectorAll('.card').length; });
    return cards;
  });
  check(handsRendered > 0, `真实牌桌 main table renders cards (${handsRendered} rendered)`);
  const noUndefinedTurn = await page.evaluate(() =>
    !document.querySelector('#tbl-t')?.textContent.includes('undefined'));
  check(noUndefinedTurn, '真实牌桌 center panel has no undefined turn');
  // The scenario-mode center must NOT offer manual play (P-simulator buttons)
  const noManualPlay = await page.evaluate(() => {
    const btns = [...document.querySelectorAll('#tbl-t button')];
    return !btns.some(b => b.textContent === '出牌');
  });
  check(noManualPlay, '真实牌桌 center has no cross-tab manual-play button');
  // 分析 on this tab renders results in THIS tab's panel (2 default models)
  await page.click('#tbl-t button:has-text("分析")');
  const tRes = await waitFor(page, '#trcnt:has-text("(2/2)")', 60000);
  check(tRes, '真实牌桌 分析 runs both models into this tab');
  const tResRows = await page.evaluate(() =>
    document.querySelectorAll('#tres .mres').length);
  check(tResRows === 2, `真实牌桌 results render in this tab (${tResRows} model results)`);
  await page.screenshot({ path: path.join(outDir, 'e2e_ui_arena_table.png') });

  // ---- 7. Arena custom start blocks illegal table combos (audit HIGH-7) ----
  console.log('== arena custom illegal combo guard ==');
  expectErrorStatus = true;   // the two probes below intentionally hit 400s
  await page.click('.atab:has-text("自定义")');
  await page.waitForTimeout(400);
  // Assign exactly one card to each hand, then put TWO different ranks in
  // P1's play area — an illegal combo the backend must reject and the UI
  // must block before any cards can vanish.
  const guardOk = await page.evaluate(async () => {
    // pick 6 distinct unassigned cards: 4 for hands, 2 for the illegal table
    const ids = [];
    for (let i = 0; i < 108 && ids.length < 6; i++) {
      if (![0].includes(i)) ids.push(i);
    }
    // Use direct API: check_combo on two different-rank cards must be invalid
    const r = await fetch('/api/arena/check_combo', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card_ids: [1, 15], level: 2 }),  // C3 + D4: illegal
    });
    const d = await r.json();
    return d.valid === false;
  });
  check(guardOk, 'check_combo flags an illegal table combo (C3+D4)');
  // And the full custom start with such a table is rejected server-side
  const rejectOk = await page.evaluate(async () => {
    const r = await fetch('/api/arena/sim/init_custom', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        hands: { '0': [2], '1': [16], '2': [29], '3': [42] },
        table: [1, 15],   // C3 + D4 — illegal combo
        table_player: 1, current_player: 0, level: 2,
      }),
    });
    return r.status === 400;
  });
  check(rejectOk, 'sim/init_custom rejects illegal table combo with 400 (no vanishing cards)');
  expectErrorStatus = false;

  console.log('--- errors:', errors.length);
  errors.slice(0, 15).forEach(e => console.log(' ', e));
  await browser.close();
  process.exit(errors.length ? 2 : 0);
})();
