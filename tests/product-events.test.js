const test = require('node:test');
const assert = require('node:assert/strict');

global.window = { location: { pathname: '/' } };
global.window.top = global.window;
global.window.self = global.window;

const { installProductEventHooks, initializeAnalytics } = require('../project-home.js');

function createDocument() {
  const listeners = {};
  return {
    documentElement: { dataset: {} },
    addEventListener(type, listener) { listeners[type] = listener; },
    dispatch(type, detail) { listeners[type]({ detail }); },
  };
}

function createSessionStorage() {
  const values = new Map();
  return {
    getItem(key) { return values.get(key) || null; },
    setItem(key, value) { values.set(key, value); },
    removeItem(key) { values.delete(key); },
  };
}

function initializeQueuedProductEvent(pathname, authorized = false) {
  const windowRef = {
    __SITE_ANALYTICS_CONFIG__: { token: 'phc_public-test-token' },
    location: { pathname },
    sessionStorage: { getItem() { return null; }, removeItem() {}, setItem() {} },
  };
  const documentRef = {
    title: '',
    referrer: '',
    documentElement: { dataset: authorized ? { jmAuthorized: 'true' } : {} },
    addEventListener() {},
    createElement() { return {}; },
    getElementsByTagName() {
      return [{ parentNode: { insertBefore() {} } }];
    },
    head: { append() {} },
  };

  initializeAnalytics(windowRef, documentRef);
  return windowRef;
}

test('queues exactly one authorized viewer-entry event during analytics initialization', () => {
  const windowRef = initializeQueuedProductEvent('/jm/', true);

  assert.deepEqual(Array.from(windowRef.posthog), [
    ['capture', 'comic_viewer_entered', { path: '/jm/', source: 'password_authorized' }],
  ]);
});

test('queues exactly one direct-view event during analytics initialization', () => {
  const windowRef = initializeQueuedProductEvent('/jm/view/123456');

  assert.deepEqual(Array.from(windowRef.posthog), [
    ['capture', 'comic_opened', { path: '/jm/view/123456', comic_id: '123456', source: 'direct_view' }],
  ]);
});

test('a loader error fails closed without product events throwing', () => {
  const windowRef = {
    __SITE_ANALYTICS_CONFIG__: { token: 'phc_public-test-token' },
    location: { pathname: '/jm/view/123456' },
    sessionStorage: { getItem() { return null; }, removeItem() {}, setItem() {} },
  };
  const documentRef = {
    title: '',
    referrer: '',
    documentElement: { dataset: {} },
    addEventListener() {},
    createElement() { throw new Error('blocked loader'); },
  };

  assert.doesNotThrow(() => initializeAnalytics(windowRef, documentRef));
  assert.deepEqual(Array.from(windowRef.posthog), []);
});

test('viewer search persists only a bounded marker while training still captures after success', () => {
  const events = [];
  const documentRef = createDocument();
  const sessionStorage = createSessionStorage();
  const windowRef = {
    location: { pathname: '/jm/' },
    SiteAnalytics: { capture(name, properties) { events.push([name, properties]); } },
    sessionStorage,
  };

  installProductEventHooks(windowRef, documentRef);
  documentRef.dispatch('comic:opened', { comicId: '123456', source: 'viewer_search' });
  const marker = JSON.parse(sessionStorage.getItem('site_analytics_viewer_search'));
  windowRef.location.pathname = '/new/training';
  documentRef.dispatch('training:started');

  assert.deepEqual(events, [['training_started', { path: '/new/training' }]]);
  assert.equal(marker.comicId, '123456');
  assert.equal(marker.source, 'viewer_search');
  assert.equal(typeof marker.createdAt, 'number');
  assert.deepEqual(Object.keys(marker).sort(), ['comicId', 'createdAt', 'source']);
});

test('destination emits one viewer-search event for a matching marker and direct-view otherwise', () => {
  const searchedEvents = [];
  const searchedDocument = createDocument();
  const storage = new Map();
  const viewerWindow = {
    location: { pathname: '/jm/' },
    SiteAnalytics: { capture(name, properties) { searchedEvents.push([name, properties]); } },
    sessionStorage: {
      setItem(key, value) { storage.set(key, value); },
      getItem(key) { return storage.get(key) || null; },
      removeItem(key) { storage.delete(key); },
    },
  };

  installProductEventHooks(viewerWindow, searchedDocument);
  searchedDocument.dispatch('comic:opened', { comicId: '123456', source: 'viewer_search' });

  const directEvents = [];
  const directDocument = createDocument();
  const directWindow = {
    location: { pathname: '/jm/view/123456' },
    SiteAnalytics: { capture(name, properties) { directEvents.push([name, properties]); } },
    sessionStorage: viewerWindow.sessionStorage,
  };

  installProductEventHooks(directWindow, directDocument);
  assert.deepEqual(searchedEvents, []);
  assert.deepEqual(directEvents, [
    ['comic_opened', { path: '/jm/view/123456', comic_id: '123456', source: 'viewer_search' }],
  ]);

  const independentEvents = [];
  const independentWindow = {
    location: { pathname: '/jm/view/654321' },
    SiteAnalytics: { capture(name, properties) { independentEvents.push([name, properties]); } },
    sessionStorage: viewerWindow.sessionStorage,
  };
  installProductEventHooks(independentWindow, createDocument());
  assert.deepEqual(independentEvents, [
    ['comic_opened', { path: '/jm/view/654321', comic_id: '654321', source: 'direct_view' }],
  ]);
});

test('navigation before SDK load cannot zero or double count a viewer search', () => {
  const sessionStorage = createSessionStorage();
  const searchListeners = {};
  const searchWindow = {
    __SITE_ANALYTICS_CONFIG__: { token: 'phc_public-test-token' },
    location: { pathname: '/jm/' },
    sessionStorage,
  };
  const searchDocument = {
    title: '',
    referrer: '',
    documentElement: { dataset: {} },
    addEventListener(type, listener) { searchListeners[type] = listener; },
    createElement() { return {}; },
    getElementsByTagName() { return [{ parentNode: { insertBefore() {} } }]; },
    head: { append() {} },
  };
  initializeAnalytics(searchWindow, searchDocument);
  searchListeners['comic:opened']({ detail: { comicId: '123456', source: 'viewer_search' } });

  assert.deepEqual(Array.from(searchWindow.posthog), []);
  assert.ok(sessionStorage.getItem('site_analytics_viewer_search'));

  const destinationWindow = initializeQueuedProductEvent('/jm/view/123456');
  destinationWindow.sessionStorage = sessionStorage;
  const destinationListeners = {};
  const destinationDocument = {
    title: '',
    referrer: '',
    documentElement: { dataset: {} },
    addEventListener(type, listener) { destinationListeners[type] = listener; },
    createElement() { return {}; },
    getElementsByTagName() { return [{ parentNode: { insertBefore() {} } }]; },
    head: { append() {} },
  };
  delete destinationWindow.posthog;
  initializeAnalytics(destinationWindow, destinationDocument);

  assert.deepEqual(Array.from(destinationWindow.posthog), [
    ['capture', 'comic_opened', {
      path: '/jm/view/123456', comic_id: '123456', source: 'viewer_search',
    }],
  ]);
  assert.equal(sessionStorage.getItem('site_analytics_viewer_search'), null);
});

test('records entry only for an authorized viewer root and rejects unbounded comic IDs', () => {
  const events = [];
  const documentRef = createDocument();
  documentRef.documentElement.dataset.jmAuthorized = 'true';
  const windowRef = {
    location: { pathname: '/jm/' },
    SiteAnalytics: { capture(name, properties) { events.push([name, properties]); } },
    sessionStorage: { setItem() {}, getItem() { return null; }, removeItem() {} },
  };

  installProductEventHooks(windowRef, documentRef);
  documentRef.dispatch('comic:opened', { comicId: 'invalid-id', source: 'viewer_search' });

  assert.deepEqual(events, [
    ['comic_viewer_entered', { path: '/jm/', source: 'password_authorized' }],
  ]);
});
