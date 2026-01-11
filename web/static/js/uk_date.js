// /static/js/uk_date.js — ADD: client-side due-date calculator

let _locale = "en-GB";   // default
let _time = "24h";       // "24h" | "12h"

function setLocale(loc) { if (loc) _locale = loc; }
function setTimeFormat(t) { if (t) _time = t; }

/** Parse a DB/API date ("YYYY-MM-DD" or ISO) as a *date-only* (no TZ drift) */
function parseDateOnly(v) {
  if (!v) return null;
  const s = String(v);
  const ymd = s.slice(0, 10);
  const [y, m, d] = ymd.split("-").map(Number);
  if (!y || !m || !d) return null;
  // keep as UTC-midnight to avoid local TZ shifts when adding days / month math
  return new Date(Date.UTC(y, m - 1, d));
}

/** Format date as dd/mm/yyyy (UK) or mm/dd/yyyy (US), per locale */
function formatDate(v) {
  const d = parseDateOnly(v);
  if (!d) return "";
  return new Intl.DateTimeFormat(_locale, { day: "2-digit", month: "2-digit", year: "numeric" }).format(d);
}

/** Format date-time as per locale + time format */
function formatDateTime(v) {
  if (!v) return "";
  const d = new Date(v);
  if (isNaN(d)) return "";
  const hourCycle = _time === "12h" ? "h12" : "h23";
  return new Intl.DateTimeFormat(_locale, {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", hourCycle
  }).format(d);
}

/** Return "YYYY-MM-DD" from a Date/date-like (expects a date-only or Date) */
function toISODate(v) {
  const d = v instanceof Date ? v : parseDateOnly(v);
  return d ? new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()))
              .toISOString().slice(0, 10) : null;
}

/* ---------- DUE DATE: mirror server compute_due_date ---------- */

function endOfNextMonthUTC(dUTC) {
  const y = dUTC.getUTCFullYear();
  const m = dUTC.getUTCMonth(); // 0–11
  // first day of next month (UTC)
  const firstNext = new Date(Date.UTC(m === 11 ? y + 1 : y, (m + 1) % 12, 1));
  // first day of the month after that (UTC)
  const firstAfter = new Date(Date.UTC(firstNext.getUTCMonth() === 11 ? firstNext.getUTCFullYear() + 1 : firstNext.getUTCFullYear(),
                                       (firstNext.getUTCMonth() + 1) % 12,
                                       1));
  // subtract one day
  return new Date(firstAfter.getTime() - 24 * 3600 * 1000);
}

/**
 * computeDueDateJs(issueISO, terms_type, terms_days) -> "YYYY-MM-DD" | null
 * terms_type: "net_30" | "net_60" | "month_following" | "custom"
 * If terms not provided, returns null so the UI leaves Due blank.
 */
function computeDueDateJs(issueISO, terms_type, terms_days) {
  const base = parseDateOnly(issueISO);
  if (!base) return null;

  let out;
  if (terms_type === "net_30") {
    out = new Date(base.getTime() + 30 * 24 * 3600 * 1000);
  } else if (terms_type === "net_60") {
    out = new Date(base.getTime() + 60 * 24 * 3600 * 1000);
  } else if (terms_type === "month_following") {
    out = endOfNextMonthUTC(base);
  } else if (terms_type === "custom" && Number(terms_days)) {
    out = new Date(base.getTime() + Number(terms_days) * 24 * 3600 * 1000);
  } else {
    // No terms configured → leave blank; let user fill it
    return null;
  }
  return toISODate(out);
}

window.AppDate = {
  setLocale, setTimeFormat, formatDate, formatDateTime, toISODate,
  computeDueDate: computeDueDateJs // optional namespaced access
};
// keep existing wizard working without changes:
window.computeDueDateJs = computeDueDateJs;
