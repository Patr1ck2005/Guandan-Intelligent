/**
 * Guandan Web UI — Verification Script
 *
 * Uses Playwright to drive a headless Chromium browser and verify the
 * game page works end-to-end.  Run after any change to game.html,
 * game.js, or related files.
 *
 * Usage:  node tests/ui/verify-game.js
 *
 * Requires: npm install playwright
 *           npx playwright install chromium
 */

const { chromium } = require('playwright');

const BASE = process.env.GUANDAN_BASE || 'http://localhost:8766';
const PASS = '\x1b[32m✓\x1b[0m';
const FAIL = '\x1b[31m✗\x1b[0m';
const WARN = '\x1b[33m⚠\x1b[0m';

let errors = [];
let total = 0;
let passed = 0;

function check(label, ok, detail) {
  total++;
  if (ok) { passed++; console.log(`  ${PASS} ${label}`); }
  else     { console.log(`  ${FAIL} ${label}${detail ? ' — ' + detail : ''}`); }
}

(async () => {
  console.log('Guandan Game Page Verification\n');

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('pageerror', err => errors.push(err.message));

  // ── 1. Page load ──────────────────────────────────────────────
  console.log('1. Page Load');
  try {
    await page.goto(BASE + '/game', { waitUntil: 'networkidle', timeout: 10000 });
    check('HTTP 200', true);
  } catch (e) {
    check('HTTP 200', false, e.message);
    console.log('\nBLOCKED — server not reachable. Start with: python -m guandan.ui.web.app');
    await browser.close();
    process.exit(1);
  }

  const pageTitle = await page.title();
  check('Title contains 掼蛋', pageTitle.includes('掼蛋'), pageTitle);

  // ── 2. Window functions ───────────────────────────────────────
  console.log('\n2. Window Function Registration');
  const fns = await page.evaluate(() => ({
    startGame: typeof window.startGame,
    playCards: typeof window.playCards,
    passTurn:  typeof window.passTurn,
    showHint:  typeof window.showHint,
    toggleConfig: typeof window.toggleConfig,
    toggleDebug: typeof window.toggleDebug,
    toggleAIDebug: typeof window.toggleAIDebug,
    toggleLayout: typeof window.toggleLayout,
    newGame:   typeof window.newGame,
    newRound:  typeof window.newRound,
    aiSuggest: typeof window.aiSuggest,
    aiEvaluate: typeof window.aiEvaluate,
    toggleAutoPlay: typeof window.toggleAutoPlay,
    clearAILog: typeof window.clearAILog,
    organizeCombo: typeof window.organizeCombo,
  }));
  for (const [name, type] of Object.entries(fns)) {
    check(`window.${name}`, type === 'function', type);
  }

  // ── 3. Alpine store ───────────────────────────────────────────
  console.log('\n3. Alpine Store');
  const store = await page.evaluate(() => ({
    exists: typeof window._gameState === 'object',
    configOpen: window._gameState?.configOpen,
    suggestOpen: window._gameState?.suggestOpen,
    debugMode: window._gameState?.debugMode,
    started: window._gameState?.started,
    hasState: !!window._gameState?.state,
  }));
  check('_gameState exists', store.exists);
  check('configOpen = false', store.configOpen === false, store.configOpen);
  check('suggestOpen = false', store.suggestOpen === false, store.suggestOpen);
  check('debugMode = true', store.debugMode === true, store.debugMode);
  check('started = false', store.started === false || store.started === undefined);

  // ── 4. Pre-game UI state ──────────────────────────────────────
  console.log('\n4. Pre-game UI State');
  const preUI = await page.evaluate(() => {
    const startOverlay = document.getElementById('start-overlay');
    const controls = document.getElementById('controls');
    const configPanel = document.getElementById('config-panel');
    const suggestPanel = document.getElementById('suggest-panel');
    const aiDebug = document.getElementById('ai-debug-panel');
    const myHand = document.getElementById('my-hand');
    const startBtn = document.getElementById('btn-start-game');
    const visible = (el) => el && window.getComputedStyle(el).display !== 'none';
    return {
      startOverlay: visible(startOverlay),
      controls: visible(controls),
      configPanel: visible(configPanel),
      suggestPanel: visible(suggestPanel),
      aiDebug: visible(aiDebug),
      myHand: myHand ? myHand.querySelectorAll('.card').length : -1,
      startBtn: startBtn ? startBtn.textContent : 'MISSING',
    };
  });
  check('Start overlay visible', preUI.startOverlay);
  check('Controls hidden', !preUI.controls);
  check('Config panel hidden', !preUI.configPanel);
  check('Suggest panel hidden', !preUI.suggestPanel);
  check('AI debug panel hidden', !preUI.aiDebug);
  check('Cards rendered in hand', preUI.myHand > 0, preUI.myHand + ' cards');
  check('Start game button present', preUI.startBtn === '开始游戏', preUI.startBtn);

  // ── 5. Header buttons visible ─────────────────────────────────
  console.log('\n5. Header Buttons');
  const headerBtns = await page.evaluate(() => {
    return ['btn-winrate', 'btn-debug', 'btn-ai-debug', 'btn-config'].map(id => {
      const el = document.getElementById(id);
      return { id, visible: el && el.offsetParent !== null };
    });
  });
  for (const b of headerBtns) {
    check(`${b.id} visible`, b.visible);
  }

  // ── 6. Start game ─────────────────────────────────────────────
  console.log('\n6. Start Game Flow');
  try {
    await page.click('#btn-start-game');
    await page.waitForTimeout(1500);
  } catch (e) {
    check('Click start game', false, e.message);
  }

  const postStart = await page.evaluate(() => {
    const controls = document.getElementById('controls');
    return {
      gameStarted: window._gameState?.state?.game_started,
      controlsVisible: controls && window.getComputedStyle(controls).display !== 'none',
      overlayHidden: (() => {
        const el = document.getElementById('start-overlay');
        return el && window.getComputedStyle(el).display === 'none';
      })(),
    };
  });
  check('game_started = true', postStart.gameStarted === true);
  check('Controls visible', postStart.controlsVisible);
  check('Start overlay hidden', postStart.overlayHidden);

  // ── 7. Game buttons visible ───────────────────────────────────
  console.log('\n7. Game Buttons Visible');
  const gameBtns = await page.evaluate(() => {
    return ['btn-play', 'btn-pass', 'btn-hint', 'btn-organize',
            'btn-layout', 'btn-suggest', 'btn-evaluate',
            'btn-auto-play', 'btn-new-game'].map(id => {
      const el = document.getElementById(id);
      return { id, visible: el && el.offsetParent !== null };
    });
  });
  for (const b of gameBtns) {
    check(`${b.id} visible`, b.visible);
  }

  // ── 8. Card interaction ───────────────────────────────────────
  console.log('\n8. Card Interaction');
  const cardInfo = await page.evaluate(() => {
    const hand = document.getElementById('my-hand');
    const cards = hand ? hand.querySelectorAll('.card') : [];
    return { count: cards.length };
  });
  check('Cards present', cardInfo.count > 0, cardInfo.count + ' cards');

  // Try selecting a card
  const clicked = await page.evaluate(() => {
    const cards = document.getElementById('my-hand')?.querySelectorAll('.card');
    if (!cards || cards.length === 0) return 'no cards';
    const cid = cards[0].getAttribute('data-card-id');
    cards[0].click();
    return 'clicked ' + cid;
  });
  await page.waitForTimeout(300);

  const selectedCount = await page.evaluate(() =>
    document.getElementById('my-hand')?.querySelectorAll('.card.selected').length || 0
  );
  check('Card click → selected', selectedCount > 0, selectedCount + ' selected');
  check('Click info', clicked !== 'no cards', clicked);

  // Deselect by clicking again
  await page.evaluate(() => {
    const cards = document.getElementById('my-hand')?.querySelectorAll('.card.selected');
    if (cards?.[0]) cards[0].click();
  });
  await page.waitForTimeout(200);
  const afterDeselect = await page.evaluate(() =>
    document.getElementById('my-hand')?.querySelectorAll('.card.selected').length || 0
  );
  check('Second click → deselected', afterDeselect === 0, afterDeselect);

  // ── 9. Keyboard shortcuts ─────────────────────────────────────
  console.log('\n9. Keyboard Shortcuts');
  // Ctrl+D (toggle debug)
  const debugBefore = await page.evaluate(() => window._gameState?.debugMode);
  await page.keyboard.press('Control+d');
  await page.waitForTimeout(300);
  const debugAfter = await page.evaluate(() => {
    const btn = document.getElementById('btn-debug');
    return { debugMode: window._gameState?.debugMode, btnText: btn?.textContent };
  });
  check('Ctrl+D toggles debug', debugBefore !== debugAfter.debugMode,
    debugBefore + ' → ' + debugAfter.debugMode);

  // ── 10. Config panel ──────────────────────────────────────────
  console.log('\n10. Config Panel');
  await page.click('#btn-config');
  await page.waitForTimeout(500);
  const configVisible = await page.evaluate(() => {
    const el = document.getElementById('config-panel');
    return el && window.getComputedStyle(el).display !== 'none';
  });
  check('Config panel opens', configVisible);

  await page.click('#btn-config'); // close
  await page.waitForTimeout(300);
  const configClosed = await page.evaluate(() => {
    const el = document.getElementById('config-panel');
    return el && window.getComputedStyle(el).display === 'none';
  });
  check('Config panel closes', configClosed);

  // ── Console errors ────────────────────────────────────────────
  console.log('\nConsole Errors');
  if (errors.length === 0) {
    check('No console errors', true);
  } else {
    for (const e of errors) {
      check('Error: ' + e.substring(0, 100), false);
    }
  }

  // ── Summary ──────────────────────────────────────────────────
  console.log(`\n${'═'.repeat(40)}`);
  console.log(`Passed: ${passed}/${total}`);
  if (passed === total) {
    console.log('VERDICT: ALL CHECKS PASSED');
  } else {
    console.log(`VERDICT: ${total - passed} CHECK(S) FAILED`);
  }
  console.log(`${'═'.repeat(40)}`);

  await browser.close();
  process.exit(passed === total ? 0 : 1);
})();
