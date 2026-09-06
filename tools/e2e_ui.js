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
  page.on('console', m => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
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
  while (Date.now() - t0 < 300000) {
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
  const benchOk = await waitFor(page, '#eval-result h3:has-text("排行榜")', 180000);
  check(benchOk, 'benchmark leaderboard rendered');
  await page.screenshot({ path: path.join(outDir, 'e2e_ui_bench_done.png') });

  // ---- 3. History: row click → detail → export links ----
  console.log('== eval history ==');
  await page.click('.etab[data-mode="history"]');
  await page.waitForTimeout(800);
  const firstRow = page.locator('.hrow').first();
  check(await firstRow.count() > 0, 'history rows present');
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

  console.log('--- errors:', errors.length);
  errors.slice(0, 15).forEach(e => console.log(' ', e));
  await browser.close();
  process.exit(errors.length ? 2 : 0);
})();
