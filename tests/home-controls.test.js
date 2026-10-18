const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {
  createSecretTrigger,
  resolveTheme,
  normalizeLanguage,
  translations,
} = require('../home-controls.js');

const homepage = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

function loadBrowserControls() {
  const events = [];
  const stored = new Map();
  const controls = {};
  function button(key) {
    const listeners = {};
    return {
      attributes: {},
      dataset: { i18n: key },
      textContent: '',
      addEventListener(type, listener) { listeners[type] = listener; },
      click() { listeners.click(); },
      setAttribute(name, value) { this.attributes[name] = value; },
    };
  }
  controls.languageToggle = button('control.language');
  controls.themeToggle = button('control.theme');
  const document = {
    documentElement: { dataset: {} },
    dispatchEvent(event) { events.push(event); },
    getElementById(id) { return controls[id]; },
    querySelectorAll() { return Object.values(controls); },
  };
  const window = {
    CustomEvent: function CustomEvent(type, options) {
      this.type = type;
      this.detail = options.detail;
    },
    document,
    localStorage: {
      getItem(key) { return stored.get(key) || null; },
      setItem(key, value) { stored.set(key, value); },
    },
    location: { assign() {} },
    matchMedia() { return { matches: false }; },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'home-controls.js'), 'utf8'), { window });
  return { controls, events };
}

test('theme uses saved choice before operating-system preference', () => {
  assert.equal(resolveTheme('light', true), 'light');
  assert.equal(resolveTheme(null, true), 'dark');
  assert.equal(resolveTheme(null, false), 'light');
});

test('language accepts only supported values', () => {
  assert.equal(normalizeLanguage('en'), 'en');
  assert.equal(normalizeLanguage('zh-CN'), 'zh-CN');
  assert.equal(normalizeLanguage('fr'), 'zh-CN');
});

test('seven theme clicks within five seconds trigger once', () => {
  let clock = 0;
  let triggers = 0;
  const click = createSecretTrigger({
    count: 7,
    windowMs: 5000,
    now: () => clock,
    onTrigger: () => { triggers += 1; },
  });
  for (let i = 0; i < 7; i += 1) { click(); clock += 600; }
  assert.equal(triggers, 1);
});

test('slow clicks do not open the hidden entry', () => {
  let clock = 0;
  let triggers = 0;
  const click = createSecretTrigger({
    count: 7,
    windowMs: 5000,
    now: () => clock,
    onTrigger: () => { triggers += 1; },
  });
  for (let i = 0; i < 7; i += 1) { click(); clock += 1000; }
  assert.equal(triggers, 0);
});

test('user control changes dispatch their selected values as direct event details', () => {
  const browser = loadBrowserControls();
  browser.controls.languageToggle.click();
  browser.controls.themeToggle.click();

  assert.deepEqual(browser.events.map((event) => [event.type, event.detail]), [
    ['site:language-changed', 'en'],
    ['site:theme-changed', 'dark'],
  ]);
});

test('bilingual copy has the same complete key set as homepage markup', () => {
  const chineseKeys = Object.keys(translations['zh-CN']).sort();
  const englishKeys = Object.keys(translations.en).sort();
  const markupKeys = [...homepage.matchAll(/data-i18n="([^"]+)"/g)]
    .map((match) => match[1])
    .sort();

  assert.deepEqual(englishKeys, chineseKeys);
  assert.deepEqual(markupKeys, chineseKeys);
});

test('language and theme controls are accessible buttons', () => {
  for (const id of ['languageToggle', 'themeToggle']) {
    const control = new RegExp(`<button[^>]*\\bid="${id}"[^>]*>`, 'i').exec(homepage);
    assert.ok(control, `${id} must be a button`);
    assert.match(control[0], /aria-label="[^"]+"/i);
    assert.match(control[0], /type="button"/i);
  }
});

test('theme control renders distinct sun and moon icons without visible text', () => {
  const themeControl = /<button[^>]*\bid="themeToggle"[^>]*>([\s\S]*?)<\/button>/i.exec(homepage);

  assert.ok(themeControl);
  assert.match(themeControl[1], /class="theme-icon__sun"[^>]*aria-hidden="true"/i);
  assert.match(themeControl[1], /class="theme-icon__moon"[^>]*aria-hidden="true"/i);
  assert.doesNotMatch(themeControl[1], /<span[^>]*data-i18n="control\.theme"/i);
  assert.match(homepage, /\[data-theme="dark"\]\s+\.theme-icon__sun\s*\{[^}]*display:\s*none/i);
  assert.match(homepage, /\[data-theme="dark"\]\s+\.theme-icon__moon\s*\{[^}]*display:\s*block/i);
});

test('homepage has no visible analytics-retention notice and protects every English title from clipping', () => {
  assert.doesNotMatch(homepage, /分析功能会记录有限的访问信息/i);
  assert.match(homepage, /\.hero h1\s*\{[^}]*max-width:\s*100%[^}]*overflow-wrap:\s*anywhere/s);
  for (const selector of ['.hero .eyebrow', '.hero p', '.card .eyebrow', '.card h2', '.card .desc']) {
    const escapedSelector = selector.replace('.', '\\.').replace(' ', '\\s+');
    assert.match(homepage, new RegExp(`${escapedSelector}\\s*\\{[^}]*overflow-wrap:\\s*anywhere`, 's'));
  }
});

test('mobile control styling keeps the controls inside the viewport', () => {
  assert.match(homepage, /<meta name="viewport" content="width=device-width, initial-scale=1\.0">/i);
  assert.match(homepage, /\.site-controls\s*\{[^}]*position:\s*absolute[^}]*right:\s*clamp\([^}]*display:\s*flex[^}]*max-width:\s*calc\(100vw\s*-\s*[^)]+\)/s);
  assert.match(homepage, /@media\s*\(max-width:\s*760px\)\s*\{[^}]*\.site-controls\s*\{[^}]*right:\s*12px[^}]*top:\s*12px[^}]*\}/s);
});
