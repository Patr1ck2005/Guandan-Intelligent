/**
 * Guandan Arena Page — Verification Script
 *
 * Usage:  node tests/ui/verify-arena.js
 * Requires: npm install playwright && npx playwright install chromium
 */

const { chromium } = require('playwright');

const BASE = process.env.GUANDAN_BASE || 'http://localhost:8766';
const PASS = '\x1b[32m✓\x1b[0m';
const FAIL = '\x1b[31m✗\x1b[0m';

let errors = [], total = 0, passed = 0;

function check(label, ok, detail) {
  total++;
  if (ok) { passed++; console.log(`  ${PASS} ${label}`); }
  else     { console.log(`  ${FAIL} ${label}${detail ? ' — ' + detail : ''}`); }
}

(async () => {
  console.log('Arena Page Verification\n');

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('pageerror', err => errors.push(err.message));

  // ── 1. Page load ──────────────────────────────────────────────
  console.log('1. Page Load');
  try {
    await page.goto(BASE + '/arena', { waitUntil: 'networkidle', timeout: 10000 });
    check('HTTP 200', true);
  } catch (e) {
    check('HTTP 200', false, e.message);
    console.log('\nBLOCKED — server not reachable.');
    await browser.close();
    process.exit(1);
  }
  check('Title', (await page.title()).includes('测试场'));

  // ── 2. Tabs ───────────────────────────────────────────────────
  console.log('\n2. Tabs');
  const tabs = await page.evaluate(() =>
    Array.from(document.querySelectorAll('.atab')).map(b => ({
      text: b.textContent, visible: b.offsetParent !== null
    }))
  );
  check('3 tabs visible', tabs.length === 3 && tabs.every(t => t.visible));
  check('Tab 1: 自定义', tabs[0]?.text === '自定义');
  check('Tab 2: 确定推演', tabs[1]?.text === '确定推演');
  check('Tab 3: 真实牌桌', tabs[2]?.text === '真实牌桌');

  // ── 3. Custom tab — card pool ─────────────────────────────────
  console.log('\n3. Custom Tab — Card Pool');
  const pool = await page.evaluate(() => {
    const el = document.getElementById('card-pool');
    return { exists: !!el, cards: el ? el.querySelectorAll('.pool-card').length : 0 };
  });
  check('Card pool exists', pool.exists);
  check('Cards in pool', pool.cards > 0, pool.cards + ' cards');

  // Custom tab buttons
  const customBtns = await page.evaluate(() => {
    const ids = ['btn-ac'];
    return ids.map(id => {
      const el = document.getElementById(id);
      return { id, visible: el && el.offsetParent !== null, text: el?.textContent };
    });
  });
  for (const b of customBtns)
    check(`Button ${b.id}`, b.visible, b.text);

  // ── 4. Custom tab — random deal ───────────────────────────────
  console.log('\n4. Custom Tab — Random Deal');
  await page.evaluate(() => { window.customRandomDeal(); });
  await page.waitForTimeout(500);
  const afterDeal = await page.evaluate(() =>
    [0,1,2,3].map(p => document.getElementById('czcnt-'+p)?.textContent)
  );
  check('4 players dealt', afterDeal.every(c => c === '27张'), afterDeal.join(', '));

  // ── 5. Custom tab — clear ─────────────────────────────────────
  console.log('\n5. Custom Tab — Clear');
  await page.evaluate(() => { window.customClear(); });
  await page.waitForTimeout(300);
  const afterClear = await page.evaluate(() =>
    [0,1,2,3].map(p => document.getElementById('czcnt-'+p)?.textContent)
  );
  check('All hands cleared', afterClear.every(c => c === '0张'), afterClear.join(', '));

  // ── 6. Switch to Perfect tab ──────────────────────────────────
  console.log('\n6. Perfect Tab');
  await page.evaluate(() => { window.switchTab('perfect'); });
  await page.waitForTimeout(500);
  const perfectVisible = await page.evaluate(() => {
    const el = document.getElementById('row-perfect');
    return el && window.getComputedStyle(el).display === 'flex';
  });
  check('Perfect tab visible', perfectVisible);

  const catOptions = await page.evaluate(() =>
    Array.from(document.getElementById('pcat')?.options || []).map(o => o.value)
  );
  check('Categories loaded', catOptions.length > 0, catOptions.join(', '));

  // Load scenarios
  if (catOptions[0]) {
    await page.selectOption('#pcat', catOptions[0]);
    await page.waitForTimeout(500);
    const scCount = await page.evaluate(() =>
      document.querySelectorAll('#sc-list .sc-item').length
    );
    check('Scenarios loaded', scCount > 0, scCount + ' scenarios');

    // Click first scenario
    const selAfter = await page.evaluate(() => {
      const first = document.querySelector('#sc-list .sc-item');
      if (first) { first.click(); return first.classList.contains('sel'); }
      return false;
    });
    check('Scenario selectable', selAfter);
  }

  // ── 7. Switch to Table tab ────────────────────────────────────
  console.log('\n7. Table Tab');
  await page.evaluate(() => { window.switchTab('table'); });
  await page.waitForTimeout(500);
  const tableVisible = await page.evaluate(() => {
    const el = document.getElementById('row-table');
    return el && window.getComputedStyle(el).display === 'flex';
  });
  check('Table tab visible', tableVisible);

  const tcat = await page.evaluate(() =>
    Array.from(document.getElementById('tcat')?.options || []).map(o => o.value)
  );
  check('Table categories', tcat.length > 0, tcat.join(', '));

  // ── 8. Switch back to Custom ──────────────────────────────────
  console.log('\n8. Switch Back');
  await page.evaluate(() => { window.switchTab('custom'); });
  await page.waitForTimeout(300);
  const customBack = await page.evaluate(() => {
    const el = document.getElementById('row-custom');
    return el && window.getComputedStyle(el).display === 'flex';
  });
  check('Custom tab visible', customBack);

  // ── 9. Global functions ───────────────────────────────────────
  console.log('\n9. Key Global Functions');
  const fns = await page.evaluate(() => ({
    switchTab: typeof window.switchTab,
    customRandomDeal: typeof window.customRandomDeal,
    customClear: typeof window.customClear,
    customStart: typeof window.customStart,
    customFocus: typeof window.customFocus,
    loadScP: typeof window.loadScP,
    loadScT: typeof window.loadScT,
    runP: typeof window.runP,
    runT: typeof window.runT,
    benchT: typeof window.benchT,
    poolClick: typeof window.poolClick,
    setLevel: typeof window.setLevel,
    undoAction: typeof window.undoAction,
    customClearTrick: typeof window.customClearTrick,
    closeModelPanel: typeof window.closeModelPanel,
    ac: typeof window.ac,
    scs: typeof window.scs,
  }));
  for (const [name, type] of Object.entries(fns))
    check(`window.${name}`, type === 'function', type);

  // ── 10. Console errors ────────────────────────────────────────
  console.log('\n10. Console Errors');
  if (errors.length === 0) check('No console errors', true);
  else errors.forEach(e => check('Error', false, e.substring(0, 100)));

  // ── Summary ──────────────────────────────────────────────────
  console.log(`\n${'═'.repeat(40)}`);
  console.log(`Passed: ${passed}/${total}`);
  console.log(`VERDICT: ${passed === total ? 'ALL CHECKS PASSED' : (total-passed) + ' FAILED'}`);
  console.log(`${'═'.repeat(40)}`);

  await browser.close();
  process.exit(passed === total ? 0 : 1);
})();
