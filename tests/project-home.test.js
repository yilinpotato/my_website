const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

global.window = { location: { pathname: '/' } };
global.window.top = global.window;
global.window.self = global.window;

const {
  sanitizeEvent,
  classifyPath,
  extractComicId,
  beforeSend,
  initializeAnalytics,
} = require('../project-home.js');

const homepage = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

test('rejects events outside the explicit allowlist', () => {
  assert.equal(typeof sanitizeEvent, 'function');
  assert.equal(sanitizeEvent('password_submitted', { password: 'secret' }), null);
});

test('drops sensitive and unknown properties', () => {
  assert.equal(typeof sanitizeEvent, 'function');
  assert.deepEqual(
    sanitizeEvent('primary_click', {
      path: '/new/', control_id: 'training', label: 'Start', password: 'secret', value: 'typed text',
    }),
    { path: '/new/', control_id: 'training', label: 'Start' },
  );
});

test('extracts only bounded numeric comic ids', () => {
  assert.equal(typeof extractComicId, 'function');
  assert.equal(extractComicId('/jm/view/123456'), '123456');
  assert.equal(extractComicId('/jm/view/not-a-number'), null);
  assert.equal(extractComicId('/jm/view/12345678901234567'), null);
});

test('classifies every deployed site area', () => {
  assert.equal(typeof classifyPath, 'function');
  assert.equal(classifyPath('/'), 'home');
  assert.equal(classifyPath('/new/training'), 'new');
  assert.equal(classifyPath('/old/'), 'old');
  assert.equal(classifyPath('/jm/view/123'), 'jm');
  assert.equal(classifyPath('/mygo/'), 'mygo');
  assert.equal(classifyPath('/myagents/'), 'myagents');
});

test('before_send preserves a faithful current SDK envelope and drops person or unknown data', () => {
  assert.equal(typeof beforeSend, 'function');
  const longLabel = `  ${'x'.repeat(205)}  `;
  const sdkPayload = {
    uuid: '0198e210-7d80-7000-8000-000000000001',
    event: 'primary_click',
    properties: {
      token: 'phc_public-test-token',
      distinct_id: '0198e210-7d80-7000-8000-000000000002',
      '$process_person_profile': false,
      path: '/new/?session=secret#fragment',
      site_area: 'new',
      control_id: '  training  ',
      label: longLabel,
      target_path: '/new/training?token=secret#step-2',
      '$browser': 'Chrome',
      '$os': 'Linux',
      '$device_type': 'Desktop',
      '$lib': 'web',
      '$lib_version': '1.0.0',
      '$current_url': 'https://example.test/new/?secret=1',
      '$set': { email: 'private@example.test' },
      password: 'secret',
      unknown_automatic_property: 'drop me',
    },
    '$set': { email: 'private@example.test' },
    '$set_once': { initial_path: '/private' },
    '$unset': ['email'],
  };

  assert.deepEqual(beforeSend(sdkPayload), {
    uuid: '0198e210-7d80-7000-8000-000000000001',
    event: 'primary_click',
    properties: {
      token: 'phc_public-test-token',
      distinct_id: '0198e210-7d80-7000-8000-000000000002',
      '$process_person_profile': false,
      path: '/new/',
      site_area: 'new',
      control_id: 'training',
      label: 'x'.repeat(200),
      target_path: '/new/training',
      '$browser': 'Chrome',
      '$os': 'Linux',
      '$device_type': 'Desktop',
      '$lib': 'web',
      '$lib_version': '1.0.0',
    },
  });
  assert.equal(beforeSend({ event: 'autocapture', properties: { path: '/' } }), null);
});

test('before_send rejects malformed required transport identity, UUID, or profile fields and only retains valid optional timestamps', () => {
  const valid = {
    uuid: '0198e210-7d80-7000-8000-000000000001',
    event: '$pageview',
    properties: {
      token: 'phc_public-test-token',
      distinct_id: '0198e210-7d80-7000-8000-000000000002',
      '$process_person_profile': false,
      path: '/',
      site_area: 'home',
    },
  };
  const cases = [
    { ...valid, uuid: 'not-a-uuid' },
    { ...valid, properties: { ...valid.properties, token: 'private-key' } },
    { ...valid, properties: { ...valid.properties, distinct_id: 'bad id with spaces' } },
    { ...valid, properties: { ...valid.properties, '$process_person_profile': 'false' } },
  ];

  for (const payload of cases) assert.equal(beforeSend(payload), null);
  assert.equal(beforeSend({ ...valid, properties: { ...valid.properties, token: `phc_${'x'.repeat(197)}` } }), null);
  assert.equal(beforeSend({ ...valid, properties: { ...valid.properties, distinct_id: 'x'.repeat(201) } }), null);
  assert.deepEqual(beforeSend({ ...valid, timestamp: 'not-a-date' }), beforeSend(valid));
  const timestamp = new Date('2026-08-25T15:00:00.123Z');
  assert.deepEqual(beforeSend({ ...valid, timestamp }), { ...beforeSend(valid), timestamp });
});

test('event enums accept only the exact language, theme, source, and site-area values', () => {
  assert.deepEqual(sanitizeEvent('language_changed', { path: '/', language: 'en' }), { path: '/', language: 'en' });
  assert.deepEqual(sanitizeEvent('language_changed', { path: '/', language: 'EN' }), { path: '/' });
  assert.deepEqual(sanitizeEvent('theme_changed', { path: '/', theme: 'dark' }), { path: '/', theme: 'dark' });
  assert.deepEqual(sanitizeEvent('theme_changed', { path: '/', theme: 'system' }), { path: '/' });
  assert.deepEqual(sanitizeEvent('$pageview', { path: '/', site_area: 'home' }), { path: '/', site_area: 'home' });
  assert.deepEqual(sanitizeEvent('$pageview', { path: '/', site_area: 'admin' }), { path: '/' });
  assert.deepEqual(
    sanitizeEvent('comic_opened', { path: '/jm/view/1', comic_id: '1', source: 'viewer_search' }),
    { path: '/jm/view/1', comic_id: '1', source: 'viewer_search' },
  );
  assert.deepEqual(
    sanitizeEvent('comic_opened', { path: '/jm/view/1', comic_id: '1', source: 'deployment_test' }),
    { path: '/jm/view/1', comic_id: '1' },
  );
  assert.deepEqual(
    sanitizeEvent('comic_viewer_entered', { path: '/jm/', source: 'direct_view' }),
    { path: '/jm/' },
  );
});

test('analytics initializes on the homepage and captures only explicit UI metadata', () => {
  assert.equal(typeof initializeAnalytics, 'function');
  const listeners = {};
  const captures = [];
  let initialization;
  const posthog = {
    init(token, options) {
      initialization = { token, options };
      options.loaded(this);
    },
    capture(eventName, properties) { captures.push([eventName, properties]); },
  };
  const windowRef = {
    __SITE_ANALYTICS_CONFIG__: { token: 'phc_public-test-token' },
    location: { pathname: '/', href: 'https://example.test/?private=1#fragment' },
    posthog,
  };
  const documentRef = {
    title: '  Homepage  ',
    referrer: 'https://referrer.test/source?private=1#fragment',
    addEventListener(type, listener) { listeners[type] = listener; },
  };

  initializeAnalytics(windowRef, documentRef);

  assert.equal(initialization.token, 'phc_public-test-token');
  assert.equal(initialization.options.api_host, 'https://us.i.posthog.com');
  assert.equal(initialization.options.autocapture, false);
  assert.equal(initialization.options.capture_pageview, false);
  assert.equal(initialization.options.capture_pageleave, false);
  assert.equal(initialization.options.capture_heatmaps, false);
  assert.equal(initialization.options.capture_exceptions, false);
  assert.equal(initialization.options.disable_session_recording, true);
  assert.equal(initialization.options.disable_surveys, true);
  assert.equal(initialization.options.advanced_disable_flags, true);
  assert.equal(initialization.options.advanced_disable_feature_flags_on_first_load, true);
  assert.equal(initialization.options.person_profiles, 'identified_only');
  assert.equal(initialization.options.persistence, 'localStorage+cookie');
  assert.equal(initialization.options.before_send, beforeSend);

  listeners.click({
    target: {
      textContent: 'private rendered copy',
      closest() {
        return {
          dataset: { analyticsId: 'project-new', analyticsLabel: 'AI training' },
          getAttribute(name) { return name === 'href' ? '/new/?private=1#top' : null; },
        };
      },
    },
  });
  listeners.click({ target: { textContent: 'ignore me', closest() { return null; } } });
  listeners['site:language-changed']({ detail: 'en' });
  listeners['site:theme-changed']({ detail: 'dark' });

  assert.deepEqual(captures, [
    ['$pageview', {
      path: '/', site_area: 'home', title: 'Homepage', referrer: 'https://referrer.test/source',
    }],
    ['primary_click', {
      path: '/', site_area: 'home', control_id: 'project-new', label: 'AI training', target_path: '/new/',
    }],
    ['language_changed', { path: '/', language: 'en' }],
    ['theme_changed', { path: '/', theme: 'dark' }],
  ]);
});

test('queues sanitized control changes before the async SDK finishes loading', () => {
  const listeners = {};
  const insertedScripts = [];
  const windowRef = {
    __SITE_ANALYTICS_CONFIG__: { token: 'phc_public-test-token' },
    location: { pathname: '/' },
  };
  const documentRef = {
    title: 'Homepage',
    referrer: '',
    addEventListener(type, listener) { listeners[type] = listener; },
    createElement() { return {}; },
    getElementsByTagName() {
      return [{ parentNode: { insertBefore(script) { insertedScripts.push(script); } } }];
    },
    head: { append(script) { insertedScripts.push(script); } },
  };

  initializeAnalytics(windowRef, documentRef);
  listeners['site:theme-changed']({ detail: '  dark  ', password: 'secret' });

  assert.equal(insertedScripts[0].src, 'https://us-assets.i.posthog.com/static/array.js');
  assert.deepEqual(windowRef.posthog[0], [
    'capture',
    'theme_changed',
    { path: '/', theme: 'dark' },
  ]);
});

test('loader errors fail closed without breaking delegated navigation', () => {
  const listeners = {};
  const windowRef = {
    __SITE_ANALYTICS_CONFIG__: { token: 'phc_public-test-token' },
    location: { pathname: '/' },
  };
  const documentRef = {
    title: 'Homepage',
    referrer: '',
    addEventListener(type, listener) { listeners[type] = listener; },
    createElement() { throw new Error('blocked loader'); },
  };

  assert.doesNotThrow(() => initializeAnalytics(windowRef, documentRef));
  assert.doesNotThrow(() => listeners.click({
    target: {
      closest() {
        return {
          dataset: { analyticsId: 'project-new', analyticsLabel: 'AI training' },
          getAttribute() { return '/new/'; },
        };
      },
    },
  }));
  assert.equal(windowRef.posthog.capture, undefined);
});

test('approved homepage project cards carry stable explicit analytics metadata', () => {
  const cards = [...homepage.matchAll(/<a\b[^>]*\bclass="card"[^>]*>/g)].map((match) => match[0]);
  assert.equal(cards.length, 5);
  for (const card of cards) {
    assert.match(card, /\bdata-analytics-id="[a-z0-9-]+"/);
    assert.match(card, /\bdata-analytics-label="[^"]+"/);
  }
  assert.doesNotMatch(homepage, /<(?:input|form)\b[^>]*\bdata-analytics-(?:id|label)=/i);
});
