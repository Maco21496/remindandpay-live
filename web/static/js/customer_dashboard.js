// /static/js/customer_dashboard.js
(function () {
  /* ============================================================
   * Shared helpers
   * ============================================================ */
  const fmtMoney = (n) => {
    try { return (window.AppCurrency && AppCurrency.format) ? AppCurrency.format(n) : ("£" + Number(n||0).toFixed(2)); }
    catch { return "£" + Number(n||0).toFixed(2); }
  };
  const fmtDate =
    (iso) =>
      (window.AppDate && AppDate.formatDate) ? AppDate.formatDate(iso) : (iso || "");

  const bootEl = document.getElementById("cust-boot");
  const CUSTOMER_ID = Number(bootEl?.dataset.id || 0);

  /* ============================================================
   * ---- customer balance & aging (single row) ----
   * Fills the row under “Balance & aging” for this customer
   * using /api/dashboard/customers-aging and selecting this id.
   * ============================================================ */
  async function renderCustomerAgingRow(customerId) {
    if (!customerId) return;
    try {
      const r = await fetch("/api/dashboard/customers-aging");
      if (!r.ok) throw new Error(String(r.status));
      const items = await r.json();
      const row = items.find((x) => Number(x.customer_id) === Number(customerId));
      const set = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = fmtMoney(val);
      };

      if (row) {
        set("cust_tot_owed", Number(row.total || 0));
        set("cust_due_now", Number(row.due_now || 0));
        set("cust_b0", Number(row.b0_30 || 0));
        set("cust_b31", Number(row.b31_60 || 0));
        set("cust_b61", Number(row.b61_90 || 0));
        set("cust_b90", Number(row.b90p || 0));
        const nameEl = document.getElementById("cust_name");
        if (nameEl && row.customer_name) nameEl.textContent = row.customer_name;
      } else {
        ["cust_tot_owed", "cust_due_now", "cust_b0", "cust_b31", "cust_b61", "cust_b90"].forEach((id) => set(id, 0));
      }
    } catch {
      // leave defaults silently
    }
  }

  /* ============================================================
   * ---- customer transactions (ledger) ----
   * Keeps the existing interactive table with grouping and paging
   * ============================================================ */
  const rowsEl = document.getElementById("txn_rows");
  const emptyEl = document.getElementById("txn_empty");
  const tabsEl = document.getElementById("txn_filters");

  const perSel = document.getElementById("txn_per");
  const prevBtn = document.getElementById("txn_prev");
  const nextBtn = document.getElementById("txn_next");
  const pageInfo = document.getElementById("txn_page_info");

  const pager = { page: 1, per: Number(perSel?.value || 20), pages: 1, total: 0 };

  let allItems = [];          // raw API items (newest-first for this page)
  let openingBalance = 0;     // balance BEFORE the oldest row on this page
  let filter = "all";
  const openGroups = new Set(); // remember which payment groups are expanded

  // authoritative paid-to-date per invoice_id from the API (customer-wide)
  let paidMap = new Map();     // invoice_id -> paid_to_date

  // map of rowKey -> balanceAfterRow, computed from ALL rows on the page
  const balanceMap = new Map();

  function updatePagerUI() {
    if (perSel) perSel.value = String(pager.per);
    if (pageInfo) pageInfo.textContent = `Page ${pager.page} / ${pager.pages} (${pager.total} items)`;
    if (prevBtn) prevBtn.disabled = pager.page <= 1;
    if (nextBtn) nextBtn.disabled = pager.page >= pager.pages;
  }

  function invNo(str) {
    const m = String(str || "").match(/inv(?:oice)?\s*#?\s*([A-Za-z0-9-]+)/i);
    return m ? m[1] : null;
  }

  // Build a view that groups payment allocations into a single "payment_group" row.
  function buildView(items) {
    const out = [];
    const groups = new Map(); // key -> group

    items.forEach((x, idx) => {
      const debit = Number(x.debit || 0);
      const credit = Number(x.credit || 0);

      if (credit > 0) {
        // Try to group allocations by payment; fall back to ref|date combo
        const key = (x.payment_id || "") + "|" + (x.ref || "") + "|" + (x.dt || "");
        let g = groups.get(key);
        if (!g) {
          g = { kind: "payment_group", key, idx, dt: x.dt, ref: x.ref || "PAY", total: 0, items: [] };
          groups.set(key, g);
          out.push(g);
        }
        g.items.push(x);
        g.total += credit;
      } else {
        out.push({ kind: "row", idx, data: x }); // invoice or other debit/credit row
      }
    });

    // Keep the input order (items are already newest-first); idx preserves that.
    out.sort((a, b) => a.idx - b.idx);
    return out;
  }

  // Compute balances for ALL rows on the page using openingBalance.
  function computeBalancesForPage(viewAll) {
    balanceMap.clear();

    // Walk OLDEST->NEWEST within this page (reverse of how we display)
    const asc = [...viewAll].reverse();
    let bal = Number(openingBalance || 0);

    asc.forEach((v) => {
      if (v.kind === "row") {
        const x = v.data;
        const d = Number(x.debit || 0);
        const c = Number(x.credit || 0);
        bal = bal + d - c;
        balanceMap.set(`r|${v.idx}`, bal);
      } else if (v.kind === "payment_group") {
        const delta = -Number(v.total || 0);
        bal = bal + delta;
        balanceMap.set(`g|${v.key}`, bal);
      }
    });
  }

  function apply() {
    if (!rowsEl) return;

    // Build full view and precompute balances across ALL rows (no filter)
    const viewAll = buildView(allItems);

    computeBalancesForPage(viewAll);

    // Now build the filtered view for rendering
    let toRender = viewAll.filter((v) => {
      if (filter === "invoice") return v.kind === "row" && Number(v.data?.debit || 0) > 0;
      if (filter === "payment") return v.kind === "payment_group";
      return true; // 'all'
    });

    if (!toRender.length) {
      rowsEl.innerHTML = "";
      if (emptyEl) emptyEl.style.display = "block";
      return;
    }
    if (emptyEl) emptyEl.style.display = "none";

    const html = [];
    const EPS = 1e-6;

    toRender.forEach((v) => {
      if (v.kind === "row") {
        const x = v.data;
        const d = Number(x.debit || 0);
        const c = Number(x.credit || 0);

        let badge = "";
        if (d > 0) {
          // authoritative paid-to-date by invoice_id from API
          const invId = Number(x.invoice_id || 0);
          const paid = invId ? (paidMap.get(invId) || 0) : 0;

          if (paid <= 0 + EPS) badge = `<span class="badge badge--warn">unpaid</span>`;
          else if (paid < d - EPS) badge = `<span class="badge badge--amber">part-paid</span>`;
          else badge = `<span class="badge badge--ok">paid</span>`;
        }

        const bal = balanceMap.get(`r|${v.idx}`);
        html.push(`
          <tr>
            <td>${fmtDate(x.dt)}</td>
            <td>${x.ref || ""}</td>
            <td>${(x.desc || "")} ${badge}</td>
            <td style="text-align:right;">${d ? fmtMoney(d) : ""}</td>
            <td style="text-align:right;">${c ? fmtMoney(c) : ""}</td>
            <td style="text-align:right;"><strong>${fmtMoney(bal)}</strong></td>
          </tr>
        `);
        return;
      }

      const isOpen = openGroups.has(v.key);
      const bal = balanceMap.get(`g|${v.key}`);
      html.push(`
        <tr class="is-payment">
          <td>${fmtDate(v.dt)}</td>
          <td>${v.ref || "PAY"}</td>
          <td>
            <button class="link js-toggle" data-key="${v.key}" aria-expanded="${isOpen ? "true" : "false"}">
              ${isOpen ? "▾" : "▸"} Payment (${v.items.length} allocations)
            </button>
          </td>
          <td></td>
          <td style="text-align:right;">${fmtMoney(v.total)}</td>
          <td style="text-align:right;"><strong>${fmtMoney(bal)}</strong></td>
        </tr>
      `);

      if (isOpen) {
        v.items.forEach((it) => {
          const num = invNo(it.desc) || "";
          const amt = Number(it.credit || 0);
          html.push(`
            <tr class="alloc">
              <td></td>
              <td></td>
              <td class="muted">↳ Payment → ${num ? `Invoice ${num}` : (it.desc || "")}</td>
              <td></td>
              <td style="text-align:right;">${fmtMoney(amt)}</td>
              <td></td>
            </tr>
          `);
        });
      }
    });

    rowsEl.innerHTML = html.join("");
  }

  async function loadTransactions(customerId) {
    if (!customerId) return;
    const url = `/api/dashboard/customer-transactions?customer_id=${encodeURIComponent(customerId)}&page=${pager.page}&per_page=${pager.per}`;
    if (rowsEl) rowsEl.innerHTML = `<tr><td colspan="6" class="muted">Loading…</td></tr>`;
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();
      allItems = data.items || [];
      openingBalance = Number(data.opening_balance || 0);
      pager.page = data.page;
      pager.per = data.per_page;
      pager.pages = data.pages;
      pager.total = data.total;

      // capture server-provided paid_to_date per invoice_id
      paidMap = new Map(
        Object.entries(data.paid_map || {}).map(([k, v]) => [Number(k), Number(v) || 0]),
      );

      updatePagerUI();
    } catch {
      allItems = [];
      openingBalance = 0;
      pager.pages = 1;
      pager.total = 0;
      updatePagerUI();
      paidMap = new Map();
    }
    apply();
  }

  window.refreshCustomerTransactions = async () => {
    if (CUSTOMER_ID) await loadTransactions(CUSTOMER_ID);
  };

  // tx filter tabs
  if (tabsEl) {
    tabsEl.addEventListener("click", (e) => {
      const t = e.target.closest(".tab");
      if (!t) return;
      tabsEl.querySelectorAll(".tab").forEach((b) => b.classList.remove("is-active"));
      t.classList.add("is-active");
      filter = t.dataset.filter || "all";
      apply();
    });
  }

  // Toggle allocation sub-rows
  rowsEl?.addEventListener("click", (e) => {
    const btn = e.target.closest(".js-toggle");
    if (!btn) return;
    const key = btn.dataset.key;
    if (openGroups.has(key)) openGroups.delete(key);
    else openGroups.add(key);
    apply();
  });

  // Boot everything on this page
  document.addEventListener("DOMContentLoaded", async () => {
    updatePagerUI();
    if (typeof setActiveCustomerTab === "function") setActiveCustomerTab(CUSTOMER_ID);

    // NOTE: no loadSummary() here (you’re removing the KPI strip)
    await renderCustomerAgingRow(CUSTOMER_ID);
    await loadTransactions(CUSTOMER_ID);
  });

  // Pager controls
  perSel?.addEventListener("change", () => {
    pager.per = Number(perSel.value) || 20;
    pager.page = 1;
    openGroups.clear();
    window.refreshCustomerTransactions();
  });
  prevBtn?.addEventListener("click", () => {
    if (pager.page > 1) {
      pager.page -= 1;
      openGroups.clear();
      window.refreshCustomerTransactions();
    }
  });
  nextBtn?.addEventListener("click", () => {
    if (pager.page < pager.pages) {
      pager.page += 1;
      openGroups.clear();
      window.refreshCustomerTransactions();
    }
  });
})();
