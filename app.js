(function () {
  const PAGE_SIZE = 50;

  const state = {
    trades: [],
    filtered: [],
    query: "",
    typeFilter: "all",
    sortKey: "transaction_date_iso",
    sortDir: "desc",
    page: 1,
  };

  const els = {
    statCount: document.getElementById("stat-count"),
    statSenators: document.getElementById("stat-senators"),
    statRange: document.getElementById("stat-range"),
    statUpdated: document.getElementById("stat-updated"),
    search: document.getElementById("search"),
    filterChips: document.querySelectorAll(".filter-chip"),
    resultCount: document.getElementById("result-count"),
    tbody: document.getElementById("trades-body"),
    pagination: document.getElementById("pagination"),
    headers: document.querySelectorAll("th[data-sort]"),
  };

  function typeBucket(transactionType) {
    const t = transactionType.toLowerCase();
    if (t.startsWith("purchase")) return "purchase";
    if (t.startsWith("sale")) return "sale";
    if (t.startsWith("exchange")) return "exchange";
    return "other";
  }

  function typePillClass(bucket) {
    if (bucket === "purchase") return "buy";
    if (bucket === "sale") return "sell";
    return "exchange";
  }

  function formatDate(iso) {
    if (!iso) return "—";
    const [y, m, d] = iso.split("-");
    return `${m}/${d}/${y}`;
  }

  function applyFilters() {
    const q = state.query.trim().toLowerCase();
    state.filtered = state.trades.filter((t) => {
      if (state.typeFilter !== "all" && typeBucket(t.transaction_type) !== state.typeFilter) {
        return false;
      }
      if (!q) return true;
      const haystack = `${t.senator_first} ${t.senator_last} ${t.ticker || ""} ${t.asset_name}`.toLowerCase();
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
        const bucket = typeBucket(t.transaction_type);
        return `<tr>
          <td class="cell-date">${formatDate(t.transaction_date_iso)}</td>
          <td class="cell-senator">${escapeHtml(t.senator_first)} ${escapeHtml(t.senator_last)}</td>
          <td class="cell-ticker">${t.ticker ? escapeHtml(t.ticker) : "—"}</td>
          <td class="cell-asset">${escapeHtml(t.asset_name)}</td>
          <td><span class="type-pill ${typePillClass(bucket)}">${escapeHtml(t.transaction_type)}</span></td>
          <td class="cell-amount">${escapeHtml(t.amount_range)}</td>
          <td class="cell-owner">${escapeHtml(t.owner)}</td>
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

  function renderStats(trades, meta) {
    els.statCount.textContent = trades.length.toLocaleString();

    const senators = new Set(trades.map((t) => `${t.senator_first} ${t.senator_last}`));
    els.statSenators.textContent = senators.size.toLocaleString();

    const dates = trades.map((t) => t.transaction_date_iso).filter(Boolean).sort();
    if (dates.length) {
      els.statRange.textContent = `${formatDate(dates[0])} – ${formatDate(dates[dates.length - 1])}`;
    }

    if (meta && meta.last_run_utc) {
      const d = new Date(meta.last_run_utc);
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

    els.filterChips.forEach((chip) => {
      chip.addEventListener("click", () => {
        els.filterChips.forEach((c) => c.classList.remove("is-active"));
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
          state.sortDir = key === "senator_last" ? "asc" : "desc";
        }
        sortFiltered();
        state.page = 1;
        render();
      });
    });
  }

  async function init() {
    wireControls();
    try {
      const [tradesRes, stateRes] = await Promise.all([
        fetch("data/trades.json"),
        fetch("data/state.json"),
      ]);
      state.trades = await tradesRes.json();
      const meta = stateRes.ok ? await stateRes.json() : null;
      renderStats(state.trades, meta);
      applyFilters();
    } catch (err) {
      els.tbody.innerHTML = `<tr class="empty-row"><td colspan="8">Couldn't load trade data. Try refreshing.</td></tr>`;
      console.error(err);
    }
  }

  init();
})();
