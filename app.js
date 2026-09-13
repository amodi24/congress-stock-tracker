(function () {
  const PAGE_SIZE = 50;
  const GOATCOUNTER_CODE = "congress-stock-tracker";

  const state = {
    trades: [],
    filtered: [],
    query: "",
    sourceFilter: "all",
    typeFilter: "all",
    sortKey: "transaction_date_iso",
    sortDir: "desc",
    page: 1,
  };

  const els = {
    statCount: document.getElementById("stat-count"),
    statSenators: document.getElementById("stat-senators"),
    statInsiders: document.getElementById("stat-insiders"),
    statCompanies: document.getElementById("stat-companies"),
    statUpdated: document.getElementById("stat-updated"),
    statVisitsTile: document.getElementById("stat-visits-tile"),
    statVisits: document.getElementById("stat-visits"),
    search: document.getElementById("search"),
    sourceChips: document.querySelectorAll(".filter-chip[data-source]"),
    typeChips: document.querySelectorAll(".filter-chip[data-type]"),
    resultCount: document.getElementById("result-count"),
    tbody: document.getElementById("trades-body"),
    pagination: document.getElementById("pagination"),
    headers: document.querySelectorAll("th[data-sort]"),
  };

  function senateTypeBucket(transactionType) {
    const t = (transactionType || "").toLowerCase();
    if (t.startsWith("purchase")) return "purchase";
    if (t.startsWith("sale")) return "sale";
    return "other";
  }

  function insiderTypeBucket(code) {
    if (code === "P") return "purchase";
    if (code === "S") return "sale";
    return "other";
  }

  function typePillClass(bucket) {
    if (bucket === "purchase") return "buy";
    if (bucket === "sale") return "sell";
    return "other";
  }

  function formatDate(iso) {
    if (!iso) return "—";
    const [y, m, d] = iso.split("-");
    return `${m}/${d}/${y}`;
  }

  function researchLinks(ticker) {
    const q = encodeURIComponent(ticker);
    return [
      { label: "SEC filings", href: `https://www.sec.gov/edgar/search/#/q=${q}` },
      { label: "Yahoo Finance", href: `https://finance.yahoo.com/quote/${q}` },
      { label: "News search", href: `https://news.google.com/search?q=${q}%20stock&hl=en-US&gl=US&ceid=US:en` },
    ];
  }

  function tickerCell(ticker) {
    const safeTicker = escapeHtml(ticker);
    const links = researchLinks(ticker)
      .map((l) => `<a href="${l.href}" target="_blank" rel="noopener">${l.label}</a>`)
      .join("");
    return `<span class="ticker-text">${safeTicker}</span><details class="research" name="research">
      <summary aria-label="Research ${safeTicker}" title="Research ${safeTicker}">
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M13.6 12.2a6 6 0 1 0-1.4 1.4l4 4 1.4-1.4-4-4ZM8 12a4 4 0 1 1 0-8 4 4 0 0 1 0 8Z"/></svg>
      </summary>
      <div class="research-menu">${links}</div>
    </details>`;
  }

  function formatUsd(value) {
    if (value === null || value === undefined) return "—";
    return "$" + Math.round(value).toLocaleString("en-US");
  }

  function normalizeSenateTrade(t) {
    return {
      source: "senate",
      source_label: "U.S. Senator",
      filer_name: `${t.senator_first} ${t.senator_last}`,
      ticker: t.ticker,
      company: t.asset_name,
      role: t.owner,
      transaction_date_iso: t.transaction_date_iso,
      filing_date_iso: t.filing_date_iso,
      type_label: t.transaction_type,
      type_bucket: senateTypeBucket(t.transaction_type),
      amount_display: t.amount_range,
      amount_min: t.amount_min,
    };
  }

  function normalizeInsiderTrade(t) {
    return {
      source: "insider",
      source_label: "Company Insider",
      filer_name: t.reporting_owner,
      ticker: t.ticker,
      company: t.issuer_name,
      role: t.role,
      transaction_date_iso: t.transaction_date_iso,
      filing_date_iso: t.filing_date_iso,
      type_label: t.transaction_code_label,
      type_bucket: insiderTypeBucket(t.transaction_code),
      amount_display: formatUsd(t.value_usd),
      amount_min: t.value_usd,
    };
  }

  function applyFilters() {
    const q = state.query.trim().toLowerCase();
    state.filtered = state.trades.filter((t) => {
      if (state.sourceFilter !== "all" && t.source !== state.sourceFilter) return false;
      if (state.typeFilter !== "all" && t.type_bucket !== state.typeFilter) return false;
      if (!q) return true;
      const haystack = `${t.filer_name} ${t.ticker || ""} ${t.company}`.toLowerCase();
      return haystack.includes(q);
    });
    sortFiltered();
    state.page = 1;
    render();
  }

  function sortFiltered() {
    const key = state.sortKey;
    const dir = state.sortDir === "asc" ? 1 : -1;
    state.filtered.sort((a, b) => {
      let av = a[key];
      let bv = b[key];
      if (av === null || av === undefined) av = "";
      if (bv === null || bv === undefined) bv = "";
      if (typeof av === "number" || typeof bv === "number") {
        av = av === "" ? -Infinity : av;
        bv = bv === "" ? -Infinity : bv;
        return (av - bv) * dir;
      }
      return String(av).localeCompare(String(bv)) * dir;
    });
  }

  function render() {
    renderResultCount();
    renderRows();
    renderPagination();
    renderSortIndicators();
  }

  function renderResultCount() {
    const total = state.filtered.length;
    els.resultCount.textContent =
      total === state.trades.length
        ? `${total.toLocaleString()} transactions`
        : `${total.toLocaleString()} of ${state.trades.length.toLocaleString()} transactions`;
  }

  function renderRows() {
    const start = (state.page - 1) * PAGE_SIZE;
    const pageRows = state.filtered.slice(start, start + PAGE_SIZE);

    if (pageRows.length === 0) {
      els.tbody.innerHTML = `<tr class="empty-row"><td colspan="8">No trades match your search.</td></tr>`;
      return;
    }

    els.tbody.innerHTML = pageRows
      .map((t) => {
        return `<tr class="source-${t.source}">
          <td class="cell-date">${formatDate(t.transaction_date_iso)}</td>
          <td class="cell-senator">
            <span>${escapeHtml(t.filer_name)}</span>
            <span class="source-tag">${escapeHtml(t.source_label)}</span>
          </td>
          <td class="cell-ticker">${t.ticker ? tickerCell(t.ticker) : "—"}</td>
          <td class="cell-asset">${escapeHtml(t.company || "—")}</td>
          <td><span class="type-pill ${typePillClass(t.type_bucket)}">${escapeHtml(t.type_label || "—")}</span></td>
          <td class="cell-amount">${escapeHtml(t.amount_display || "—")}</td>
          <td class="cell-owner">${escapeHtml(t.role || "—")}</td>
          <td class="cell-date">${formatDate(t.filing_date_iso)}</td>
        </tr>`;
      })
      .join("");
  }

  function renderPagination() {
    const totalPages = Math.max(1, Math.ceil(state.filtered.length / PAGE_SIZE));
    if (state.page > totalPages) state.page = totalPages;
    els.pagination.innerHTML = `
      <button type="button" id="page-prev" ${state.page <= 1 ? "disabled" : ""}>Previous</button>
      <span>Page ${state.page} of ${totalPages}</span>
      <button type="button" id="page-next" ${state.page >= totalPages ? "disabled" : ""}>Next</button>
    `;
    const prev = document.getElementById("page-prev");
    const next = document.getElementById("page-next");
    if (prev) prev.addEventListener("click", () => { state.page--; render(); window.scrollTo({ top: 0, behavior: "smooth" }); });
    if (next) next.addEventListener("click", () => { state.page++; render(); window.scrollTo({ top: 0, behavior: "smooth" }); });
  }

  function renderSortIndicators() {
    els.headers.forEach((th) => {
      th.classList.remove("is-sorted-asc", "is-sorted-desc");
      if (th.dataset.sort === state.sortKey) {
        th.classList.add(state.sortDir === "asc" ? "is-sorted-asc" : "is-sorted-desc");
      }
    });
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function renderStats(trades, lastRunDates) {
    els.statCount.textContent = trades.length.toLocaleString();

    const senators = new Set(trades.filter((t) => t.source === "senate").map((t) => t.filer_name));
    els.statSenators.textContent = senators.size.toLocaleString();

    const insiders = new Set(trades.filter((t) => t.source === "insider").map((t) => t.filer_name));
    els.statInsiders.textContent = insiders.size.toLocaleString();

    const companies = new Set(trades.map((t) => t.ticker).filter(Boolean));
    els.statCompanies.textContent = companies.size.toLocaleString();

    const latest = lastRunDates.filter(Boolean).sort().pop();
    if (latest) {
      const d = new Date(latest);
      els.statUpdated.textContent = d.toLocaleString("en-US", {
        timeZone: "America/New_York",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }) + " ET";
    }
  }

  function wireControls() {
    els.search.addEventListener("input", (e) => {
      state.query = e.target.value;
      applyFilters();
    });

    els.sourceChips.forEach((chip) => {
      chip.addEventListener("click", () => {
        els.sourceChips.forEach((c) => c.classList.remove("is-active"));
        chip.classList.add("is-active");
        state.sourceFilter = chip.dataset.source;
        applyFilters();
      });
    });

    els.typeChips.forEach((chip) => {
      chip.addEventListener("click", () => {
        els.typeChips.forEach((c) => c.classList.remove("is-active"));
        chip.classList.add("is-active");
        state.typeFilter = chip.dataset.type;
        applyFilters();
      });
    });

    els.headers.forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (state.sortKey === key) {
          state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
        } else {
          state.sortKey = key;
          state.sortDir = key === "filer_name" ? "asc" : "desc";
        }
        sortFiltered();
        state.page = 1;
        render();
      });
    });
  }

  async function fetchJson(path, fallback) {
    try {
      const res = await fetch(path);
      if (!res.ok) return fallback;
      return await res.json();
    } catch (err) {
      return fallback;
    }
  }

  async function loadVisitCount() {
    if (GOATCOUNTER_CODE === "YOUR-GOATCOUNTER-CODE") return;
    try {
      const res = await fetch(`https://${GOATCOUNTER_CODE}.goatcounter.com/counter//.json`);
      // GoatCounter returns a 404 status (not 200) for a path with zero
      // recorded views so far, but still includes a valid {count: "0"}
      // body -- so parse the JSON regardless of res.ok rather than
      // bailing on status code alone.
      const data = await res.json();
      if (data.count === undefined) return;
      els.statVisits.textContent = data.count;
      els.statVisitsTile.hidden = false;
    } catch (err) {
      // GoatCounter unreachable, CORS-blocked, or malformed response -- fail quietly.
    }
  }

  async function init() {
    wireControls();
    loadVisitCount();
    try {
      const [senateTrades, senateState, insiderTrades, insiderState] = await Promise.all([
        fetchJson("data/trades.json", []),
        fetchJson("data/state.json", null),
        fetchJson("data/insider_trades.json", []),
        fetchJson("data/insider_state.json", null),
      ]);

      state.trades = [
        ...senateTrades.map(normalizeSenateTrade),
        ...insiderTrades.map(normalizeInsiderTrade),
      ];

      renderStats(state.trades, [
        senateState && senateState.last_run_utc,
        insiderState && insiderState.last_run_utc,
      ]);
      applyFilters();
    } catch (err) {
      els.tbody.innerHTML = `<tr class="empty-row"><td colspan="8">Couldn't load trade data. Try refreshing.</td></tr>`;
      console.error(err);
    }
  }

  init();
})();
