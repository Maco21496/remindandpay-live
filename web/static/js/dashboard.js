// /static/js/dashboard.js
const API = '';

function fmtMoney(n){
  try { return (window.AppCurrency && AppCurrency.format) ? AppCurrency.format(n) : ('£' + Number(n||0).toFixed(2)); }
  catch { return '£' + Number(n||0).toFixed(2); }
}
function getCustomerId(){
  // Prefer the boot div if present (customer page)
  const boot = document.getElementById('cust-boot');
  if (boot && boot.dataset && boot.dataset.id) return Number(boot.dataset.id);
  // Legacy globals (if any)
  if (typeof window !== 'undefined' && window.__IC_CUSTOMER_ID__) return Number(window.__IC_CUSTOMER_ID__);
  return null;
}

/* ---------- KPIs / summary ---------- */
async function loadSummary(customerId){
  const qs = customerId ? ('?customer_id=' + encodeURIComponent(customerId)) : '';
  const res = await fetch(API + '/api/dashboard/summary' + qs);
  if(!res.ok) return;
  const s = await res.json();

  // Totals
  const g = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  g('kpi-outstanding', fmtMoney(s.outstanding_total));
  g('kpi-overdue',     fmtMoney(s.overdue));
  g('kpi-duesoon',     fmtMoney(s.due_soon));
  g('kpi-paidmonth',   fmtMoney(s.paid_this_month));

  // Aging buckets
  const ag = s.aging || {};
  g('aging-0-30',  fmtMoney(ag['0_30']));
  g('aging-31-60', fmtMoney(ag['31_60']));
  g('aging-61-90', fmtMoney(ag['61_90']));
  g('aging-90p',   fmtMoney(ag['90p']));

  // Counts (guards for customer page where some elements aren’t present)
  const c = s.counts || {};
  const plural = (n, w) => `${n || 0} ${w}${(n||0) === 1 ? '' : 's'}`;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set('stat-customers',      plural(c.customers, 'customer'));
  set('stat-open-invoices',  plural(c.open_invoices, 'open invoice'));
  set('stat-overdue-count',  `${c.overdue || 0} overdue`);
  set('stat-duesoon-count',  `${c.due_soon || 0} due this week`);
}

/* ---------- Customers with balances (main dashboard only) ---------- */
async function loadCustomerAging(){
  const rows  = document.getElementById('cust_rows');
  const empty = document.getElementById('cust_rows_empty');
  const foot  = document.getElementById('cust_totals');
  if (!rows || !empty || !foot) return;

  const r = await fetch(API + '/api/dashboard/customers-aging');
  if (!r.ok){
    rows.innerHTML = '<tr><td colspan="7">Failed to load</td></tr>';
    empty.style.display = 'none';
    foot.innerHTML = '';
    return;
  }

  const items = await r.json();
  if (!items.length){
    rows.innerHTML = '';
    foot.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  // Sort by total owed (desc)
  items.sort((a,b)=> (b.total||0) - (a.total||0));

  // Running totals
  let sumTotal = 0, sOver=0, s0=0, s31=0, s61=0, s90=0;

  rows.innerHTML = items.map(x => {
    const tot = Number(x.total   || 0);
    const b0  = Number(x.b0_30  || 0);
    const b31 = Number(x.b31_60 || 0);
    const b61 = Number(x.b61_90 || 0);
    const b90 = Number(x.b90p   || 0);
    const totalOver = Number(x.due_now != null ? x.due_now : (b0 + b31 + b61 + b90));

    sumTotal += tot; sOver += totalOver; s0 += b0; s31 += b31; s61 += b61; s90 += b90;

    return `
      <tr>
        <td><a href="/customers/${x.customer_id}" target="_blank" rel="noopener" class="link customer-link" data-cid="${x.customer_id}">${x.customer_name}</a></td>
        <td style="text-align:right;">${fmtMoney(tot)}</td>
        <td style="text-align:right; color:#b42318; font-weight:600;">${fmtMoney(totalOver)}</td>
        <td style="text-align:right;">${fmtMoney(b0)}</td>
        <td style="text-align:right;">${fmtMoney(b31)}</td>
        <td style="text-align:right;">${fmtMoney(b61)}</td>
        <td style="text-align:right;">${fmtMoney(b90)}</td>
      </tr>
    `;
  }).join('');

  foot.innerHTML = `
    <tr>
      <th>Totals</th>
      <th style="text-align:right;">${fmtMoney(sumTotal)}</th>
      <th style="text-align:right; color:#b42318;">${fmtMoney(sOver)}</th>
      <th style="text-align:right;">${fmtMoney(s0)}</th>
      <th style="text-align:right;">${fmtMoney(s31)}</th>
      <th style="text-align:right;">${fmtMoney(s61)}</th>
      <th style="text-align:right;">${fmtMoney(s90)}</th>
    </tr>
  `;
}

// --- date helper (now delegates to global formatter) ---
function fmtDateShort(iso) {
  if (!iso) return "";
  // Use the global helper that respects Settings (en-GB / en-US)
  if (window.AppDate && typeof AppDate.formatDate === "function") {
    return AppDate.formatDate(iso);
  }
  // Fallback if the bootstrap didn't run for some reason
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleDateString("en-GB"); // harmless default
}


async function loadCustomerInvoices(customerId, filter = "all") {
  // Decide server status filter
  // - "overdue" -> only overdue
  // - everything else -> all OPEN (includes overdue)
  const status = (filter === "overdue") ? "overdue" : "open";

  const url = `/api/dashboard/customer-invoices?customer_id=${encodeURIComponent(
    customerId
  )}&status=${encodeURIComponent(status)}&limit=500`;

  const tbody = document.getElementById("cust_inv_rows");
  const empty = document.getElementById("cust_inv_empty");
  if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="muted">Loading…</td></tr>`;
  if (empty) empty.style.display = "none";

  try {
    const r = await fetch(url);
    if (!r.ok) throw new Error(String(r.status));
    let items = await r.json();

    // Client-side date window filters for "30" / "60"
    if (filter === "30" || filter === "60") {
      const days = filter === "30" ? 30 : 60;
      const cutoff = new Date();
      cutoff.setDate(cutoff.getDate() - days);
      items = items.filter((x) => {
        if (!x.issue_date) return false;
        const dt = new Date(x.issue_date);
        return dt >= cutoff;
      });
    }

    if (!items.length) {
      if (tbody) tbody.innerHTML = "";
      if (empty) empty.style.display = "block";
      return;
    }

    // Sort oldest -> newest so the running balance flows naturally
    items.sort((a, b) => new Date(a.issue_date) - new Date(b.issue_date));

    // amount_due from this API is the REMAINING balance for open/overdue
    // (for a statement later we can switch to gross amounts).
    let running = 0;

    if (tbody) {
      tbody.innerHTML = items
        .map((x) => {
          const invDate = fmtDateShort(x.issue_date);
          const dueDate = fmtDateShort(x.due_date);
          const base = Number(x.amount_due || 0);               // remaining
          const signed = (x.kind === "credit_note") ? -base : base; // future-proof: treat CR notes as negative
          running += signed;

          const days = Number(x.days_overdue || 0);
          const statusBadge = x.status || "";

          return `
            <tr>
              <td>${invDate || "-"}</td>
              <td>${String(x.invoice_number || "")}</td>
              <td style="text-align:right;">�${fmtMoney(signed)}</td>
              <td>${dueDate || "-"}</td>
              <td style="text-align:right;">${days}</td>
              <td><span class="pill ${statusBadge}">${statusBadge}</span></td>
              <td style="text-align:right;">�${fmtMoney(running)}</td>
            </tr>
          `;
        })
        .join("");
    }
    if (empty) empty.style.display = "none";
  } catch (e) {
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty">Failed to load invoices</td></tr>`;
    }
    if (empty) empty.style.display = "none";
  }
}

/* ---------- Populate customer dropdown (main dashboard only) ---------- */
async function populateCustomerFilter(){
  const sel = document.getElementById('dash_customer_filter');
  if (!sel) return;

  try {
    const r = await fetch(API + '/api/customers');
    if (!r.ok) return;
    const list = await r.json();
    list
      .filter(c => c && c.id && c.name)
      .sort((a,b)=> String(a.name).localeCompare(String(b.name)))
      .forEach(c => {
        const o = document.createElement('option');
        o.value = c.id;
        o.textContent = c.name;
        sel.appendChild(o);
      });
  } catch {}
}

/* ---------- Events ---------- */
document.addEventListener('change', (e)=>{
  if (e.target?.id === 'dash_customer_filter'){
    const id = e.target.value;
    if (id){
      const name = e.target.options[e.target.selectedIndex]?.text || `Customer #${id}`;
      if (typeof openCustomerTab === 'function') {
        openCustomerTab(Number(id), name);
      } else {
        // graceful fallback
        window.location.href = `/customers/${encodeURIComponent(id)}`;
      }
    } else {
      if (typeof setActiveCustomerTab === 'function') setActiveCustomerTab('');
      window.location.href = '/dashboard';
    }
  }
});

// If the tabbed customer view is available, intercept clicks and open there.
document.addEventListener('click', (e)=>{
  const a = e.target.closest('a.customer-link');
  if (!a) return;
  if (typeof openCustomerTab === 'function'){
    e.preventDefault();
    const id = Number(a.dataset.cid || 0);
    const name = (a.textContent || '').trim();
    if (id) openCustomerTab(id, name || `Customer #${id}`);
  }
});

/* ---------- load customer invoices ---------- */
document.addEventListener("click", (ev) => {
  const btn = ev.target.closest("#cust_inv_filters .tab");
  if (!btn) return;

  // visual active state
  const all = btn.parentElement.querySelectorAll(".tab");
  all.forEach((el) => el.classList.remove("is-active"));
  btn.classList.add("is-active");

  const filter = btn.dataset.filter || "all";
  const id = window.__IC_CUSTOMER_ID__;
  if (id) loadCustomerInvoices(id, filter);
});

/* ---------- Weekly chart (interactive) ---------- */
(function () {
  const chartEl = document.getElementById("wk_chart");
  if (!chartEl) return;

  const customerId = getCustomerId();

  let metric = "issued";        // issued | received
  let weeks = 26;               // 1..104
  let showCount = true;         // overlay invoice count (issued only)

  const btnIssued    = document.getElementById("wk_btn_issued");
  const btnReceived  = document.getElementById("wk_btn_received");
  const quick        = document.getElementById("wk_quick");
  const inputWeeks   = document.getElementById("wk_input");
  const toggleCount  = document.getElementById("wk_toggle_cnt");

  function setMoney(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = fmtMoney(Number(val || 0));
  }

  function setActiveQuick() {
    if (!quick) return;
    const btns = quick.querySelectorAll("button[data-wk]");
    btns.forEach((b) => b.classList.toggle("is-active", Number(b.dataset.wk) === Number(weeks)));
  }

  function activate(which) {
    metric = which;
    if (btnIssued && btnReceived) {
      btnIssued.classList.toggle("is-active", which === "issued");
      btnReceived.classList.toggle("is-active", which === "received");
    }
    if (which === "received") showCount = false; // counts only exist for issued
    if (toggleCount) toggleCount.checked = showCount;
    load();
  }

  async function load() {
    try {
      let url = `/api/dashboard/sales-weekly?weeks=${encodeURIComponent(weeks)}&metric=${encodeURIComponent(metric)}`;
      if (customerId) url += `&customer_id=${encodeURIComponent(customerId)}`;
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      drawBars(d.points || [], metric, showCount);

      setMoney("wk_total", d.sum_total);
      const avgInv = d.avg_invoice_amount != null ? fmtMoney(d.avg_invoice_amount) : "—";
      const avgCnt = d.avg_invoices_per_week != null ? String(d.avg_invoices_per_week) : "—";
      const a1 = document.getElementById("wk_avg_inv"); if (a1) a1.textContent = avgInv;
      const a2 = document.getElementById("wk_avg_cnt"); if (a2) a2.textContent = avgCnt;
    } catch (e) {
      chartEl.innerHTML = '<div class="empty">Failed to load weekly data.</div>';
    }
  }

  function drawBars(points, metric, showCount) {
    const W = chartEl.clientWidth || 800;
    const H = 300;
    const padL = 50, padR = 12, padT = 10, padB = 32;
    const n = Math.max(points.length, 1);
    const innerW = W - padL - padR;
    const bw = Math.max(2, Math.floor(innerW / n) - 2);
    const values = points.map((p) => Number(p.total || 0));
    const maxAbs = Math.max(1, ...values.map((v) => Math.abs(v)));
    const innerH = H - padT - padB;

    const niceMax = (x) => {
      const p = Math.pow(10, Math.floor(Math.log10(x)));
      const m = x / p;
      if (m <= 1) return 1 * p;
      if (m <= 2) return 2 * p;
      if (m <= 5) return 5 * p;
      return 10 * p;
    };
    const YMAX = niceMax(maxAbs);
    const yScale = innerH / YMAX;

    // avoid label collisions: every Nth label depending on bar width
    const labelStep = Math.max(1, Math.ceil(60 / Math.max(bw + 2, 1)));

    let x = padL;
    const bars = [];
    const xLabels = [];
    const textLabels = []; // NEW: amount + count labels

    points.forEach((p, i) => {
      const v = Number(p.total || 0);
      const h = Math.round(Math.abs(v) * yScale);
      const yTop = H - padB - h;  // top of the bar for positive values
      const y = v >= 0 ? yTop : (H - padB);
      const w = bw;

      bars.push(`<rect class="bar" x="${x}" y="${y}" width="${w}" height="${h}" rx="2" ry="2" data-i="${i}"></rect>`);

      // Amount label just above the bar
      const amount = fmtMoney(Math.round(v));
      const amountY = Math.max(padT + 8, yTop - 2); // keep inside viewBox
      textLabels.push(
        `<text x="${x + w/2}" y="${amountY}" font-size="10" text-anchor="middle">${amount}</text>`
      );

      // Optional: invoice count label above the amount (issued + toggle on)
      if (metric === "issued" && showCount) {
        const cnt = Number(p.invoice_count || 0);
        if (cnt > 0) {
          const countY = Math.max(padT + 8, amountY - 10);
          textLabels.push(
            `<text x="${x + w/2}" y="${countY}" font-size="9" text-anchor="middle" opacity="0.85">${cnt}</text>`
          );
        }
      }

      // x-axis date labels (MM-DD)
      if (i % labelStep === 0 || i === n - 1) {
        const lbl = (p.start || "").slice(5);
        xLabels.push(`<text x="${x + w/2}" y="${H - 10}" font-size="10" text-anchor="middle">${lbl}</text>`);
      }

      x += w + 2;
    });

    // y grid + ticks
    const ticks = 5;
    const yGrid = [];
    for (let i = 0; i < ticks; i++) {
      const frac = i / (ticks - 1);
      const y = H - padB - frac * innerH;
      const v = Math.round(YMAX * frac);
      yGrid.push(`<line class="grid" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}"/>`);
      yGrid.push(`<text x="${padL - 6}" y="${y + 4}" font-size="10" text-anchor="end">${fmtMoney(v)}</text>`);
    }

    // Optional overlay line + dots for invoice count (kept as-is)
    let linePath = "";
    let dots = "";

    // render
    chartEl.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">
        <g>${yGrid.join("")}</g>
        <line class="grid" x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}"/>
        <g>${bars.join("")}</g>
        <g>${textLabels.join("")}</g>
        ${linePath}${dots}
        <g>${xLabels.join("")}</g>
      </svg>
    `;

    // Tooltip (unchanged)
    const tipId = "wk_tooltip";
    let tip = document.getElementById(tipId);
    if (!tip) {
      tip = document.createElement("div");
      tip.id = tipId;
      tip.style.position = "absolute";
      tip.style.pointerEvents = "none";
      tip.style.background = "rgba(0,0,0,0.75)";
      tip.style.color = "#fff";
      tip.style.padding = "6px 8px";
      tip.style.borderRadius = "6px";
      tip.style.fontSize = "12px";
      tip.style.transform = "translate(-50%,-120%)";
      tip.style.display = "none";
      chartEl.style.position = "relative";
      chartEl.appendChild(tip);
    }

    const svg = chartEl.querySelector("svg");
    svg.addEventListener("mousemove", (ev) => {
      const target = ev.target.closest(".bar");
      if (!target) { tip.style.display = "none"; return; }
      const i = Number(target.getAttribute("data-i"));
      const p = points[i] || {};
      const amt = fmtMoney(Number(p.total || 0));
      const wk = p.start || "";
      const cnt = Number(p.invoice_count || 0);
      tip.textContent = (metric === "issued" && showCount)
        ? `${wk}  •  ${amt}  •  ${cnt} invoices`
        : `${wk}  •  ${amt}`;
      tip.style.left = `${ev.offsetX}px`;
      tip.style.top  = `${ev.offsetY}px`;
      tip.style.display = "block";
    });
    svg.addEventListener("mouseleave", () => { tip.style.display = "none"; });
  }

  // Wire metric buttons
  if (btnIssued)   btnIssued.addEventListener("click", () => activate("issued"));
  if (btnReceived) btnReceived.addEventListener("click", () => activate("received"));

  // Wire quick week buttons
  if (quick) {
    quick.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-wk]");
      if (!b) return;
      weeks = Math.min(104, Math.max(1, Number(b.dataset.wk) || 26));
      if (inputWeeks) inputWeeks.value = String(weeks);
      setActiveQuick();
      load();
    });
  }

  // Wire free-form weeks input
  if (inputWeeks) {
    inputWeeks.addEventListener("change", () => {
      const v = Math.min(104, Math.max(1, Number(inputWeeks.value || 26)));
      weeks = v;
      setActiveQuick();
      load();
    });
  }

  // Toggle invoice count overlay
  if (toggleCount) {
    toggleCount.addEventListener("change", () => {
      showCount = !!toggleCount.checked;
      load();
    });
  }

  // initial
  setActiveQuick();
  activate("issued");
})();



/* ---------- Boot ---------- */
document.addEventListener('DOMContentLoaded', async () => {
  const id = getCustomerId();
  if (id){            // customer dashboard
    if (typeof setActiveCustomerTab === 'function') setActiveCustomerTab(id);
    await loadSummary(id);             // <- filtered by customer
    await loadCustomerInvoices(id);
  } else {            // main dashboard (always “All customers”)
    await populateCustomerFilter();
    await loadSummary(null);           // <- all customers
    await loadCustomerAging();
  }
});


