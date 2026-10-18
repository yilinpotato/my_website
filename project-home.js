const SiteAnalyticsModule = (() => {
  const EVENT_PROPERTIES = Object.freeze({
    '$pageview': ['path', 'site_area', 'title', 'referrer'],
    primary_click: ['path', 'site_area', 'control_id', 'label', 'target_path'],
    language_changed: ['path', 'language'],
    theme_changed: ['path', 'theme'],
    comic_viewer_entered: ['path', 'source'],
    comic_opened: ['path', 'comic_id', 'source'],
    training_started: ['path'],
  });
  const SITE_AREAS = Object.freeze(['home', 'new', 'old', 'jm', 'mygo', 'myagents', 'other']);
  const EVENT_PROPERTY_ENUMS = Object.freeze({
    '$pageview': Object.freeze({ site_area: SITE_AREAS }),
    primary_click: Object.freeze({ site_area: SITE_AREAS }),
    language_changed: Object.freeze({ language: Object.freeze(['zh-CN', 'en']) }),
    theme_changed: Object.freeze({ theme: Object.freeze(['light', 'dark']) }),
    comic_viewer_entered: Object.freeze({ source: Object.freeze(['password_authorized']) }),
    comic_opened: Object.freeze({ source: Object.freeze(['viewer_search', 'direct_view']) }),
  });
  const OPTIONAL_TRANSPORT_PROPERTIES = Object.freeze(['$browser', '$os', '$device_type', '$lib', '$lib_version']);
  const PUBLIC_TOKEN_PATTERN = /^phc_[A-Za-z0-9_-]{1,196}$/;
  const DISTINCT_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
  const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const UNSAFE_STRING_CHARACTER = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/;
  const VIEWER_SEARCH_STORAGE_KEY = 'site_analytics_viewer_search';
  const VIEWER_SEARCH_TTL_MS = 10_000;

  function stripQueryAndHash(value) {
    return value.split(/[?#]/, 1)[0];
  }

  function sanitizeEvent(eventName, input) {
    const allowedProperties = EVENT_PROPERTIES[eventName];
    if (!allowedProperties || !input || typeof input !== 'object') return null;

    const sanitized = {};
    for (const property of allowedProperties) {
      if (typeof input[property] !== 'string') continue;
      let value = input[property].trim().slice(0, 200);
      if (['path', 'target_path', 'referrer'].includes(property)) value = stripQueryAndHash(value);
      if (property === 'comic_id' && !/^\d{1,16}$/.test(value)) continue;
      const allowedValues = EVENT_PROPERTY_ENUMS[eventName] && EVENT_PROPERTY_ENUMS[eventName][property];
      if (allowedValues && !allowedValues.includes(value)) continue;
      sanitized[property] = value;
    }
    return sanitized;
  }

  function boundedTransportString(value) {
    if (typeof value !== 'string' || UNSAFE_STRING_CHARACTER.test(value)) return null;
    const normalized = value.trim();
    return normalized && normalized.length <= 200 ? normalized : null;
  }

  function beforeSend(event) {
    if (!event || typeof event !== 'object' || !event.properties || typeof event.properties !== 'object') return null;
    const properties = sanitizeEvent(event.event, event.properties);
    if (properties === null) return null;

    const token = event.properties.token;
    const distinctId = event.properties.distinct_id;
    const processPersonProfile = event.properties.$process_person_profile;
    if (
      typeof token !== 'string'
      || !PUBLIC_TOKEN_PATTERN.test(token)
      || typeof distinctId !== 'string'
      || !DISTINCT_ID_PATTERN.test(distinctId)
      || typeof processPersonProfile !== 'boolean'
      || typeof event.uuid !== 'string'
      || !UUID_PATTERN.test(event.uuid)
    ) return null;

    properties.token = token;
    properties.distinct_id = distinctId;
    properties.$process_person_profile = processPersonProfile;
    for (const property of OPTIONAL_TRANSPORT_PROPERTIES) {
      const value = boundedTransportString(event.properties[property]);
      if (value !== null) properties[property] = value;
    }
    const sanitizedEvent = {
      uuid: event.uuid,
      event: event.event,
      properties,
    };
    // Current PostHog browser SDK payloads do not expose a timestamp here, but
    // preserve a valid one if a compatible SDK supplies it.
    if (event.timestamp instanceof Date && Number.isFinite(event.timestamp.getTime())) {
      sanitizedEvent.timestamp = event.timestamp;
    }
    return sanitizedEvent;
  }

  function extractComicId(pathname) {
    if (typeof pathname !== 'string') return null;
    const match = /^\/jm\/view\/(\d{1,16})(?:\/|$)/.exec(stripQueryAndHash(pathname));
    return match ? match[1] : null;
  }

  function readPendingViewerSearch(storage) {
    if (!storage || typeof storage.getItem !== 'function') return null;
    try {
      const pending = JSON.parse(storage.getItem(VIEWER_SEARCH_STORAGE_KEY));
      if (
        !pending
        || typeof pending.comicId !== 'string'
        || !/^\d{1,16}$/.test(pending.comicId)
        || typeof pending.createdAt !== 'number'
        || !Number.isFinite(pending.createdAt)
        || pending.source !== 'viewer_search'
      ) return null;
      return pending;
    } catch {
      return null;
    }
  }

  function clearPendingViewerSearch(storage) {
    if (!storage || typeof storage.removeItem !== 'function') return;
    try {
      storage.removeItem(VIEWER_SEARCH_STORAGE_KEY);
    } catch {
      // Session storage can be unavailable in restrictive browser contexts.
    }
  }

  function rememberViewerSearch(storage, comicId) {
    if (!storage || typeof storage.setItem !== 'function' || !/^\d{1,16}$/.test(comicId)) return;
    try {
      storage.setItem(VIEWER_SEARCH_STORAGE_KEY, JSON.stringify({
        comicId,
        createdAt: Date.now(),
        source: 'viewer_search',
      }));
    } catch {
      // Analytics must not interfere with viewer navigation.
    }
  }

  function installProductEventHooks(windowRef, documentRef) {
    if (!windowRef.SiteAnalytics || typeof windowRef.SiteAnalytics.capture !== 'function') return;
    const capture = windowRef.SiteAnalytics.capture;
    const currentPath = () => stripQueryAndHash(windowRef.location.pathname || '/');
    const storage = windowRef.sessionStorage;
    const path = currentPath();

    if (path === '/jm/' && documentRef.documentElement && documentRef.documentElement.dataset.jmAuthorized === 'true') {
      capture('comic_viewer_entered', { path, source: 'password_authorized' });
    }

    const directComicId = extractComicId(path);
    if (directComicId) {
      const pending = readPendingViewerSearch(storage);
      const isImmediateViewerNavigation = pending
        && pending.comicId === directComicId
        && pending.source === 'viewer_search'
        && Date.now() - pending.createdAt >= 0
        && Date.now() - pending.createdAt <= VIEWER_SEARCH_TTL_MS;
      clearPendingViewerSearch(storage);
      capture('comic_opened', {
        path,
        comic_id: directComicId,
        source: isImmediateViewerNavigation ? 'viewer_search' : 'direct_view',
      });
    }

    documentRef.addEventListener('comic:opened', (event) => {
      const detail = event && event.detail;
      if (!detail || detail.source !== 'viewer_search' || typeof detail.comicId !== 'string') return;
      const comicId = detail.comicId.trim();
      if (!/^\d{1,16}$/.test(comicId)) return;
      rememberViewerSearch(storage, comicId);
    });
    documentRef.addEventListener('training:started', () => {
      capture('training_started', { path: currentPath() });
    });
  }

  function classifyPath(pathname) {
    const path = typeof pathname === 'string' ? stripQueryAndHash(pathname) : '';
    if (path === '/') return 'home';
    for (const area of ['new', 'old', 'jm', 'mygo', 'myagents']) {
      if (path === `/${area}` || path.startsWith(`/${area}/`)) return area;
    }
    return 'other';
  }

  function installPostHogLoader(windowRef, documentRef) {
    const posthog = windowRef.posthog || [];
    if (typeof posthog.init === 'function') return posthog;

    // This is PostHog's official array bootstrap, expanded for readability.
    windowRef.posthog = posthog;
    posthog._i = [];
    posthog.init = function init(token, options, name) {
      function stub(target, method) {
        const parts = method.split('.');
        if (parts.length === 2) {
          target = target[parts[0]];
          method = parts[1];
        }
        target[method] = function queueCall() {
          target.push([method].concat(Array.prototype.slice.call(arguments, 0)));
        };
      }

      const script = documentRef.createElement('script');
      script.type = 'text/javascript';
      script.crossOrigin = 'anonymous';
      script.async = true;
      script.src = options.api_host.replace('.i.posthog.com', '-assets.i.posthog.com') + '/static/array.js';
      const firstScript = documentRef.getElementsByTagName('script')[0];
      if (firstScript && firstScript.parentNode) firstScript.parentNode.insertBefore(script, firstScript);
      else documentRef.head.append(script);

      let instance = posthog;
      if (name !== undefined) instance = posthog[name] = [];
      else name = 'posthog';
      instance.people = instance.people || [];
      instance.toString = function toString(stubPeople) {
        let value = 'posthog';
        if (name !== 'posthog') value += `.${name}`;
        if (!stubPeople) value += ' (stub)';
        return value;
      };
      instance.people.toString = function peopleToString() { return `${instance.toString(1)}.people (stub)`; };
      const methods = 'init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagPayload isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug'.split(' ');
      for (const method of methods) stub(instance, method);
      posthog._i.push([token, options, name]);
    };
    posthog.__SV = 1;
    return posthog;
  }

  function initializeAnalytics(windowRef, documentRef) {
    let captureEnabled = false;
    const currentPath = () => stripQueryAndHash(windowRef.location.pathname || '/');
    const capture = (eventName, properties) => {
      if (!captureEnabled || !windowRef.posthog || typeof windowRef.posthog.capture !== 'function') return;
      const sanitized = sanitizeEvent(eventName, properties);
      if (sanitized === null) return;
      windowRef.posthog.capture(eventName, sanitized);
    };
    windowRef.SiteAnalytics = Object.freeze({ capture });

    const config = windowRef.__SITE_ANALYTICS_CONFIG__;
    if (!config || typeof config.token !== 'string' || !PUBLIC_TOKEN_PATTERN.test(config.token.trim())) {
      return windowRef.SiteAnalytics;
    }

    documentRef.addEventListener('click', (event) => {
      const clicked = event.target && typeof event.target.closest === 'function'
        ? event.target.closest('[data-analytics-id]')
        : null;
      if (!clicked) return;
      const path = currentPath();
      capture('primary_click', {
        path,
        site_area: classifyPath(path),
        control_id: clicked.dataset.analyticsId,
        label: clicked.dataset.analyticsLabel,
        target_path: clicked.getAttribute('href') || '',
      });
    });
    documentRef.addEventListener('site:language-changed', (event) => {
      capture('language_changed', { path: currentPath(), language: event.detail });
    });
    documentRef.addEventListener('site:theme-changed', (event) => {
      capture('theme_changed', { path: currentPath(), theme: event.detail });
    });

    try {
      const posthog = installPostHogLoader(windowRef, documentRef);
      captureEnabled = true;
      posthog.init(config.token.trim(), {
        api_host: 'https://us.i.posthog.com',
        autocapture: false,
        capture_pageview: false,
        capture_pageleave: false,
        capture_heatmaps: false,
        capture_exceptions: false,
        disable_session_recording: true,
        disable_surveys: true,
        advanced_disable_flags: true,
        advanced_disable_feature_flags_on_first_load: true,
        person_profiles: 'identified_only',
        persistence: 'localStorage+cookie',
        before_send: beforeSend,
        loaded() {
          const path = currentPath();
          capture('$pageview', {
            path,
            site_area: classifyPath(path),
            title: documentRef.title,
            referrer: documentRef.referrer,
          });
        },
      });
      installProductEventHooks(windowRef, documentRef);
    } catch {
      captureEnabled = false;
    }
    return windowRef.SiteAnalytics;
  }

  function addHomeButton(windowRef, documentRef) {
    if (
      windowRef.location.pathname === '/'
      || windowRef.top !== windowRef.self
      || documentRef.querySelector('.site-project-home')
    ) return;

    const style = documentRef.createElement('style');
    style.textContent = `.site-project-home-floating{position:fixed;bottom:max(22px,env(safe-area-inset-bottom));left:max(22px,env(safe-area-inset-left));z-index:2147483647;display:inline-flex;align-items:center;gap:10px;box-sizing:border-box;min-height:46px;padding:7px 17px 7px 8px;border:1px solid rgba(148,163,184,.28);border-radius:999px;background:rgba(255,255,255,.94);box-shadow:0 4px 20px rgba(15,23,42,.10);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);color:#334155!important;font:600 13px/1.2 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;text-decoration:none!important;transition:transform .2s ease,box-shadow .2s ease,border-color .2s ease}.site-project-home-floating svg{display:block;width:30px;height:30px;padding:7px;box-sizing:border-box;border-radius:50%;background:#eef2ff;color:#4f46e5;flex-shrink:0}.site-project-home-floating:hover{transform:translateY(-2px);border-color:#a5b4fc;box-shadow:0 7px 24px rgba(79,70,229,.16)}.site-project-home-floating:focus-visible{outline:3px solid #818cf8;outline-offset:4px}html[data-theme="dark"] .site-project-home-floating{background:rgba(30,41,59,.94);color:#e2e8f0!important;border-color:#475569}html[data-theme="dark"] .site-project-home-floating svg{background:#334155;color:#c7d2fe}@media(max-width:600px){.site-project-home-floating{bottom:max(14px,env(safe-area-inset-bottom));left:max(12px,env(safe-area-inset-left));min-height:44px;padding-right:14px;gap:8px}}@media(prefers-reduced-motion:reduce){.site-project-home-floating{transition:none}}`;
    documentRef.head.append(style);
    const button = documentRef.createElement('a');
    button.className = 'site-project-home site-project-home-floating';
    button.href = '/';
    button.setAttribute('aria-label', '返回项目主页');
    button.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 5-7 7 7 7M5 12h14"/></svg><span>返回主页</span>';
    const updateLabel = () => {
      let english = documentRef.documentElement.lang === 'en';
      try { english = windowRef.localStorage.getItem('site-language') === 'en'; } catch {}
      button.querySelector('span').textContent = english ? 'Back to home' : '返回主页';
      button.setAttribute('aria-label', english ? 'Back to project home' : '返回项目主页');
    };
    updateLabel();
    windowRef.addEventListener('site:language-changed', updateLabel);
    documentRef.body.append(button);
  }

  return {
    EVENT_PROPERTIES,
    sanitizeEvent,
    beforeSend,
    classifyPath,
    extractComicId,
    installProductEventHooks,
    initializeAnalytics,
    addHomeButton,
  };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = SiteAnalyticsModule;

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  SiteAnalyticsModule.initializeAnalytics(window, document);
  SiteAnalyticsModule.addHomeButton(window, document);
}
