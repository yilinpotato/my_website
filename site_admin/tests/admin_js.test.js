"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ADMIN_JS = fs.readFileSync(
  path.join(__dirname, "..", "static", "admin.js"),
  "utf8"
);
const EVENT_COLUMNS = [
  "timestamp", "uuid", "ip", "event", "path", "referrer",
  "device", "browser", "os", "country", "city", "comic_id",
];

class FakeElement {
  constructor({ dataset = {}, value = "" } = {}) {
    this.dataset = dataset;
    this.value = value;
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.children = [];
    this.style = {};
    this.listeners = new Map();
    this.output = null;
    this.list = null;
  }

  addEventListener(type, handler) {
    const handlers = this.listeners.get(type) || [];
    handlers.push(handler);
    this.listeners.set(type, handlers);
  }

  dispatch(type) {
    const event = { preventDefault() {} };
    for (const handler of this.listeners.get(type) || []) {
      handler(event);
    }
  }

  querySelector(selector) {
    if (selector === "strong") return this.output;
    if (selector === "ol") return this.list;
    return null;
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  setAttribute(name, value) {
    this[name] = String(value);
  }
}

function payloadFor(url, marker) {
  const parsed = new URL(url);
  if (parsed.pathname.endsWith("/summary")) {
    return {
      columns: [
        "total_events", "pageviews", "unique_visitors", "unique_ips",
        "primary_clicks", "comic_entries", "unique_comic_ids",
      ],
      results: [[marker, marker, marker, marker, marker, marker, marker]],
    };
  }
  if (parsed.pathname.endsWith("/trend")) {
    return { columns: ["day", "pageviews"], results: [["2026-08-25", marker]] };
  }
  if (parsed.pathname.endsWith("/rankings")) {
    return { columns: ["value", "count"], results: [[`rank-${marker}`, marker]] };
  }
  return {
    columns: EVENT_COLUMNS,
    results: [[
      "2026-08-25T07:06:05Z",
      "12345678-1234-4abc-8def-1234567890ab",
      `ip-${marker}`,
      "comic_opened",
      `/path-${marker}`,
      "referrer",
      "desktop",
      "browser",
      "os",
      "country",
      "city",
      String(marker),
    ]],
    next_cursor: null,
  };
}

function createHarness() {
  const elements = new Map();
  const add = (id, element = new FakeElement()) => {
    elements.set(`#${id}`, element);
    return element;
  };
  const controls = {
    days: new FakeElement({ value: "30" }),
    ip: new FakeElement(),
    path: new FakeElement(),
    event: new FakeElement(),
    comic_id: new FakeElement(),
  };
  const form = add("filter-form");
  form.elements = { namedItem: (name) => controls[name] || null };
  form.reportValidity = () => true;
  form.reset = () => {
    controls.days.value = "30";
    for (const name of ["ip", "path", "event", "comic_id"]) controls[name].value = "";
  };
  const status = add("dashboard-status");
  const updated = add("last-updated");
  updated.textContent = "Never";
  const stale = add("stale-indicator");
  stale.hidden = true;
  const retry = add("retry-button");
  retry.hidden = true;
  add("export-link");
  add("previous-page");
  add("next-page");
  add("page-number");
  add("reset-filters");
  add("event-table-body");
  add("trend-chart");

  const kpis = [
    "pageviews", "unique_visitors", "unique_ips",
    "primary_clicks", "comic_entries", "unique_comic_ids",
  ].map((name) => {
    const card = new FakeElement({ dataset: { kpi: name } });
    card.output = new FakeElement();
    card.output.textContent = "—";
    return card;
  });
  const rankingPanels = new Map();
  for (const kind of ["page", "referrer", "country", "device", "operation", "comic"]) {
    const panel = new FakeElement({ dataset: { rankingKind: kind } });
    panel.list = new FakeElement();
    rankingPanels.set(kind, panel);
  }

  const requests = [];
  function fetch(url, options) {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
      resolve = resolvePromise;
      reject = rejectPromise;
    });
    requests.push({ url: String(url), options, resolve, reject });
    return promise;
  }
  const redirects = [];
  const body = new FakeElement({
    dataset: {
      summaryUrl: "/site-admin/api/summary",
      trendUrl: "/site-admin/api/trend",
      rankingsUrl: "/site-admin/api/rankings",
      eventsUrl: "/site-admin/api/events",
      exportUrl: "/site-admin/export.csv",
      loginUrl: "/site-admin/login",
    },
  });
  const document = {
    body,
    querySelector(selector) {
      if (elements.has(selector)) return elements.get(selector);
      const match = selector.match(/^\[data-ranking-kind="(.+)"\]$/);
      return match ? rankingPanels.get(match[1]) : null;
    },
    querySelectorAll(selector) {
      return selector === "[data-kpi]" ? kpis : [];
    },
    createElement() {
      return new FakeElement();
    },
    createElementNS() {
      return new FakeElement();
    },
  };
  class FakeFormData {
    constructor() {}
    get(name) {
      return controls[name] ? controls[name].value : null;
    }
  }
  const context = {
    AbortController,
    Date,
    FormData: FakeFormData,
    Intl,
    Promise,
    URL,
    URLSearchParams,
    document,
    fetch,
    window: {
      location: {
        origin: "https://example.test",
        pathname: "/site-admin/",
        search: "",
        assign(value) { redirects.push(value); },
      },
      history: { replaceState() {} },
    },
  };
  vm.runInNewContext(ADMIN_JS, context, { filename: "admin.js" });
  return {
    elements,
    kpis,
    redirects,
    requests,
    retry,
    stale,
    status,
    updated,
    pageviews: () => kpis[0].output.textContent,
  };
}

function respond(request, { marker = 1, status = 200 } = {}) {
  request.resolve({
    status,
    ok: status >= 200 && status < 300,
    json: async () => payloadFor(request.url, marker),
  });
}

async function flush() {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

async function waitForRequests(harness, count) {
  for (let attempt = 0; attempt < 20 && harness.requests.length < count; attempt += 1) {
    await flush();
  }
  assert.equal(harness.requests.length, count);
}

async function completeBatch(harness, start, marker, failureIndex = -1, failureStatus = 503) {
  await waitForRequests(harness, start + 9);
  harness.requests.slice(start, start + 9).forEach((request, index) => {
    respond(request, { marker, status: index === failureIndex ? failureStatus : 200 });
  });
  await flush();
}

test("a failed refresh preserves the prior complete dashboard atomically", async () => {
  const harness = createHarness();
  await completeBatch(harness, 0, 10);
  const successfulTimestamp = harness.updated.textContent;
  assert.equal(harness.pageviews(), "10");

  harness.retry.dispatch("click");
  await waitForRequests(harness, 18);
  respond(harness.requests[10], { marker: 20, status: 503 });
  await flush();
  harness.requests.slice(9, 18).forEach((request, index) => {
    if (index !== 1) respond(request, { marker: 20 });
  });
  await flush();

  assert.equal(harness.pageviews(), "10");
  assert.equal(harness.updated.textContent, successfulTimestamp);
  assert.equal(harness.stale.hidden, false);
  assert.equal(harness.retry.hidden, false);
});

test("responses from an aborted older generation cannot overwrite newer data", async () => {
  const harness = createHarness();
  await completeBatch(harness, 0, 10);

  harness.retry.dispatch("click");
  await waitForRequests(harness, 18);
  harness.retry.dispatch("click");
  assert.equal(harness.requests[9].options.signal.aborted, true);
  await completeBatch(harness, 18, 30);
  assert.equal(harness.pageviews(), "30");

  harness.requests.slice(9, 18).forEach((request) => respond(request, { marker: 20 }));
  await flush();

  assert.equal(harness.pageviews(), "30");
});

test("a first-load 503 renders no partial values and exposes stale retry state", async () => {
  const harness = createHarness();

  await completeBatch(harness, 0, 20, 1);

  assert.equal(harness.pageviews(), "—");
  assert.match(harness.status.textContent, /尚未成功加载任何数据/);
  assert.equal(harness.updated.textContent, "Never");
  assert.equal(harness.stale.hidden, false);
  assert.equal(harness.retry.hidden, false);
});

test("a 401 redirects outside the dashboard to the prefix-correct login URL", async () => {
  const harness = createHarness();

  await completeBatch(harness, 0, 1, 0, 401);

  assert.deepEqual(harness.redirects, ["/site-admin/login"]);
});
