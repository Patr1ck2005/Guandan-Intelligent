/* Visual audit: load all pages, capture console errors, screenshot. */
const { chromium } = require('playwright');
const path = require('path');

(async () => {
  const base = process.env.GUANDAN_BASE || 'http://localhost:8766';
  const outDir = path.join(__dirname, '..', '_screenshots');
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(`[console] ${page.url()} :: ${m.text()}`); });
  page.on('pageerror', e => errors.push(`[pageerror] ${page.url()} :: ${e.message}`));
  page.on('requestfailed', r => errors.push(`[reqfail] ${page.url()} :: ${r.url()} :: ${r.failure()?.errorText}`));

  const shots = [
    ['/', 'menu'],
    ['/game', 'game'],
    ['/arena', 'arena'],
    ['/eval', 'eval_bench'],
  ];
  for (const [url, name] of shots) {
    await page.goto(base + url, { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(outDir, `audit_${name}.png`) });
    console.log(`shot ${name}`);
  }

  // Eval Lab: click through every tab and shoot
  await page.goto(base + '/eval', { waitUntil: 'networkidle' });
  for (const mode of ['match', 'roundrobin', 'generate', 'tuner', 'history']) {
    const tab = page.locator(`.etab[data-mode="${mode}"]`);
    if (await tab.count()) {
      await tab.click();
      await page.waitForTimeout(700);
      await page.screenshot({ path: path.join(outDir, `audit_eval_${mode}.png`) });
      console.log(`shot eval_${mode}`);
    } else {
      errors.push(`[tab] missing tab ${mode}`);
    }
  }

  // Game page: interactive smoke — start a game, check board renders
  await page.goto(base + '/game', { waitUntil: 'networkidle' });
  await page.waitForTimeout(400);
  const hasBoard = await page.locator('#board, #game-container, #hand-cards, #player-hand').count();
  console.log(`game board candidates: ${hasBoard}`);

  console.log('--- console/page errors:', errors.length);
  errors.slice(0, 20).forEach(e => console.log(e));
  await browser.close();
  process.exit(errors.length ? 2 : 0);
})();
