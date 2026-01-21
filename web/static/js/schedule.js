(function () {
  // -----------------------
  // tiny helpers
  // -----------------------
  const q  = (sel, parent = document) => parent.querySelector(sel);
  const qa = (sel, parent = document) => Array.from(parent.querySelectorAll(sel));
  const fmtDT = (iso) => (window.AppDate && AppDate.formatDateTime)
    ? AppDate.formatDateTime(iso)
    : (new Date(iso)).toLocaleString();

  function optionValueToHourInt(v) {
    const s = String(v || "0");
    const h = s.includes(":") ? s.split(":")[0] : s;
    const n = Number(h);
    return Math.max(0, Math.min(23, isNaN(n) ? 0 : n));
  }

  function hourToOptionValue(hourInt) {
    const h = Math.max(0, Math.min(23, Number(hourInt) || 0));
    return `${String(h).padStart(2, "0")}:00`;
  }

  function setSelectValue(sel, val) {
    if (!sel) return;
    const v = String(val ?? "");
    const opts = Array.from(sel.options || []);
    // clear any default 'selected' from HTML so screen readers don't get confused
    opts.forEach((o) => (o.selected = false));
    const match = opts.find((o) => o.value === v);
    if (match) match.selected = true;
    sel.value = v;
    sel.setAttribute("value", v);
  }

  function flashSaved(btn, ok = true, textOk = "Saved ✓", textErr = "Failed") {
    if (!btn) return;
    const prev = btn.textContent;
    btn.disabled = true;
    btn.textContent = ok ? textOk : textErr;
    setTimeout(() => {
      btn.textContent = prev;
      btn.disabled = false;
    }, 1200);
  }

  async function safeFetch(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) throw new Error(await r.text().catch(() => r.statusText));
    return r;
  }

  // -----------------------
  // Tabs
  // -----------------------
  qa(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      qa(".tab").forEach((b) => b.classList.remove("active"));
      qa(".tab-pane").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const pane = q(`#tab-${btn.dataset.tab}`);
      if (pane) pane.classList.add("active");
    });
  });

// ============================================================
// STATEMENT GLOBALS (plan enable, frequency, schedule, exclusions)
// ============================================================

const G_API = "/api/statement_globals";

const planEnabledSel  = q("#g_plan_enabled");
const freqToggle      = q("#g_freq_toggle");
const gFreqHidden     = q("#g_freq");

const weeklyDayWrap   = q("#weekly_day_wrap");
const monthlyDayWrap  = q("#monthly_day_wrap");

const gWeekHour       = q("#g_week_hour");
const gWeekDay        = q("#g_week_day");
const gMonthDay       = q("#g_month_day");

const exclBtn         = q("#g_excl_btn");

const scheduleSaveBtn = q("#g_schedule_save");


// cache from backend so we can rehydrate when the user flips Weekly <-> Monthly
let globalsCache = null;

// -------------------------------
// helpers
// -------------------------------

// currentFreq() -> "weekly" or "monthly"
function currentFreq() {
  if (!gFreqHidden) return "weekly";
  const v = (gFreqHidden.value || "weekly").toLowerCase();
  return v === "monthly" ? "monthly" : "weekly";
}

// Take whatever is in globalsCache and push it into the visible inputs
// based on which frequency is currently selected.
function applyCachedGlobalsToUI() {
  if (!globalsCache) return;

  const freq = currentFreq();

  if (freq === "weekly") {
    // Send hour comes from weekly_hour
    if (gWeekHour) {
      setSelectValue(gWeekHour, hourToOptionValue(globalsCache.weekly_hour));
    }

    // Day of week from weekly_dow
    if (gWeekDay) {
      setSelectValue(gWeekDay, String(globalsCache.weekly_dow ?? 0));
    }

    // Keep monthly day-of-month cached even if not visible
    if (gMonthDay) {
      gMonthDay.value = String(
        Math.max(1, Math.min(31, globalsCache.monthly_dom ?? 1))
      );
    }
  } else {
    // monthly mode
    if (gWeekHour) {
      setSelectValue(gWeekHour, hourToOptionValue(globalsCache.monthly_hour));
    }

    if (gMonthDay) {
      gMonthDay.value = String(
        Math.max(1, Math.min(31, globalsCache.monthly_dom ?? 1))
      );
    }

    // Keep weekly day cached too
    if (gWeekDay) {
      setSelectValue(gWeekDay, String(globalsCache.weekly_dow ?? 0));
    }
  }
}

// Refresh what’s *visible* in the UI when freq changes:
// - which "day" picker we show
// - which label says "Weekly schedule" vs "Monthly schedule"
// - which part of the customer summary is visible
// - which side of the pill is highlighted
function refreshFreqUI() {
  const freq = currentFreq();

  // show/hide day-of-week vs day-of-month
  if (weeklyDayWrap)  weeklyDayWrap.style.display  = (freq === "weekly")  ? "" : "none";
  if (monthlyDayWrap) monthlyDayWrap.style.display = (freq === "monthly") ? "" : "none";

  // highlight pill
  if (freqToggle) {
    freqToggle.classList.toggle("is-weekly",  freq === "weekly");
    freqToggle.classList.toggle("is-monthly", freq === "monthly");
  }

  // exclusions button knows which schedule is in play
  if (exclBtn) {
    exclBtn.setAttribute("data-freq", freq);
  }

  // update the button label based on new freq
  updateExclSummary();
}


// Dim / disable the whole config if master "Send statements automatically" is Off.
function refreshPlanEnabledUI() {
  if (!planEnabledSel) return;
  const on = String(planEnabledSel.value) === "true";

  // For everything interactive in this block:
  [freqToggle, gWeekHour, gWeekDay, gMonthDay, scheduleSaveBtn, exclBtn].forEach(
    (el) => {
      if (!el) return;
      if (el === freqToggle) {
        el.style.opacity = on ? "1" : "0.5";
        el.style.pointerEvents = on ? "auto" : "none";
      } else {
        el.disabled = !on;
      }
    }
  );
}

// Turns a <select id="g_plan_enabled">true/false</select> into an accessible pill toggle.
function mountYesNoToggle(selectEl) {
  if (!selectEl || selectEl.__toggleMounted) return;
  selectEl.__toggleMounted = true;

  const pill = document.createElement("div");
  pill.className = "yn-toggle";
  pill.innerHTML = `
    <span class="yn-toggle__knob" aria-hidden="true"></span>
    <span class="yn-toggle__text" aria-hidden="true"></span>
  `;

  // hide real select, insert pill after it
  selectEl.style.display = "none";
  selectEl.parentNode.insertBefore(pill, selectEl.nextSibling);

  function setUIFromSelect() {
    const on = String(selectEl.value) === "true";
    pill.classList.toggle("is-on", on);

    const text = pill.querySelector(".yn-toggle__text");
    if (text) text.textContent = on ? "On" : "Off";

    pill.setAttribute("role", "switch");
    pill.setAttribute("aria-checked", String(on));
    pill.setAttribute("tabindex", "0");
    pill.title = on ? "On" : "Off";
  }

  function commit(nextOn) {
    selectEl.value = nextOn ? "true" : "false";
    setUIFromSelect();
    // fire normal change for any listeners
    selectEl.dispatchEvent(new Event("change", { bubbles: true }));
  }

  pill.addEventListener("click", () => {
    commit(!(String(selectEl.value) === "true"));
  });

  pill.addEventListener("keydown", (e) => {
    if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      commit(!(String(selectEl.value) === "true"));
    }
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      commit(false);
    }
    if (e.key === "ArrowRight") {
      e.preventDefault();
      commit(true);
    }
  });

  // sync pill if <select> changes some other way
  selectEl.addEventListener("change", setUIFromSelect);

  // sync pill if we update selectEl.value in code
  const obs = new MutationObserver(() => setUIFromSelect());
  obs.observe(selectEl, { attributes: true, attributeFilter: ["value"] });
  selectEl.__toggleObserver = obs;

  setUIFromSelect();
}

// Wire the Weekly / Monthly pill
function mountFreqToggle() {
  if (!freqToggle || freqToggle.__mounted) return;
  freqToggle.__mounted = true;

  freqToggle.addEventListener("click", (e) => {
    const opt = e.target.closest(".freq-toggle__opt");
    if (!opt) return;

    const newVal = opt.dataset.val === "monthly" ? "monthly" : "weekly";
    if (gFreqHidden) {
      gFreqHidden.value = newVal;
    }

    // Update UI with new freq
    applyCachedGlobalsToUI();
    refreshFreqUI();

    // If the plan is currently ON, persist to backend right away
    if (String(planEnabledSel?.value) === "true") {
      saveMasterEnabled();
    }
  });
}

// ---------- Exclusions popover helpers ----------

// We'll use these module-level vars to track the floating popover.
let EXCL_POPOVER = null;
let EXCL_POPOVER_ANCHOR = null;

// Load all customers (shared with chasing area)
async function fetchAllCustomers() {
  const r = await fetch("/api/customers?per_page=1000", { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  const data = await r.json();
  return Array.isArray(data?.items)
    ? data.items
    : Array.isArray(data)
    ? data
    : [];
}

async function getCurrentExclusions(freq) {
  const r = await fetch(`${G_API}/exclusions`, { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  const all = await r.json();
  return new Set(
    (all || [])
      .filter((x) => x.frequency === freq)
      .map((x) => x.customer_id)
  );
}

async function getAllExclusions() {
  const r = await fetch(`${G_API}/exclusions`, { cache: "no-store" });
  if (!r.ok) throw new Error(String(r.status));
  return await r.json(); // [{customer_id, frequency}, ...]
}

async function getCustomersCount() {
  const all = await fetchAllCustomers();
  return Array.isArray(all) ? all.length : 0;
}

// Write summary text under the schedule row
async function updateExclSummary() {
  // figure out how many are included for weekly vs monthly,
  // then set the button text accordingly for the *current* freq.
  if (!exclBtn) return;

  try {
    const [rows, total] = await Promise.all([
      getAllExclusions(),
      getCustomersCount(),
    ]);

    const freq = currentFreq();

    // how many excluded for each schedule
    const wkExcluded = (rows || []).filter((x) => x.frequency === "weekly").length;
    const moExcluded = (rows || []).filter((x) => x.frequency === "monthly").length;

    const wkIncluded = total - wkExcluded;
    const moIncluded = total - moExcluded;

    let label;

    if (freq === "weekly") {
      // if none excluded under weekly, just say All customers…
      if (wkExcluded === 0) {
        label = "All customers…";
      } else {
        label = `Customers (${wkIncluded} of ${total} included)…`;
      }
    } else {
      // monthly mode
      if (moExcluded === 0) {
        label = "All customers…";
      } else {
        label = `Customers (${moIncluded} of ${total} included)…`;
      }
    }

    exclBtn.textContent = label;
  } catch (e) {
    console.error("updateExclSummary failed", e);
    // fallback so button still says *something*
    exclBtn.textContent = "All customers…";
  }
}

// Position the floating popover under the button
function positionPopover(pop, anchor) {
  const r = anchor.getBoundingClientRect();
  const top = window.scrollY + r.bottom + 6;
  let left = window.scrollX + r.left;
  const width = 360;
  const maxLeft = window.scrollX + window.innerWidth - width - 10;
  if (left > maxLeft) left = maxLeft;
  pop.style.top = `${top}px`;
  pop.style.left = `${left}px`;
}

function closeExclPopover() {
  if (!EXCL_POPOVER) return;
  EXCL_POPOVER.remove();
  EXCL_POPOVER = null;
  EXCL_POPOVER_ANCHOR = null;
  document.removeEventListener("click", onDocClickPopover, true);
  window.removeEventListener("resize", onWinChangePopover);
  window.removeEventListener("scroll", onWinChangePopover, true);
}

function onDocClickPopover(e) {
  if (!EXCL_POPOVER) return;
  // Click inside the popover? ignore.
  if (e.target.closest(".excl-popover")) return;
  // Click the trigger again? let that handler re-open if needed.
  if (e.target.closest(".js-excl-manage, .js-ch-excl-manage")) return;
  closeExclPopover();
}

function onWinChangePopover() {
  if (!EXCL_POPOVER || !EXCL_POPOVER_ANCHOR) return;
  positionPopover(EXCL_POPOVER, EXCL_POPOVER_ANCHOR);
}

// Fill the exclusion popover list with checkboxes for customers
async function buildExclPopoverList(freq) {
  const listEl    = EXCL_POPOVER?.querySelector(".excl-popover__list");
  const sumEl     = EXCL_POPOVER?.querySelector(".excl-popover__summary");
  const btnSave   = EXCL_POPOVER?.querySelector(".js-excl-save");
  const btnCancel = EXCL_POPOVER?.querySelector(".js-excl-cancel");
  const search    = EXCL_POPOVER?.querySelector(".excl-popover__search");
  if (!listEl || !btnSave) return;

  listEl.textContent = "Loading…";

  try {
    const [customers, excludedSet] = await Promise.all([
      fetchAllCustomers(),
      getCurrentExclusions(freq),
    ]);

    function render(filter = "") {
      const norm = filter.trim().toLowerCase();
      const rows = customers.filter(
        (c) =>
          !norm ||
          String(c.name || "").toLowerCase().includes(norm) ||
          String(c.id).includes(norm)
      );

      listEl.innerHTML =
        rows
          .map((c) => {
            const excluded = excludedSet.has(c.id);
            const checked = !excluded;
            const safeName = (c.name || `Customer #${c.id}`).replace(
              /[<>&]/g,
              (s) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[s])
            );
            return `
              <label class="excl-item">
                <span class="excl-item__left">
                  <input type="checkbox" data-id="${c.id}" ${
              checked ? "checked" : ""
            }>
                  <span>${safeName}</span>
                </span>
                <span class="excl-item__right">#${c.id}</span>
              </label>
            `;
          })
          .join("") || '<div class="muted">No customers found.</div>';

      if (sumEl) {
        const inputs = listEl.querySelectorAll(
          'input[type="checkbox"][data-id]'
        );
        let included = 0;
        inputs.forEach((i) => {
          if (i.checked) included += 1;
        });
        sumEl.textContent = `${customers.length - included} excluded — ${included} of ${customers.length} included`;
      }
    }

    render();

    // live search filter
    let t = null;
    if (search) {
      search.addEventListener("input", () => {
        clearTimeout(t);
        t = setTimeout(() => render(search.value), 150);
      });
    }

    // Save button -> diff POST/DELETE
    btnSave.onclick = async () => {
      const inputs = Array.from(
        listEl.querySelectorAll('input[type="checkbox"][data-id]')
      );
      // in UI: checked = included. unchecked = excluded.
      const newExcluded = new Set(
        inputs
          .filter((i) => !i.checked)
          .map((i) => Number(i.dataset.id))
      );

      const toAdd = [...newExcluded].filter((id) => !excludedSet.has(id));
      const toRemove = [...excludedSet].filter(
        (id) => !newExcluded.has(id)
      );

      await Promise.all([
        ...toAdd.map((id) =>
          fetch(`${G_API}/exclusions`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ frequency: freq, customer_id: id }),
          })
        ),
        ...toRemove.map((id) =>
          fetch(
            `${G_API}/exclusions/${encodeURIComponent(freq)}/${encodeURIComponent(id)}`,
            { method: "DELETE" }
          )
        ),
      ]);

      await updateExclSummary();
      closeExclPopover();
    };

    btnCancel?.addEventListener("click", closeExclPopover);
  } catch (e) {
    listEl.innerHTML = '<div class="muted">Failed to load.</div>';
    console.error(e);
  }
}

// Create + show the popover next to the "All customers…" button
async function openExclPopover(anchorBtn, freq) {
  closeExclPopover(); // ensure only one open

  const pop = document.createElement("div");
  pop.className = "excl-popover";
  pop.innerHTML = `
    <div class="excl-popover__head">
      <div class="title">All customers — ${freq}</div>
      <input type="text"
             class="excl-popover__search"
             placeholder="Search customers…">
    </div>
    <div class="excl-popover__body">
      <div class="excl-popover__list">Loading…</div>
    </div>
    <div class="excl-popover__foot">
      <span class="excl-popover__summary"></span>
      <div style="flex:1;"></div>
      <button type="button"
              class="btn btn--ghost sm js-excl-cancel">Close</button>
      <button type="button"
              class="btn btn--primary sm js-excl-save">Save</button>
    </div>
  `;

  document.body.appendChild(pop);
  EXCL_POPOVER = pop;
  EXCL_POPOVER_ANCHOR = anchorBtn;

  positionPopover(pop, anchorBtn);

  // global listeners to close / reposition
  document.addEventListener("click", onDocClickPopover, true);
  window.addEventListener("resize", onWinChangePopover);
  window.addEventListener("scroll", onWinChangePopover, true);

  await buildExclPopoverList(freq);
}

// -------------------------------
// server sync
// -------------------------------

// Called when master On/Off or freq changes.
// Pushes weekly + monthly state (hour/dow/dom) to backend.
async function saveMasterEnabled() {
  const on   = String(planEnabledSel?.value) === "true";
  const freq = currentFreq();

  const weeklyBody = {
    enabled: on && freq === "weekly",
    hour: optionValueToHourInt(gWeekHour?.value),
    dow:  Number(gWeekDay?.value ?? 0),
  };

  const monthlyBody = {
    enabled: on && freq === "monthly",
    hour: optionValueToHourInt(gWeekHour?.value), // same hour select
    dom:  Math.max(1, Math.min(31, Number(gMonthDay?.value || 1))),
  };

  try {
    await safeFetch(`${G_API}/weekly`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(weeklyBody),
    });

    await safeFetch(`${G_API}/monthly`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(monthlyBody),
    });

    await loadGlobals();        // re-pull into cache + inputs
    await updateExclSummary();
  } catch (e) {
    console.error("saveMasterEnabled failed", e);
    alert("Could not update statement plan state");
  }
}

// Called when user hits "Update schedule" button.
async function saveSchedule() {
  const on   = String(planEnabledSel?.value) === "true";
  const freq = currentFreq();

  if (freq === "weekly") {
    const body = {
      enabled: on,
      hour: optionValueToHourInt(gWeekHour?.value),
      dow:  Number(gWeekDay?.value ?? 0),
    };
    try {
      await safeFetch(`${G_API}/weekly`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      flashSaved(scheduleSaveBtn, true, "Saved ✓");
      await loadGlobals();
      await updateExclSummary();
    } catch (e) {
      console.error(e);
      flashSaved(scheduleSaveBtn, false, "Failed");
    }
  } else {
    const body = {
      enabled: on,
      hour: optionValueToHourInt(gWeekHour?.value),
      dom:  Math.max(1, Math.min(31, Number(gMonthDay?.value || 1))),
    };
    try {
      await safeFetch(`${G_API}/monthly`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      flashSaved(scheduleSaveBtn, true, "Saved ✓");
      await loadGlobals();
      await updateExclSummary();
    } catch (e) {
      console.error(e);
      flashSaved(scheduleSaveBtn, false, "Failed");
    }
  }
}

// Pull server data, update globalsCache, then reflect into UI.
async function loadGlobals() {
  try {
    const r = await fetch(G_API, { cache: "no-store" });
    if (!r.ok) throw new Error(String(r.status));
    const data = await r.json();
    // data:
    // {
    //   weekly_enabled, weekly_hour, weekly_dow,
    //   monthly_enabled, monthly_hour, monthly_dom
    // }

    globalsCache = {
      weekly_enabled:  data.weekly_enabled,
      weekly_hour:     data.weekly_hour,
      weekly_dow:      data.weekly_dow,
      monthly_enabled: data.monthly_enabled,
      monthly_hour:    data.monthly_hour,
      monthly_dom:     data.monthly_dom,
    };

    // decide active freq:
    // priority: weekly if enabled, else monthly if enabled, else weekly
    let activeFreq = "weekly";
    if (data.weekly_enabled) {
      activeFreq = "weekly";
    } else if (data.monthly_enabled) {
      activeFreq = "monthly";
    }

    if (gFreqHidden) {
      gFreqHidden.value = activeFreq;
    }

    // master toggle is on if EITHER weekly_enabled or monthly_enabled
    const anyOn = !!data.weekly_enabled || !!data.monthly_enabled;
    if (planEnabledSel) {
      planEnabledSel.value = anyOn ? "true" : "false";
      planEnabledSel.setAttribute("value", anyOn ? "true" : "false");
    }

    // put cached values into the visible inputs for the active freq
    applyCachedGlobalsToUI();

    // sync visuals
    refreshFreqUI();
    refreshPlanEnabledUI();

    await updateExclSummary();
  } catch (e) {
    console.error("loadGlobals failed", e);
  }
}

// -------------------------------
// event wiring for statements card
// -------------------------------

mountYesNoToggle(planEnabledSel);
mountFreqToggle();

planEnabledSel?.addEventListener("change", () => {
  refreshPlanEnabledUI();
  saveMasterEnabled();
});

scheduleSaveBtn?.addEventListener("click", saveSchedule);

// "All customers…" button -> open exclusions popover
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".js-excl-manage");
  if (!btn) return;
  const freq = btn.dataset.freq || currentFreq();
  openExclPopover(btn, freq);
});

  // ============================================================
  // CHASING (globals + exclusions + send-now)
  // ============================================================

  const CH_API        = "/api/chasing_reminders";            // globals base
  const CH_SEQ_API    = "/api/chasing_messages/sequences";   // list cycles
  const CH_EXCL_API   = "/api/chasing_reminders/exclusions"; // exclusions base

  // Elements
  const chEnabled     = q("#ch_enabled");
  const chHour        = q("#ch_hour");
  const chSeqDefault  = q("#ch_sequence_default");
  const chDelivery    = q("#ch_delivery_mode");
  const chSaveBtn     = q("#ch_save");
  const chExclBtn     = q("#ch_excl_btn");

  // Send-now UI
  const chSNSeq   = q("#ch_sendnow_sequence");
  const chSNPick  = q("#ch_sendnow_customers");
  const chSNSum   = q("#ch_sendnow_summary");
  const chSNMode  = q("#ch_sendnow_delivery");
  const chSNBtn   = q("#ch_sendnow_btn");
  const chSNRes   = q("#ch_sendnow_result");

  // null = all included customers; Set<number> = explicit ids
  let chSNSelected = null;
  function chSNSetSummary() {
    if (!chSNSum) return;
    if (!chSNSelected || chSNSelected.size === 0) {
      chSNSum.textContent = "All included customers";
    } else {
      chSNSum.textContent = `${chSNSelected.size} customer(s) selected`;
    }
  }
  chSNSetSummary();

  // reuse toggle pill for chasing enabled
  mountYesNoToggle(chEnabled);

  // chasing exclusions summary
  async function getChasingExclusions() {
    const r = await fetch(CH_EXCL_API, { cache: "no-store" });
    if (!r.ok) throw new Error(await r.text());
    return await r.json(); // [{customer_id, customer_name}, ...]
  }

  // Build the chasing exclusions popover list for #ch_excl_btn
  async function buildChasingExclList() {
    const listEl    = EXCL_POPOVER?.querySelector(".excl-popover__list");
    const sumEl     = EXCL_POPOVER?.querySelector(".excl-popover__summary");
    const btnSave   = EXCL_POPOVER?.querySelector(".js-ch-save");
    const btnClose  = EXCL_POPOVER?.querySelector(".js-ch-cancel");
    const search    = EXCL_POPOVER?.querySelector(".excl-popover__search");

    if (!listEl || !btnSave) return;

    listEl.textContent = "Loading…";

    try {
      // all customers + who is currently excluded from chasing
      const [customers, excludedRows] = await Promise.all([
        fetchAllCustomers(),
        getChasingExclusions(), // [{ customer_id, customer_name }, ...]
      ]);

      // Set of ids that are currently EXCLUDED from chasing
      const excludedSet = new Set(
        (excludedRows || []).map((x) => x.customer_id)
      );

      // Working copy so user can toggle without saving yet
      let workingExcluded = new Set(excludedSet);

      function render(filter = "") {
        const norm = filter.trim().toLowerCase();
        const rows = (customers || []).filter((c) => {
          if (!norm) return true;
          return (
            String(c.name || "").toLowerCase().includes(norm) ||
            String(c.id).includes(norm)
          );
        });

        listEl.innerHTML =
          rows
            .map((c) => {
              const isExcluded = workingExcluded.has(c.id);
              // checked = will receive chasing (so NOT excluded)
              const checked   = !isExcluded;
              const safeName  = (c.name || `Customer #${c.id}`).replace(
                /[<>&]/g,
                (s) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[s])
              );

              return `
                <label class="excl-item">
                  <span class="excl-item__left">
                    <input
                      type="checkbox"
                      data-id="${c.id}"
                      ${checked ? "checked" : ""}
                    >
                    <span>${safeName}</span>
                  </span>
                  <span class="excl-item__right">#${c.id}</span>
                </label>
              `;
            })
            .join("") || '<div class="muted">No customers found.</div>';

        // summary text ("X of Y included")
        if (sumEl) {
          const includedCount = (customers.length - workingExcluded.size);
          sumEl.textContent = `${includedCount} of ${customers.length} included`;
        }

        // hook checkbox change after (re)render
        listEl.querySelectorAll('input[type="checkbox"][data-id]').forEach((cb) => {
          cb.addEventListener("change", () => {
            const id = Number(cb.dataset.id);
            if (cb.checked) {
              // checked = include in chasing = remove from excluded set
              workingExcluded.delete(id);
            } else {
              // unchecked = exclude from chasing
              workingExcluded.add(id);
            }
            const includedCount = (customers.length - workingExcluded.size);
            if (sumEl) {
              sumEl.textContent = `${includedCount} of ${customers.length} included`;
            }
          });
        });
      }

      render();

      // live search
      let t = null;
      if (search) {
        search.addEventListener("input", () => {
          clearTimeout(t);
          t = setTimeout(() => render(search.value), 150);
        });
      }

      // Save → diff + POST/DELETE to CH_EXCL_API
      btnSave.addEventListener("click", async () => {
        // Build newExcluded from the in-memory workingExcluded set
        // instead of reading only currently visible checkboxes.
        // This prevents losing people if you filtered the list.
        const newExcluded = new Set(workingExcluded);

        // Work out the differences compared to what the server said
        // was excluded when we first opened the popover.
        const toAdd = [...newExcluded].filter((id) => !excludedSet.has(id));
        const toRemove = [...excludedSet].filter(
          (id) => !newExcluded.has(id)
        );

        // Debug output so we can see exactly what we're about to send
        console.log("DEBUG chasing save click");
        console.log("DEBUG original excludedSet from server:", [...excludedSet]);
        console.log("DEBUG newExcluded after user edits:", [...newExcluded]);
        console.log("DEBUG toAdd (POST these IDs):", toAdd);
        console.log("DEBUG toRemove (DELETE these IDs):", toRemove);

        try {
          // Send POSTs for any new exclusions
          for (const id of toAdd) {
            const url = CH_EXCL_API;
            const bodyObj = { customer_id: Number(id) };

            console.log("DEBUG → POST", url, bodyObj);

            const resp = await fetch(url, {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "Accept": "application/json",
              },
              body: JSON.stringify(bodyObj),
            });

            const text = await resp.text();
            console.log("DEBUG POST response status:", resp.status);
            console.log("DEBUG POST response body:", text);

            if (!resp.ok) {
              console.error("DEBUG POST failed for customer_id", id);
              throw new Error(
                "POST " + url + " failed " + resp.status + ": " + text
              );
            }
          }

          // Send DELETEs for any IDs that used to be excluded
          // but are now allowed back in
          for (const id of toRemove) {
            const url =
              CH_EXCL_API + "/" + encodeURIComponent(Number(id));

            console.log("DEBUG → DELETE", url);

            const resp = await fetch(url, {
              method: "DELETE",
              headers: {
                "Accept": "application/json",
              },
            });

            const text = await resp.text();
            console.log("DEBUG DELETE response status:", resp.status);
            console.log("DEBUG DELETE response body:", text);

            if (!resp.ok) {
              console.error("DEBUG DELETE failed for customer_id", id);
              throw new Error(
                "DELETE " + url + " failed " + resp.status + ": " + text
              );
            }
          }

          console.log("DEBUG all save requests succeeded");

          // Refresh the main button label and close the popover
          await updateChasingExclSummary();
          closeExclPopover();
        } catch (err) {
          console.error("DEBUG save error:", err);
          alert("Failed to save chasing exclusions");
        }
      });


      btnClose?.addEventListener("click", closeExclPopover);
    } catch (err) {
      console.error(err);
      listEl.textContent = "Failed to load.";
    }
  }

  // Open popover for chasing exclusions (uses same global EXCL_POPOVER plumbing)
  async function openChasingExclPopover(anchorBtn) {
    // close any other open popover first
    closeExclPopover();

    const pop = document.createElement("div");
    pop.className = "excl-popover";
    pop.innerHTML = `
      <div class="excl-popover__head">
        <div class="title">Invoice reminders – customers</div>
        <input type="text"
              class="excl-popover__search"
              placeholder="Search customers…">
      </div>

      <div class="excl-popover__body">
        <div class="excl-popover__list">Loading…</div>
      </div>

      <div class="excl-popover__foot">
        <span class="excl-popover__summary"></span>
        <div style="flex:1;"></div>
        <button type="button"
                class="btn btn--ghost sm js-ch-cancel">Close</button>
        <button type="button"
                class="btn btn--primary sm js-ch-save">Save</button>
      </div>
    `;

    document.body.appendChild(pop);
    EXCL_POPOVER = pop;
    EXCL_POPOVER_ANCHOR = anchorBtn;

    // position and global listeners (re-use your existing helpers)
    positionPopover(pop, anchorBtn);
    document.addEventListener("click", onDocClickPopover, true);
    window.addEventListener("resize", onWinChangePopover);
    window.addEventListener("scroll", onWinChangePopover, true);

    // build list of customers with chasing include/exclude toggles
    await buildChasingExclList();
  }


  async function updateChasingExclSummary() {
    if (!chExclBtn) return;

    try {
      const [rows, total] = await Promise.all([
        getChasingExclusions(),           // [{ customer_id, customer_name }, ...]
        (async () => {
          const all = await fetchAllCustomers();
          return Array.isArray(all) ? all.length : 0;
        })(),
      ]);

      const excluded = (rows || []).length;
      const included = total - excluded;

      if (excluded === 0) {
        chExclBtn.textContent = "All customers…";
      } else {
        chExclBtn.textContent = `Customers (${included} of ${total} included)…`;
      }
    } catch (e) {
      console.error("updateChasingExclSummary failed", e);
    }
  }

  // populate sequences into selects
  async function loadChasingSequences() {
    if (!chSeqDefault && !chSNSeq) return [];

    const prevDefault = chSeqDefault ? String(chSeqDefault.value || "") : null;
    const prevSendNow = chSNSeq ? String(chSNSeq.value || "") : null;

    if (chSeqDefault)
      chSeqDefault.innerHTML = `<option value="">(choose cycle)</option>`;
    if (chSNSeq)
      chSNSeq.innerHTML = `<option value="">Use defaults</option>`;

    try {
      const r = await fetch(CH_SEQ_API, { cache: "no-store" });
      if (!r.ok) throw new Error(await r.text());

      let seqs = await r.json(); // [{ id, name }, ...]
      seqs = Array.isArray(seqs)
        ? seqs.slice().sort((a, b) => {
            const an = (a.name || `Cycle ${a.id}`).toLowerCase();
            const bn = (b.name || `Cycle ${b.id}`).toLowerCase();
            if (an < bn) return -1;
            if (an > bn) return 1;
            return (a.id || 0) - (b.id || 0);
          })
        : [];

      for (const s of seqs) {
        const label = s.name || `Cycle #${s.id}`;

        if (chSeqDefault) {
          const o = document.createElement("option");
          o.value = String(s.id);
          o.textContent = label;
          chSeqDefault.appendChild(o);
        }

        if (chSNSeq) {
          const o2 = document.createElement("option");
          o2.value = String(s.id);
          o2.textContent = label;
          chSNSeq.appendChild(o2);
        }
      }

      if (chSeqDefault) {
        const hasPrev =
          prevDefault &&
          Array.from(chSeqDefault.options).some(
            (o) => o.value === prevDefault
          );
        chSeqDefault.value = hasPrev ? prevDefault : "";
        chSeqDefault.setAttribute("value", chSeqDefault.value);
      }
      if (chSNSeq) {
        const hasPrev2 =
          prevSendNow &&
          Array.from(chSNSeq.options).some(
            (o) => o.value === prevSendNow
          );
        chSNSeq.value = hasPrev2 ? prevSendNow : "";
        chSNSeq.setAttribute("value", chSNSeq.value);
      }

      return seqs;
    } catch (e) {
      console.error("loadChasingSequences failed", e);

      if (chSeqDefault && chSeqDefault.options.length === 1) {
        const o = document.createElement("option");
        o.value = "";
        o.textContent = "(failed to load)";
        chSeqDefault.appendChild(o);
      }
      if (chSNSeq && chSNSeq.options.length === 1) {
        const o2 = document.createElement("option");
        o2.value = "";
        o2.textContent = "(failed to load)";
        chSNSeq.appendChild(o2);
      }

      return [];
    }
  }

  async function loadChasingGlobals() {
    try {
      const seqs = await loadChasingSequences();
      const r = await fetch(`${CH_API}/globals`, { cache: "no-store" });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json(); // { enabled, hour, default_sequence_id }

      const enabled = data.enabled !== false;
      const hourOpt = `${String(
        typeof data.hour === "number" ? data.hour : 21
      ).padStart(2, "0")}:00`;

      setSelectValue(chEnabled, enabled ? "true" : "false");
      setSelectValue(chHour, hourOpt);
      if (chDelivery) chDelivery.disabled = !enabled;

      const want = data.default_sequence_id;
      const has = seqs.some((s) => String(s.id) === String(want));
      setSelectValue(chSeqDefault, has ? String(want) : "");
      if (chSeqDefault) chSeqDefault.disabled = !enabled;

      await updateChasingExclSummary();
      await loadChasingDeliveryMode();
    } catch (e) {
      console.error("loadChasingGlobals failed", e);
    }
  }

  async function loadChasingDeliveryMode() {
    if (!chDelivery && !chSNMode) return;
    try {
      const r = await fetch("/api/sms/settings", { cache: "no-store" });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      const mode = data.delivery_mode || "email";
      if (chDelivery) setSelectValue(chDelivery, mode);
      if (chSNMode) setSelectValue(chSNMode, mode);
    } catch (e) {
      console.error("loadChasingDeliveryMode failed", e);
    }
  }

  async function saveChasingGlobals() {
    try {
      const enabled = chEnabled?.value === "true";
      const hour = optionValueToHourInt(chHour?.value);
      const raw = chSeqDefault?.value;
      const default_sequence_id =
        enabled && raw !== "" && raw != null ? Number(raw) : null;

      const body = { enabled, hour, default_sequence_id };

      await safeFetch("/api/chasing_reminders/globals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (chDelivery) {
        await safeFetch("/api/sms/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ delivery_mode: chDelivery.value }),
        });
      }

      flashSaved(chSaveBtn, true, "Saved ✓");
    } catch (e) {
      console.error("saveChasingGlobals failed", e);
      flashSaved(chSaveBtn, false, "Failed");
      alert("Failed to save chasing settings");
    }
  }

  // Send-now picker (manual send)
  async function openSendNowPicker(anchorBtn) {
    // popover shell
    const pop = document.createElement("div");
    pop.className = "excl-popover";
    pop.innerHTML = `
      <div class="excl-popover__head">
        <div class="title">Choose customers</div>
        <input type="text" class="excl-popover__search" placeholder="Search customers…">
      </div>
      <div class="excl-popover__body"><div class="excl-popover__list">Loading…</div></div>
      <div class="excl-popover__foot">
        <span class="excl-popover__summary"></span>
        <div style="flex:1;"></div>
        <button type="button" class="btn btn--ghost sm js-excl-cancel">Close</button>
        <button type="button" class="btn btn--primary sm js-excl-save">Use selection</button>
      </div>
    `;
    document.body.appendChild(pop);

    // position
    const r = anchorBtn.getBoundingClientRect();
    const top = window.scrollY + r.bottom + 6;
    let left = window.scrollX + r.left;
    const width = 360;
    const maxLeft =
      window.scrollX + window.innerWidth - width - 10;
    if (left > maxLeft) left = maxLeft;
    pop.style.top = `${top}px`;
    pop.style.left = `${left}px`;

    const listEl = pop.querySelector(".excl-popover__list");
    const sumEl = pop.querySelector(".excl-popover__summary");
    const btnSave = pop.querySelector(".js-excl-save");
    const btnClose = pop.querySelector(".js-excl-cancel");
    const search = pop.querySelector(".excl-popover__search");

    try {
      // eligible = has email and not globally excluded for chasing
      const [customers, excludedRows] = await Promise.all([
        fetchAllCustomers(),
        getChasingExclusions(),
      ]);
      const excludedSet = new Set(
        (excludedRows || []).map((x) => x.customer_id)
      );
      const eligible = (customers || []).filter(
        (c) => (c.email || "").trim() && !excludedSet.has(c.id)
      );

      // start with previous selection or "all"
      let workingSel = chSNSelected
        ? new Set(chSNSelected)
        : new Set(eligible.map((c) => c.id));

      function render(filter = "") {
        const norm = filter.trim().toLowerCase();
        const rows = eligible.filter(
          (c) =>
            !norm ||
            String(c.name || "")
              .toLowerCase()
              .includes(norm) ||
            String(c.id).includes(norm)
        );

        listEl.innerHTML =
          rows
            .map((c) => {
              const checked = workingSel.has(c.id);
              const safeName = (c.name || `Customer #${c.id}`).replace(
                /[<>&]/g,
                (s) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[s])
              );
              return `
                <label class="excl-item">
                  <span class="excl-item__left">
                    <input type="checkbox" data-id="${c.id}" ${
                checked ? "checked" : ""
              }>
                    <span>${safeName}</span>
                  </span>
                  <span class="excl-item__right">#${c.id}</span>
                </label>
              `;
            })
            .join("") || '<div class="muted">No customers found.</div>';

        const inputs = listEl.querySelectorAll(
          'input[type="checkbox"][data-id]'
        );
        let included = 0;
        inputs.forEach((i) => {
          if (i.checked) included += 1;
        });
        if (sumEl)
          sumEl.textContent = `${included} of ${eligible.length} selected`;
      }

      render();

      // search
      let t = null;
      search?.addEventListener("input", () => {
        clearTimeout(t);
        t = setTimeout(() => render(search.value), 150);
      });

      // checkbox changes
      listEl.addEventListener("change", (e) => {
        const cb = e.target.closest('input[type="checkbox"][data-id]');
        if (!cb) return;
        const id = Number(cb.dataset.id);
        if (cb.checked) workingSel.add(id);
        else workingSel.delete(id);

        const inputs = listEl.querySelectorAll(
          'input[type="checkbox"][data-id]'
        );
        let included = 0;
        inputs.forEach((i) => {
          if (i.checked) included += 1;
        });
        if (sumEl)
          sumEl.textContent = `${included} of ${eligible.length} selected`;
      });

      // commit selection
      btnSave?.addEventListener("click", () => {
        chSNSelected =
          workingSel.size === eligible.length ? null : workingSel;
        chSNSetSummary();
        pop.remove();
      });

      btnClose?.addEventListener("click", () => pop.remove());

      document.addEventListener(
        "click",
        (evt) => {
          if (!pop.contains(evt.target) && evt.target !== anchorBtn) {
            pop.remove();
          }
        },
        { capture: true }
      );
    } catch (e) {
      console.error(e);
      if (listEl) listEl.textContent = "Failed to load.";
    }
  }

  async function sendNowChasing() {
    const seqRaw = chSNSeq?.value || "";
    const body = {
      sequence_id: seqRaw ? Number(seqRaw) : null,
      customer_ids:
        chSNSelected && chSNSelected.size > 0
          ? Array.from(chSNSelected)
          : null,
      delivery_mode: chSNMode?.value || chDelivery?.value || "email",
    };

    const prettyCycle = seqRaw ? `cycle #${seqRaw}` : "default cycles";
    const prettyRecipients = !body.customer_ids
      ? "all included customers"
      : `${body.customer_ids.length} selected customers`;

    if (
      !confirm(
        `Send chasing now using ${prettyCycle} to ${prettyRecipients}?`
      )
    )
      return;

    try {
      const r = await safeFetch("/api/chasing_reminders/send-now", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const j = await r.json().catch(() => ({}));
      if (chSNRes)
        chSNRes.textContent = `Queued ${j.jobs ?? 0} message(s).`;

      const targetTabName =
        body.delivery_mode === "sms" ? "sms-activity" : "activity";
      const activityTab = qa(".tab").find(
        (t) => (t.dataset.tab || "") === targetTabName
      );
      if (activityTab) activityTab.click();
      setTimeout(() => {
        if (targetTabName === "sms-activity") {
          loadSmsOutbox && loadSmsOutbox();
        } else {
          loadOutbox && loadOutbox();
        }
      }, 200);
    } catch (e) {
      console.error(e);
      if (chSNRes) chSNRes.textContent = "Failed to send.";
      alert("Failed to enqueue messages.");
    }
  }

  // chasing event wiring
  chEnabled?.addEventListener("change", () => {
    if (chSeqDefault) chSeqDefault.disabled = chEnabled.value !== "true";
    if (chDelivery) chDelivery.disabled = chEnabled.value !== "true";
    saveChasingGlobals();
  });
  chHour?.addEventListener("change", saveChasingGlobals);
  chSeqDefault?.addEventListener("change", () => {
    if (chEnabled?.value !== "true") return;
    saveChasingGlobals();
  });
  chDelivery?.addEventListener("change", saveChasingGlobals);
  chSaveBtn?.addEventListener("click", (e) => {
    e.preventDefault();
    saveChasingGlobals();
  });

  chSNPick?.addEventListener("click", (e) =>
    openSendNowPicker(e.currentTarget)
  );
  chSNBtn?.addEventListener("click", sendNowChasing);

    // Open the chasing exclusions picker when you click the Customers button
  chExclBtn?.addEventListener("click", (e) => {
    openChasingExclPopover(e.currentTarget);
  });


  // ============================================================
  // EMAIL ACTIVITY (Outbox)
  // ============================================================

  const OA_API   = "/api/outbox";
  const oaRows   = q("#oa_rows");
  const oaEmpty  = q("#oa_empty");
  const oaStatus = q("#oa_status");
  const oaSearch = q("#oa_search");
  const oaPer    = q("#oa_per");
  const oaPrev   = q("#oa_prev");
  const oaNext   = q("#oa_next");
  const oaInfo   = q("#oa_page_info");

  const oaPager = {
    page: 1,
    per: Number(oaPer?.value || 50),
    pages: 1,
    total: 0,
  };
  let oaItems = [];
  let oaQuery = { status: "all", search: "" };

  function extractBounceReason(row) {
    try {
      const d =
        typeof row.delivery_detail === "string"
          ? JSON.parse(row.delivery_detail)
          : row.delivery_detail || {};
      return d.Details || d.Description || d.BounceType || "";
    } catch {
      return "";
    }
  }

  function extractProviderError(row) {
    const err = String(row.last_error || "");
    try {
      const start = err.indexOf("{");
      const end = err.lastIndexOf("}");
      if (start >= 0 && end > start) {
        const j = JSON.parse(err.slice(start, end + 1));
        const code = Number(j.ErrorCode);
        const msg = String(j.Message || "").trim();
        const friendly = {
          412: "Postmark sandbox: recipient must match From domain until approval.",
          300: "Invalid server token (check Postmark Server Token).",
          401: "Unauthorized (check Postmark Server Token).",
          405: "Recipient is inactive/suppressed.",
        };
        return friendly[code] || msg || `Provider error (${code})`;
      }
    } catch {}
    const m = err.match(/"Message":"([^"]+)/);
    if (m) return m[1];
    return null;
  }

  function friendlyOutboxStatus(row) {
    if (row.delivery_status === "delivered") {
      return { badge: "delivered", text: "Delivered" };
    }
    if (row.delivery_status === "bounced") {
      const reason = extractBounceReason(row);
      return {
        badge: "bounced",
        text: reason ? `Bounced: ${reason}` : "Bounced",
      };
    }
    if (row.delivery_status === "complained") {
      return {
        badge: "complained",
        text: "Marked as spam by recipient",
      };
    }

    if (row.status === "failed") {
      const msg = extractProviderError(row) || "Failed to send";
      return { badge: "failed", text: msg };
    }
    if (row.status === "processing") {
      return { badge: "processing", text: "Sending…" };
    }
    if (row.status === "sent") {
      return {
        badge: "sent",
        text: "Sent (awaiting delivery confirmation)",
      };
    }
    if (row.status === "queued") {
      if ((row.attempt_count || 0) > 0) {
        return {
          badge: "retrying",
          text: `Retrying (${row.attempt_count})`,
        };
      }
      return { badge: "queued", text: "Queued" };
    }
    return { badge: row.status || "unknown", text: "—" };
  }
  window.friendlyOutboxStatus = friendlyOutboxStatus;

  const fmtDateTime = (iso) => {
    if (!iso) return "—";
    if (window.AppDate?.formatDateTime) return AppDate.formatDateTime(iso);
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  function oaSetPagerUI() {
    if (oaPer) oaPer.value = String(oaPager.per);
    if (oaInfo)
      oaInfo.textContent = `Page ${oaPager.page} / ${oaPager.pages} (${oaPager.total} messages)`;
    if (oaPrev) oaPrev.disabled = oaPager.page <= 1;
    if (oaNext) oaNext.disabled = oaPager.page >= oaPager.pages;
  }

  function renderOutbox() {
    if (!oaRows) return;
    if (!oaItems.length) {
      oaRows.innerHTML = "";
      if (oaEmpty) oaEmpty.style.display = "block";
      return;
    }
    if (oaEmpty) oaEmpty.style.display = "none";

    oaRows.innerHTML = oaItems
      .map((x) => {
        const ui = friendlyOutboxStatus
          ? friendlyOutboxStatus(x)
          : { badge: x.status, text: "" };
        const pill = `<span class="pill ${ui.badge}">${ui.badge}</span>`;
        const next =
          x.status === "queued" &&
          (x.attempt_count || 0) > 0 &&
          x.next_attempt_at
            ? `<div class="muted" style="font-size:12px">Next try: ${fmtDateTime(
                x.next_attempt_at
              )}</div>`
            : "";
        const detail = ui.text
          ? `<div class="muted" style="font-size:12px">${ui.text}</div>`
          : "";

        return `
          <tr>
            <td>${fmtDateTime(x.created_at)}</td>
            <td>
              ${x.to_email || ""}
              ${
                x.customer_name
                  ? `<div class="muted" style="font-size:12px">${x.customer_name}</div>`
                  : ""
              }
            </td>
            <td>${x.subject ? x.subject.replace(/</g, "&lt;") : ""}</td>
            <td>${pill}${detail}${next}</td>
            <td style="text-align:right;">${x.attempt_count || 0}</td>
          </tr>
        `;
      })
      .join("");
  }

  async function loadOutbox() {
    if (!oaRows) return;
    const params = new URLSearchParams({
      status: oaQuery.status || "all",
      page: String(oaPager.page),
      per_page: String(oaPager.per),
      channel: "email",
    });
    if (oaQuery.search) params.set("search", oaQuery.search);

    oaRows.innerHTML = `<tr><td colspan="5" class="muted">Loading…</td></tr>`;
    try {
      const r = await fetch(`${OA_API}?${params.toString()}`, {
        cache: "no-store",
      });
      if (!r.ok) throw new Error(String(r.status));
      const d = await r.json();
      oaItems = d.items || [];
      oaPager.page = d.page;
      oaPager.per = d.per_page;
      oaPager.pages = d.pages;
      oaPager.total = d.total;
      oaSetPagerUI();
    } catch (e) {
      oaItems = [];
      oaPager.pages = 1;
      oaPager.total = 0;
      oaSetPagerUI();
      oaRows.innerHTML = `<tr><td colspan="5" class="muted">Failed to load.</td></tr>`;
      return;
    }
    renderOutbox();
  }

  let oaTimer = null;
  oaStatus?.addEventListener("change", () => {
    oaQuery.status = oaStatus.value || "all";
    oaPager.page = 1;
    loadOutbox();
  });
  oaPer?.addEventListener("change", () => {
    oaPager.per = Number(oaPer.value) || 50;
    oaPager.page = 1;
    loadOutbox();
  });
  oaPrev?.addEventListener("click", () => {
    if (oaPager.page > 1) {
      oaPager.page -= 1;
      loadOutbox();
    }
  });
  oaNext?.addEventListener("click", () => {
    if (oaPager.page < oaPager.pages) {
      oaPager.page += 1;
      loadOutbox();
    }
  });
  oaSearch?.addEventListener("input", () => {
    clearTimeout(oaTimer);
    oaTimer = setTimeout(() => {
      oaQuery.search = (oaSearch.value || "").trim();
      oaPager.page = 1;
      loadOutbox();
    }, 250);
  });

  // If user clicks the “Email activity” tab, lazy-load table
  document.addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (!tab) return;
    if ((tab.dataset.tab || "") === "activity") {
      if (oaItems.length === 0) loadOutbox();
    }
  });

  // ============================================================
  // SMS ACTIVITY (Outbox)
  // ============================================================

  const saRows   = q("#sa_rows");
  const saEmpty  = q("#sa_empty");
  const saStatus = q("#sa_status");
  const saSearch = q("#sa_search");
  const saPer    = q("#sa_per");
  const saPrev   = q("#sa_prev");
  const saNext   = q("#sa_next");
  const saInfo   = q("#sa_page_info");

  const saPager = {
    page: 1,
    per: Number(saPer?.value || 50),
    pages: 1,
    total: 0,
  };
  let saItems = [];
  let saQuery = { status: "all", search: "" };

  function renderSmsOutbox() {
    if (!saRows) return;
    if (!saItems.length) {
      saRows.innerHTML = "";
      if (saEmpty) saEmpty.style.display = "block";
      return;
    }
    if (saEmpty) saEmpty.style.display = "none";

    saRows.innerHTML = saItems
      .map((x) => {
        const ui = friendlyOutboxStatus
          ? friendlyOutboxStatus(x)
          : { badge: x.status, text: "" };
        const pill = `<span class="pill ${ui.badge}">${ui.badge}</span>`;
        const message = (x.body || "").replace(/</g, "&lt;");
        const detail = ui.text
          ? `<div class="muted" style="font-size:12px">${ui.text}</div>`
          : "";

        return `
          <tr>
            <td>${fmtDateTime(x.created_at)}</td>
            <td>
              ${x.to_email || ""}
              ${
                x.customer_name
                  ? `<div class="muted" style="font-size:12px">${x.customer_name}</div>`
                  : ""
              }
            </td>
            <td>${message}</td>
            <td>${pill}${detail}</td>
            <td style="text-align:right;">${x.attempt_count || 0}</td>
          </tr>
        `;
      })
      .join("");
  }

  async function loadSmsOutbox() {
    if (!saRows) return;
    const params = new URLSearchParams({
      status: saQuery.status || "all",
      page: String(saPager.page),
      per_page: String(saPager.per),
      channel: "sms",
    });
    if (saQuery.search) params.set("search", saQuery.search);

    saRows.innerHTML = `<tr><td colspan="5" class="muted">Loading…</td></tr>`;
    try {
      const r = await fetch(`${OA_API}?${params.toString()}`, {
        cache: "no-store",
      });
      if (!r.ok) throw new Error(String(r.status));
      const d = await r.json();
      saItems = d.items || [];
      saPager.page = d.page;
      saPager.per = d.per_page;
      saPager.pages = d.pages;
      saPager.total = d.total;
      if (saInfo)
        saInfo.textContent = `Page ${saPager.page} / ${saPager.pages} (${saPager.total} messages)`;
      if (saPrev) saPrev.disabled = saPager.page <= 1;
      if (saNext) saNext.disabled = saPager.page >= saPager.pages;
    } catch (e) {
      saItems = [];
      saPager.pages = 1;
      saPager.total = 0;
      if (saInfo)
        saInfo.textContent = `Page ${saPager.page} / ${saPager.pages} (${saPager.total} messages)`;
      saRows.innerHTML = `<tr><td colspan="5" class="muted">Failed to load.</td></tr>`;
      return;
    }
    renderSmsOutbox();
  }

  let saTimer = null;
  saStatus?.addEventListener("change", () => {
    saQuery.status = saStatus.value || "all";
    saPager.page = 1;
    loadSmsOutbox();
  });
  saPer?.addEventListener("change", () => {
    saPager.per = Number(saPer.value) || 50;
    saPager.page = 1;
    loadSmsOutbox();
  });
  saPrev?.addEventListener("click", () => {
    if (saPager.page > 1) {
      saPager.page -= 1;
      loadSmsOutbox();
    }
  });
  saNext?.addEventListener("click", () => {
    if (saPager.page < saPager.pages) {
      saPager.page += 1;
      loadSmsOutbox();
    }
  });
  saSearch?.addEventListener("input", () => {
    clearTimeout(saTimer);
    saTimer = setTimeout(() => {
      saQuery.search = (saSearch.value || "").trim();
      saPager.page = 1;
      loadSmsOutbox();
    }, 250);
  });

  document.addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (!tab) return;
    if ((tab.dataset.tab || "") === "sms-activity") {
      if (saItems.length === 0) loadSmsOutbox();
    }
  });

  // -----------------------
  // init on DOM ready
  // -----------------------
  document.addEventListener("DOMContentLoaded", async () => {
    await loadGlobals();
    await loadChasingGlobals();

    // If activity tab is already active on load, pull immediately:
    if (q("#tab-activity")?.classList.contains("active")) {
      loadOutbox();
    }
    if (q("#tab-sms-activity")?.classList.contains("active")) {
      loadSmsOutbox();
    }
  });
})();
