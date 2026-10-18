(() => {
  "use strict";

  const body = document.body;
  const endpoints = {
    summary: body.dataset.summaryUrl,
    trend: body.dataset.trendUrl,
    rankings: body.dataset.rankingsUrl,
    events: body.dataset.eventsUrl,
    export: body.dataset.exportUrl,
    login: body.dataset.loginUrl,
  };
  const filterNames = ["days", "ip", "path", "event", "comic_id"];
  const allowedDays = new Set(["1", "7", "30", "90"]);
  const rankingKinds = ["page", "referrer", "country", "device", "operation", "comic"];
  const numberFormatter = new Intl.NumberFormat();
  const form = document.querySelector("#filter-form");
  const status = document.querySelector("#dashboard-status");
  const updated = document.querySelector("#last-updated");
  const stale = document.querySelector("#stale-indicator");
  const retry = document.querySelector("#retry-button");
  const exportLink = document.querySelector("#export-link");
  const previousPage = document.querySelector("#previous-page");
  const nextPage = document.querySelector("#next-page");
  const pageNumber = document.querySelector("#page-number");
  let activeController = null;
  let activeGeneration = 0;
  let lastSuccessfulAt = null;
  let cursorStack = [null];
  let pageIndex = 0;

  function filtersFromLocation() {
    const source = new URLSearchParams(window.location.search);
    const filters = new URLSearchParams();
    const days = source.get("days");
    filters.set("days", allowedDays.has(days) ? days : "30");
    for (const name of filterNames.slice(1)) {
      const value = source.get(name);
      if (value) {
        filters.set(name, value);
      }
    }
    return filters;
  }

  function filtersFromForm() {
    const data = new FormData(form);
    const filters = new URLSearchParams();
    for (const name of filterNames) {
      const value = String(data.get(name) || "").trim();
      if (name === "days" || value) {
        filters.set(name, value);
      }
    }
    return filters;
  }

  function populateForm(filters) {
    for (const name of filterNames) {
      const control = form.elements.namedItem(name);
      if (control) {
        control.value = filters.get(name) || (name === "days" ? "30" : "");
      }
    }
  }

  function setLocation(filters) {
    const query = filters.toString();
    const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}`;
    window.history.replaceState(null, "", nextUrl);
  }

  function endpointUrl(base, filters, extra = {}) {
    const url = new URL(base, window.location.origin);
    url.search = filters.toString();
    for (const [name, value] of Object.entries(extra)) {
      if (value) {
        url.searchParams.set(name, value);
      }
    }
    return url;
  }

  async function fetchJson(url, signal) {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      signal,
    });
    if (response.status === 401) {
      window.location.assign(endpoints.login || "/site-admin/login");
      throw new Error("Authentication required");
    }
    if (response.status === 503) {
      const error = new Error("数据服务暂时不可用。");
      error.retryable = true;
      throw error;
    }
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Unable to load analytics.");
    }
    return payload;
  }

  function rowsAsObjects(payload) {
    if (!payload || !Array.isArray(payload.columns) || !Array.isArray(payload.results)) {
      throw new Error("数据服务返回了无效响应。");
    }
    return payload.results.map((row) => {
      if (!Array.isArray(row) || row.length !== payload.columns.length) {
        throw new Error("数据服务返回了无效响应。");
      }
      const item = {};
      payload.columns.forEach((column, index) => {
        item[column] = row[index];
      });
      return item;
    });
  }

  function renderSummary(payload) {
    const row = rowsAsObjects(payload)[0] || {};
    document.querySelectorAll("[data-kpi]").forEach((card) => {
      const value = Number(row[card.dataset.kpi]);
      const output = card.querySelector("strong");
      output.textContent = Number.isFinite(value) ? numberFormatter.format(value) : "—";
    });
  }

  function svgElement(name, attributes = {}) {
    const element = document.createElementNS("http://www.w3.org/2000/svg", name);
    for (const [key, value] of Object.entries(attributes)) {
      element.setAttribute(key, String(value));
    }
    return element;
  }

  function renderTrend(payload) {
    const chart = document.querySelector("#trend-chart");
    const rows = rowsAsObjects(payload);
    const width = 900;
    const height = 260;
    const padding = { top: 24, right: 18, bottom: 48, left: 46 };
    const innerWidth = width - padding.left - padding.right;
    const innerHeight = height - padding.top - padding.bottom;
    chart.replaceChildren();
    const description = svgElement("desc", { id: "trend-description" });
      description.textContent = "当前筛选条件下每天的页面访问量。";
    chart.append(description);
    if (rows.length === 0) {
      const empty = svgElement("text", { x: width / 2, y: height / 2, class: "chart-empty" });
      empty.textContent = "此时间范围内没有页面访问记录";
      chart.append(empty);
      return;
    }
    const maximum = Math.max(1, ...rows.map((row) => Number(row.pageviews) || 0));
    const slot = innerWidth / rows.length;
    const barWidth = Math.max(3, Math.min(36, slot * 0.68));
    const baseline = svgElement("line", {
      x1: padding.left,
      y1: padding.top + innerHeight,
      x2: width - padding.right,
      y2: padding.top + innerHeight,
      class: "chart-axis",
    });
    chart.append(baseline);
    rows.forEach((row, index) => {
      const value = Math.max(0, Number(row.pageviews) || 0);
      const barHeight = (value / maximum) * innerHeight;
      const x = padding.left + index * slot + (slot - barWidth) / 2;
      const y = padding.top + innerHeight - barHeight;
      const bar = svgElement("rect", {
        x,
        y,
        width: barWidth,
        height: barHeight,
        rx: 4,
        class: "trend-bar",
        tabindex: 0,
        role: "img",
        "aria-label": `${String(row.day)}：${numberFormatter.format(value)} 次页面访问`,
      });
      const title = svgElement("title");
      title.textContent = `${String(row.day)}：${numberFormatter.format(value)}`;
      bar.append(title);
      chart.append(bar);
      if (rows.length <= 14 || index === 0 || index === rows.length - 1 || index % 7 === 0) {
        const label = svgElement("text", {
          x: x + barWidth / 2,
          y: height - 22,
          class: "chart-label",
          "text-anchor": "middle",
        });
        label.textContent = String(row.day).slice(5);
        chart.append(label);
      }
    });
  }

  function renderRanking(kind, payload) {
    const panel = document.querySelector(`[data-ranking-kind="${kind}"]`);
    const list = panel.querySelector("ol");
    const rows = rowsAsObjects(payload);
    const maximum = Math.max(1, ...rows.map((row) => Number(row.count) || 0));
    const items = rows.map((row) => {
      const item = document.createElement("li");
      const copy = document.createElement("span");
      copy.className = "ranking-copy";
      const label = document.createElement("span");
      label.className = "ranking-label";
      label.textContent = String(row.value || "未知");
      const count = document.createElement("strong");
      count.textContent = numberFormatter.format(Number(row.count) || 0);
      const bar = document.createElement("span");
      bar.className = "ranking-bar";
      bar.style.width = `${Math.max(2, ((Number(row.count) || 0) / maximum) * 100)}%`;
      copy.append(label, count);
      item.append(copy, bar);
      return item;
    });
    if (items.length === 0) {
      const empty = document.createElement("li");
      empty.className = "empty-row";
      empty.textContent = "暂无结果";
      items.push(empty);
    }
    list.replaceChildren(...items);
  }

  function displayValue(value) {
    return value === null || value === undefined || value === "" ? "—" : String(value);
  }

  function renderEvents(payload) {
    const tableBody = document.querySelector("#event-table-body");
    const rows = rowsAsObjects(payload);
    const tableRows = rows.map((row) => {
      const tr = document.createElement("tr");
      const device = [row.device, row.browser, row.os].filter(Boolean).join(" / ");
      const values = [row.timestamp, row.ip, row.event, row.path, row.referrer, device, row.comic_id];
      values.forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = displayValue(value);
        tr.append(cell);
      });
      return tr;
    });
    if (tableRows.length === 0) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 7;
      cell.className = "empty-row";
      cell.textContent = "没有符合当前筛选条件的操作记录。";
      row.append(cell);
      tableRows.push(row);
    }
    tableBody.replaceChildren(...tableRows);
    cursorStack[pageIndex + 1] = payload.next_cursor || null;
    cursorStack.length = pageIndex + 2;
    previousPage.disabled = pageIndex === 0;
    nextPage.disabled = !payload.next_cursor;
    pageNumber.textContent = `第 ${pageIndex + 1} 页`;
  }

  function showSuccess() {
    lastSuccessfulAt = new Date();
    updated.dateTime = lastSuccessfulAt.toISOString();
    updated.textContent = lastSuccessfulAt.toLocaleString();
    status.textContent = "数据已更新。";
    stale.hidden = true;
    retry.hidden = true;
  }

  function showFailure(error) {
    const retryable = Boolean(error.retryable);
    if (retryable) {
      status.textContent = lastSuccessfulAt
        ? "数据暂时不可用，当前显示上一次成功加载的结果。"
        : "数据暂时不可用，尚未成功加载任何数据。";
    } else {
      status.textContent = error.message;
    }
    stale.hidden = !retryable;
    retry.hidden = !retryable;
  }

  async function loadDashboard({ resetPagination = false } = {}) {
    if (activeController) {
      activeController.abort();
    }
    const controller = new AbortController();
    activeController = controller;
    const generation = activeGeneration + 1;
    activeGeneration = generation;
    const filters = filtersFromForm();
    if (resetPagination) {
      cursorStack = [null];
      pageIndex = 0;
    }
    setLocation(filters);
    exportLink.href = endpointUrl(endpoints.export, filters).toString();
    status.textContent = "正在加载数据…";
    retry.hidden = true;

    try {
      const payloads = await Promise.all([
        fetchJson(endpointUrl(endpoints.summary, filters), controller.signal),
        fetchJson(endpointUrl(endpoints.trend, filters), controller.signal),
        ...rankingKinds.map((kind) =>
          fetchJson(endpointUrl(endpoints.rankings, filters, { kind }), controller.signal)
        ),
        fetchJson(
          endpointUrl(endpoints.events, filters, { cursor: cursorStack[pageIndex] }),
          controller.signal
        ),
      ]);
      if (
        activeController !== controller ||
        activeGeneration !== generation ||
        controller.signal.aborted
      ) {
        return;
      }
      payloads.forEach(rowsAsObjects);
      renderSummary(payloads[0]);
      renderTrend(payloads[1]);
      rankingKinds.forEach((kind, index) => renderRanking(kind, payloads[index + 2]));
      renderEvents(payloads[payloads.length - 1]);
      showSuccess();
    } catch (error) {
      if (
        error.name !== "AbortError" &&
        activeController === controller &&
        activeGeneration === generation
      ) {
        controller.abort();
        showFailure(error);
      }
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (form.reportValidity()) {
      loadDashboard({ resetPagination: true });
    }
  });
  form.addEventListener("change", () => {
    if (form.reportValidity()) {
      loadDashboard({ resetPagination: true });
    }
  });
  document.querySelector("#reset-filters").addEventListener("click", () => {
    form.reset();
    loadDashboard({ resetPagination: true });
  });
  retry.addEventListener("click", () => loadDashboard());
  previousPage.addEventListener("click", () => {
    if (pageIndex > 0) {
      pageIndex -= 1;
      loadDashboard();
    }
  });
  nextPage.addEventListener("click", () => {
    if (cursorStack[pageIndex + 1]) {
      pageIndex += 1;
      loadDashboard();
    }
  });

  populateForm(filtersFromLocation());
  loadDashboard({ resetPagination: true });
})();
