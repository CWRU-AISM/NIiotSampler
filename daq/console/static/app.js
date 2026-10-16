"use strict";
// Sampling console front end. Talks to server.py: REST for state/actions, /ws/live for live frames.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 4) => (v == null || !isFinite(v) ? "–" : Number(v).toPrecision(d).replace(/\.?0+e/, "e").replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, ""));
const store = {
  get(k, d) { try { const v = localStorage.getItem("console." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("console." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (err ? " err" : "");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), err ? 7000 : 3500);
}

const DEFAULT_FAULTS = [];  // fault buttons come from your own labels and the checklist
const DURATIONS = [10, 30, 60, 120, 300];
const ICON = { pass: "✓", warn: "!", fail: "✗", info: "i" };
const STATE = { st: null, chanKey: "", plots: {}, frozen: false, frame: null, runs: [], lastRunFile: null, tab: "acquire" };

// ============================================================================ plots
function markerPlugin(getMarkers) {
  return {
    hooks: {
      draw: (u) => {
        const ms = getMarkers();
        if (!ms || !ms.length) return;
        const ctx = u.ctx, dpr = devicePixelRatio, top = u.bbox.top, h = u.bbox.height;
        ctx.save();
        ctx.font = `${11 * dpr}px Segoe UI, sans-serif`;
        ms.forEach((m, i) => {
          if (!(m.f > u.scales.x.min && m.f < u.scales.x.max)) return;
          const x = Math.round(u.valToPos(m.f, "x", true));
          ctx.strokeStyle = m.color; ctx.fillStyle = m.color; ctx.lineWidth = dpr;
          ctx.setLineDash([4 * dpr, 4 * dpr]);
          ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + h); ctx.stroke();
          ctx.fillText(m.label, x + 3 * dpr, top + (12 + 12 * (i % 3)) * dpr);
        });
        ctx.restore();
      },
    },
  };
}

function makePlot(el, { xlabel, ylabel, color, log = false, markers = null, height = 190, zoom = true, extraSeries = [] }) {
  const ink = css("--muted"), grid = css("--grid");
  const axis = { stroke: ink, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid } };
  const yScale = log
    ? { distr: 3, range: logRange }
    : { auto: true, range: (u, mn, mx) => { const p = (mx - mn) * 0.08 || Math.abs(mx) * 0.1 || 1e-3; return [mn - p, mx + p]; } };
  const series = [{ label: xlabel }, { label: ylabel, stroke: color, width: 1, points: { show: false } },
    ...extraSeries.map((s) => ({ label: s.label, stroke: s.color, width: 1, points: { show: false }, dash: s.dash }))];
  const u = new uPlot({
    width: Math.max(200, el.clientWidth), height,
    scales: { x: { time: false }, y: yScale },
    axes: [{ ...axis, label: xlabel, labelSize: 18, size: 36 },
      { ...axis, label: ylabel, labelSize: 18, size: 62, values: (u, ticks) => ticks.map(tickFmt) }],
    series, legend: { show: extraSeries.length > 0, live: false },
    cursor: { drag: { x: zoom, y: false }, points: { show: false } },
    plugins: markers ? [markerPlugin(markers)] : [],
  }, [[], [], ...extraSeries.map(() => [])], el);
  u._zoomed = false;
  u.hooks.setSelect = [(uu) => { if (uu.select.width > 2) uu._zoomed = true; }];
  u.over.addEventListener("dblclick", () => { u._zoomed = false; });
  return u;
}

// Log axis: whole decades, at most 5 below the peak (uPlot's log ticks throw on odd ranges).
function logRange(u, mn, mx) {
  if (!(mx > 0) || !isFinite(mx)) mx = 1;
  const hi = Math.ceil(Math.log10(mx * 1.5));
  let lo = Math.floor(Math.log10(Math.max(mn > 0 ? mn : 0, mx * 1e-5, 1e-12)));
  if (!(lo < hi)) lo = hi - 1;
  return [10 ** lo, 10 ** hi];
}

function tickFmt(v) {
  if (v == null) return "";
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a < 1e-3 || a >= 1e5) return v.toExponential(0).replace("e-", "e−");
  return String(+v.toPrecision(3));
}

function setPlotData(u, data) {
  u.setData(data, !u._zoomed);
}

const ro = new ResizeObserver((entries) => {
  for (const e of entries) {
    const u = e.target._uplot;
    if (u && e.contentRect.width > 0) u.setSize({ width: e.contentRect.width, height: u.height });
  }
});
function attach(el, u) { el._uplot = u; ro.observe(el); }

// ============================================================================ markers
function poles() { return (STATE.st && STATE.st.motor && STATE.st.motor.poles) || 2; }
function lineHz() { return (STATE.st && STATE.st.motor && STATE.st.motor.line_hz) || 60; }

function markersFor(kind, driveHz, derived) {
  const out = [];
  const mcol = css("--marker"), acc = css("--c1"), line = css("--warn");
  const curHz = (derived && derived.current_hz) || driveHz;
  if (kind === "current_clamp") {
    if (curHz) [1, 3, 5, 7].forEach((k) => out.push({ f: k * curHz, label: k === 1 ? "f" : `${k}f`, color: k === 1 ? acc : mcol }));
  } else if (curHz) {
    const sync = curHz / (poles() / 2);
    const shaft = (derived && derived.shaft_hz) || sync * 0.97;
    [1, 2, 3].forEach((k) => out.push({ f: k * shaft, label: `${k}×`, color: k === 1 ? acc : mcol }));
  }
  out.push({ f: lineHz(), label: `${lineHz()} Hz`, color: line });
  return out;
}

// ============================================================================ status
async function pollState() {
  try {
    const st = await api("/api/state");
    STATE.st = st;
    renderStatus(st);
  } catch (e) {
    $("#status-pill").textContent = "server offline";
    $("#status-pill").className = "pill error";
  }
  setTimeout(pollState, 1000);
}

function renderStatus(st) {
  $("#mode").textContent = st.mode === "daq" ? "hardware" : st.mode;
  $("#mode").className = "badge " + st.mode;
  const pill = $("#status-pill");
  if (st.recording) { pill.textContent = "● RECORDING"; pill.className = "pill recording"; }
  else { pill.textContent = st.status === "stopped" ? "stream off" : st.status; pill.className = "pill " + st.status; }
  const off = st.status === "stopped";
  const sb = $("#btn-stream");
  sb.textContent = off ? "▶ Start stream" : "■ Stop stream";
  sb.disabled = !!st.recording;
  $("#live-note").classList.toggle("hidden", !off);
  $("#live-plots").classList.toggle("hidden", off);
  const d = st.device || {};
  $("#device").textContent = d.product_type ? `${d.chassis ? d.chassis + " · " : ""}${d.product_type}${d.serial ? " S/N " + d.serial : ""}` : "";
  $("#rate").textContent = st.rate ? `${st.rate.toLocaleString()} S/s` : "";
  const ban = $("#banner");
  if (st.status === "error") {
    ban.classList.remove("hidden");
    $("#banner-text").textContent = (st.error_hint ? st.error_hint + "  " : "") + `[DAQmx ${st.error_code ?? ""}] ` + (st.error || "").split("\n")[0];
    $("#btn-takeover").classList.toggle("hidden", st.error_action !== "reserve");
  } else ban.classList.add("hidden");
  const mw = $("#module-warning");
  mw.textContent = st.module_warning ? "Module check: " + st.module_warning : "";
  mw.classList.toggle("hidden", !st.module_warning);
  $("#demo-ctl").classList.toggle("hidden", st.mode !== "demo");

  const key = st.channels.map((c) => c.name + c.kind + c.units + c.range).join("|");
  if (key !== STATE.chanKey) { STATE.chanKey = key; buildLivePlots(); }
  renderRecording(st);
  clOnState(st);
  sendOpt();
  fitLivePlots();  // the sweep banner / last-run card change the space left for the plots
}

// ============================================================================ live view
function liveOpt() {
  return {
    window_s: +$("#opt-window").value, fmax: +$("#opt-fmax").value, avg: +$("#opt-avg").value,
    drive_hz: clLiveDriveHz() || +$("#freq").value || null, paused: STATE.frozen || STATE.tab !== "acquire",
  };
}

// Live plot height: fit all channels between the toolbar and the docked naming / record panel.
function livePlotHeight() {
  const n = Math.max(1, (STATE.st && STATE.st.channels.length) || 1);
  const box = $("#live-plots"), dock = $("#dock");
  const top = box.getBoundingClientRect().top + window.scrollY;
  const avail = window.innerHeight - top - (dock ? dock.offsetHeight : 0) - 40;
  return Math.round(Math.min(220, Math.max(110, avail / n - 46)));
}
function fitLivePlots() {
  const h = livePlotHeight();
  Object.values(STATE.plots).forEach((p) => {
    if (p.wave.height !== h) { p.wave.setSize({ width: p.wave.width, height: h }); p.spec.setSize({ width: p.spec.width, height: h }); }
  });
}
window.addEventListener("resize", () => fitLivePlots());

function buildLivePlots() {
  const box = $("#live-plots");
  Object.values(STATE.plots).forEach((p) => { p.wave.destroy(); p.spec.destroy(); });
  STATE.plots = {};
  box.innerHTML = "";
  const colors = [css("--c0"), css("--c1"), css("--c2"), css("--c3")];
  const log = $("#opt-log").checked;
  STATE.st.channels.forEach((c, i) => {
    const el = document.createElement("div");
    el.className = "chan";
    el.innerHTML = `<div class="chan-head"><span><span class="title">${esc(c.name)}</span>
      <span class="muted small">${esc(c.physical)} · ${esc(c.kind)} · ±${fmt(c.range, 3)} ${esc(c.units)}${c.location ? " · " + esc(c.location) : ""}</span></span>
      <span class="stats" id="stats-${i}"></span></div>
      <div class="plots2"><div class="plotbox" id="w-${i}"></div><div class="plotbox" id="s-${i}"></div></div>`;
    box.appendChild(el);
    const wEl = $(`#w-${i}`, el), sEl = $(`#s-${i}`, el);
    const h = livePlotHeight();
    const wave = makePlot(wEl, { xlabel: "time [s]", ylabel: c.units, color: colors[i % 4], zoom: false, height: h });
    const spec = makePlot(sEl, {
      xlabel: "frequency [Hz]", ylabel: `${c.units} amplitude`, color: colors[i % 4], log, height: h,
      markers: () => ($("#opt-markers").checked ? markersFor(c.kind, +$("#freq").value || null, STATE.frame && STATE.frame.checks && STATE.frame.checks.derived) : null),
    });
    attach(wEl, wave); attach(sEl, spec);
    STATE.plots[c.name] = { wave, spec, idx: i, ch: c };
  });
}

let ws;
function connectWs() {
  ws = new WebSocket(`ws://${location.host}/ws/live`);
  ws.onopen = () => ws.send(JSON.stringify(liveOpt()));
  ws.onmessage = (ev) => {
    const fr = JSON.parse(ev.data);
    if (STATE.frozen) return;
    STATE.frame = fr;
    renderFrame(fr);
  };
  ws.onclose = () => setTimeout(connectWs, 1500);
}
function sendOpt() { if (ws && ws.readyState === 1) ws.send(JSON.stringify(liveOpt())); }

function renderFrame(fr) {
  try { drawFrame(fr); clOnFrame(fr); }
  catch (e) { console.warn("plot error, rebuilding plots", e); buildLivePlots(); }
}

function drawFrame(fr) {
  for (const c of fr.channels || []) {
    const p = STATE.plots[c.name];
    if (!p) continue;
    setPlotData(p.wave, [c.wave.t, c.wave.y]);
    const a = $("#opt-log").checked ? c.spec.a.map((v) => Math.max(v, 1e-9)) : c.spec.a;
    setPlotData(p.spec, [c.spec.f, a]);
    const s = c.stats, u = p.ch.units, used = Math.min(1, s.range_used || 0);
    const col = used > 0.98 ? "var(--fail)" : used > 0.9 ? "var(--warn)" : "var(--pass)";
    $(`#stats-${p.idx}`).innerHTML = `<span>RMS <b>${fmt(s.rms)}</b> ${u}</span><span>peak <b>${fmt(s.peak)}</b></span>
      <span>crest <b>${fmt(s.crest, 3)}</b></span><span>dominant <b>${fmt(s.dominant_hz, 5)}</b> Hz</span>
      <span title="share of the input range used">range <span class="rangebar"><i style="width:${(used * 100).toFixed(0)}%;background:${col}"></i></span> ${(used * 100).toFixed(0)}%</span>`;
  }
  renderChecks($("#live-checks"), fr.checks, true);
  const d = fr.checks && fr.checks.derived;
  const fm = $("#freq-measured");
  if (d && d.current_hz) {
    fm.innerHTML = `measured current fundamental <b>${d.current_hz.toFixed(2)} Hz</b> · <a id="use-measured">use</a>`;
    $("#use-measured").onclick = () => { $("#freq").value = d.current_hz.toFixed(2); onFormChange(); };
  } else fm.textContent = "";
}

function renderChecks(el, rep, live) {
  if (!rep) { el.innerHTML = `<div class="muted">${live ? "waiting for 1 s of data…" : "no report"}</div>`; return; }
  if (rep.error) { el.innerHTML = `<div class="check fail"><span class="ic">✗</span><div>${esc(rep.error)}</div></div>`; return; }
  const n = { pass: 0, warn: 0, fail: 0 };
  rep.checks.forEach((c) => { if (c.status in n) n[c.status]++; });
  const order = { fail: 0, warn: 1, pass: 2, info: 3 };
  const items = [...rep.checks].sort((a, b) => order[a.status] - order[b.status]);
  el.innerHTML = `<div class="sumline"><span class="pass">✓ ${n.pass}</span><span class="warn">! ${n.warn}</span><span class="fail">✗ ${n.fail}</span></div>` +
    items.map((c) => `<div class="check ${c.status}"><span class="ic">${ICON[c.status]}</span><div>
      <div><span class="ch">${esc(c.channel)}</span> ${esc(c.title)}</div>${c.detail ? `<div class="det">${esc(c.detail)}</div>` : ""}</div></div>`).join("");
}

// ============================================================================ two-click confirm
// Destructive buttons need a second click within 4 s (no browser dialogs).
function armed(key) { return STATE.confirm && STATE.confirm.key === key && Date.now() < STATE.confirm.until; }
function arm(key, rerender) {
  STATE.confirm = { key, until: Date.now() + 4000 };
  rerender();
  setTimeout(rerender, 4100);
}

// ============================================================================ naming + recording
function slug(s) { return String(s).replace(/[^A-Za-z0-9.]+/g, "-").replace(/^-+|-+$/g, "") || "na"; }
function pad(n) { return String(n).padStart(2, "0"); }
function fileName() {
  const d = new Date();
  const ts = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  const f = form();
  const parts = [ts, f.fault ? slug(f.fault) : "<fault>", `${f.freq ? Number(f.freq) : "<drive>"}Hz`];
  if (f.load && f.load.toLowerCase() !== "none") parts.push(slug(f.load));
  if (f.tag) parts.push(slug(f.tag));
  return parts.join("_") + ".h5";
}
function form() {
  return {
    fault: STATE.fault || "", freq: $("#freq").value, load: $("#load").value.trim() || "none", operator: $("#operator").value.trim(),
    tag: $("#tag").value.trim(), notes: $("#notes").value.trim(), duration: $("#duration").value, settle: $("#settle").value,
  };
}

function renderFaultChips() {
  const custom = store.get("customFaults", []);
  const box = $("#fault-chips");
  box.innerHTML = "";
  [...DEFAULT_FAULTS, ...custom].forEach((f) => {
    const b = document.createElement("button");
    b.className = "chip" + (STATE.fault === f ? " on" : "");
    b.innerHTML = esc(f) + (custom.includes(f) ? '<span class="x" title="remove">×</span>' : "");
    b.onclick = (ev) => {
      if (ev.target.classList.contains("x")) {
        store.set("customFaults", custom.filter((x) => x !== f));
        if (STATE.fault === f) STATE.fault = "";
      } else STATE.fault = f;
      renderFaultChips(); onFormChange();
    };
    box.appendChild(b);
  });
}
function addCustomFault() {
  const v = slug($("#fault-custom").value.trim()).replace(/-/g, "_");
  if (!v || v === "na") return;
  const custom = store.get("customFaults", []);
  if (!custom.includes(v) && !DEFAULT_FAULTS.includes(v)) store.set("customFaults", [...custom, v]);
  STATE.fault = v; $("#fault-custom").value = "";
  renderFaultChips(); onFormChange();
}
function renderDurChips() {
  const box = $("#dur-chips");
  box.innerHTML = "";
  DURATIONS.forEach((d) => {
    const b = document.createElement("button");
    b.className = "chip" + (+$("#duration").value === d ? " on" : "");
    b.textContent = d >= 60 ? `${d / 60} min` : `${d} s`;
    b.onclick = () => { $("#duration").value = d; onFormChange(); };
    box.appendChild(b);
  });
}

function onFormChange() {
  const f = form();
  store.set("form", { fault: f.fault, freq: f.freq, load: f.load, operator: f.operator, duration: f.duration, settle: f.settle });
  $("#fname-preview").textContent = fileName();
  renderDurChips();
  const today = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const same = STATE.runs.filter((r) => r.name.startsWith(today) && r.fault === f.fault && Number(r.drive_freq_hz) === Number(f.freq) && (r.load || "none") === f.load);
  $("#dup-warning").textContent = same.length ? `${same.length} run(s) with this fault / Hz / load already today` : "";
  sendOpt();
}

function renderRecording(st) {
  const btn = $("#btn-record"), prog = $("#rec-progress");
  const r = st.recording;
  if (r) {
    btn.textContent = "■ Stop (keeps partial file)"; btn.classList.add("stop"); btn.disabled = false;
    prog.classList.remove("hidden");
    const dz = $("#btn-discard");
    dz.classList.remove("hidden");
    dz.classList.toggle("armed", armed("discard"));
    dz.textContent = armed("discard") ? "Click again to discard (moves to _trash)" : "✗ Discard this recording";
    const frac = r.duration_s ? r.elapsed_s / r.duration_s : 0;
    $(".bar", prog).style.width = `${(frac * 100).toFixed(1)}%`;
    $(".ptext", prog).textContent = r.settling ? "settling…" : `${r.elapsed_s.toFixed(1)} / ${r.duration_s} s  ·  ${r.file}`;
  } else {
    btn.textContent = st.status === "stopped" ? "● Record (starts the stream)" : "● Record"; btn.classList.remove("stop");
    btn.disabled = !["running", "stopped"].includes(st.status);
    prog.classList.add("hidden");
    $("#btn-discard").classList.add("hidden");
  }
  const lr = st.last_run, box = $("#last-run");
  if (!lr) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  noteLastRun(lr);
  clOnLastRun(lr);
  if (lr.discarded || lr.deleted) {
    setCard(box, `<div><b>${lr.discarded ? "Recording discarded" : "Run deleted"}</b></div>
      <div class="muted small">moved to <code>${esc(lr.trash || "_trash")}</code> (not in the run list; move it back in Explorer to restore)</div>`);
    return;
  }
  const rep = lr.report;
  let checks = '<div class="muted small">checking saved file…</div>';
  if (rep && !rep.error) {
    const bad = rep.checks.filter((c) => c.status === "fail" || c.status === "warn");
    const n = (s) => rep.checks.filter((c) => c.status === s).length;
    checks = `<div class="sumline"><span class="pass">✓ ${n("pass")}</span><span class="warn">! ${n("warn")}</span><span class="fail">✗ ${n("fail")}</span></div>` +
      bad.map((c) => `<div class="check ${c.status}"><span class="ic">${ICON[c.status]}</span><div><span class="ch">${esc(c.channel)}</span> ${esc(c.title)}</div></div>`).join("");
  } else if (rep && rep.error) checks = `<div class="check fail"><span class="ic">✗</span><div>${esc(rep.error)}</div></div>`;
  const del = armed("del:" + lr.file);
  const changed = setCard(box, `<div><b>${lr.completed ? "Saved" : "Saved (partial)"}</b> · ${fmt(lr.duration_s, 4)} s</div>
    <div class="file"><code>${esc(lr.file)}</code></div>${lr.error ? `<div class="warn-text small">${esc(lr.error)}</div>` : ""}
    ${checks}<div class="row"><button class="ghost small" id="btn-open-last">Open in review</button>
    <button class="ghost small danger ${del ? "armed" : ""}" id="btn-del-last">${del ? "Click again to delete" : "Delete this run"}</button></div>`);
  if (changed) {
    $("#btn-open-last").onclick = () => { switchTab("review"); openRun(lr.file); };
    $("#btn-del-last").onclick = () => deleteRun(lr.file, () => renderRecording(STATE.st));
  }
}

// Only touch the DOM when the card changes (the state poll runs every second).
function setCard(box, html) {
  if (box._html === html) return false;
  box._html = html; box.innerHTML = html;
  return true;
}

function noteLastRun(lr) {
  if (lr.file === STATE.lastRunFile) return;
  const first = STATE.lastRunFile === null;
  STATE.lastRunFile = lr.file;
  if (!first) loadRuns();
}

async function deleteRun(file, rerender) {
  if (!armed("del:" + file)) return arm("del:" + file, rerender);
  STATE.confirm = null;
  try {
    const r = await api("/api/run/delete", { file });
    toast(`Deleted: moved to ${r.trash}`);
    clOnDeleted(file);
    if (STATE.openFile === file) { STATE.openFile = null; $("#review-detail").innerHTML = `<div class="empty muted">Run deleted (moved to <code>${esc(r.trash)}</code>).</div>`; }
    await loadRuns();
    pollStateOnce();
  } catch (e) { toast(e.message, true); }
}

async function pollStateOnce() {
  try { STATE.st = await api("/api/state"); renderStatus(STATE.st); } catch { /* next poll */ }
}

async function discardRecording() {
  if (!armed("discard")) return arm("discard", () => STATE.st && renderRecording(STATE.st));
  STATE.confirm = null;
  try { await api("/api/record/stop", { discard: true }); toast("Recording discarded"); } catch (e) { toast(e.message, true); }
}

async function toggleRecord() {
  if (STATE.st && STATE.st.recording) { await api("/api/record/stop", {}); return; }
  if (clRecordBlocked()) return;
  const f = form();
  if (!f.fault) return toast("Pick a fault label first", true);
  if (!f.freq) return toast("Enter the drive Hz shown on the Lenze display", true);
  if (!f.operator) toast("Tip: enter an operator name (stored in the file)");
  try {
    const ref = clItemRef();
    if (ref) f.item = ref;
    const r = await api("/api/record/start", f);
    toast("Recording → " + r.file);
    STATE.lastRunFile = STATE.lastRunFile ?? "";
  } catch (e) { toast(e.message, true); }
}

// ============================================================================ review
async function loadRuns() {
  try { STATE.runs = await api("/api/runs"); } catch (e) { return toast(e.message, true); }
  renderRuns(); onFormChange();
}
function renderRuns() {
  const q = $("#runs-filter").value.toLowerCase(), fl = $("#runs-flag").value;
  const rows = STATE.runs.filter((r) => {
    if (fl === "none" ? r.flag : fl && r.flag !== fl) return false;
    return !q || [r.name, r.fault, r.notes, r.operator, r.load, r.start_time].join(" ").toLowerCase().includes(q);
  });
  $("#runs-table tbody").innerHTML = rows.map((r) => `<tr data-file="${esc(r.file)}" class="${r.file === STATE.openFile ? "sel" : ""}" title="${esc(r.name)}${r.notes ? " — " + esc(r.notes) : ""}">
    <td>${esc((r.start_time || "").slice(0, 19).replace("T", " "))}</td><td>${esc(r.fault)}${r.completed === false ? " <span class='warn-text'>(partial)</span>" : ""}${r.simulated ? " <span class='muted'>sim</span>" : ""}</td>
    <td>${fmt(r.drive_freq_hz, 4)}</td><td>${fmt(r.duration_s, 3)}</td><td class="flag-${esc(r.flag)}">${esc(r.flag)}</td></tr>`).join("");
  $("#runs-count").textContent = `${rows.length} of ${STATE.runs.length} runs · ${STATE.st ? STATE.st.data_dir : ""}`;
  $$("#runs-table tbody tr").forEach((tr) => (tr.onclick = () => openRun(tr.dataset.file)));
}

const REVIEW = { plots: [], detail: null, compare: null };
async function openRun(file, fmax) {
  STATE.openFile = file; renderRuns();
  const box = $("#review-detail");
  fmax = fmax || store.get("reviewFmax", 2000);
  box.innerHTML = `<div class="empty muted">loading ${esc(file)}…</div>`;
  let d;
  try { d = await api(`/api/run?file=${encodeURIComponent(file)}&fmax=${fmax}`); } catch (e) { box.innerHTML = `<div class="empty warn-text">${esc(e.message)}</div>`; return; }
  REVIEW.plots.forEach((u) => u.destroy()); REVIEW.plots = [];
  REVIEW.detail = d; REVIEW.compare = null;
  const m = d.meta, run = STATE.runs.find((r) => r.file === file) || {};
  const others = STATE.runs.filter((r) => r.file !== file);
  box.innerHTML = `
    <div class="card">
      <div class="row between"><h2 style="text-transform:none;letter-spacing:0;color:var(--ink)">${esc(file)}</h2>
        <span class="row" style="margin:0">
          <button class="ghost small" data-flag="keep">keep</button><button class="ghost small" data-flag="bad">bad</button><button class="ghost small" data-flag="">clear flag</button><button class="ghost small danger" id="btn-del-run">delete run</button>
          <span class="muted small">flag: <b class="flag-${esc(run.flag || "")}" id="cur-flag">${esc(run.flag || "none")}</b></span></span></div>
      <div class="meta-grid">
        ${[["fault", m.fault], ["drive Hz", m.drive_freq_hz], ["load", m.load], ["start", (m.start_time || "").replace("T", " ").slice(0, 19)],
          ["duration", `${fmt(d.duration_s, 4)} s${m.completed === false ? " (partial)" : ""}`], ["rate", `${d.rate} S/s`], ["operator", m.operator || "–"],
          ["source", m.source || "acquire.py"], ["device", `${d.vib.device_product_type || ""} ${d.vib.device_serial ? "S/N " + d.vib.device_serial : ""}`],
          ["host", m.host], ["simulated", m.simulated ? "yes" : "no"], ["notes", m.notes || "–"]]
          .map(([k, v]) => `<div><span>${k}</span>${esc(v ?? "–")}</div>`).join("")}
      </div>
    </div>
    <div class="card"><h2>Signal checks <span class="muted small">whole file, ${8} s Welch segments</span></h2><div id="rv-checks" class="checklist"></div></div>
    <div class="toolbar">
      <label>f max <select id="rv-fmax">${[200, 500, 1000, 2000, 4000, 6400].map((f) => `<option ${f === +fmax ? "selected" : ""}>${f}</option>`).join("")}</select> Hz</label>
      <label><input id="rv-log" type="checkbox" checked> log spectrum</label>
      <label>compare with <select id="rv-compare"><option value="">—</option>${others.map((r) => `<option value="${esc(r.file)}">${esc(r.name)}</option>`).join("")}</select></label>
      <span class="muted small">drag on a waveform to zoom (loads full-rate data) · double-click to reset</span>
    </div>
    <div id="rv-chans"></div>
    <details class="card"><summary>config.toml stored in this file</summary><pre>${esc(d.config_toml)}</pre></details>`;
  renderChecks($("#rv-checks"), d.report, false);
  $$("[data-flag]", box).forEach((b) => (b.onclick = async () => {
    await api("/api/run/flag", { file, flag: b.dataset.flag });
    $("#cur-flag").textContent = b.dataset.flag || "none"; $("#cur-flag").className = "flag-" + b.dataset.flag;
    loadRuns();
  }));
  const delBtn = $("#btn-del-run");
  delBtn.onclick = () => deleteRun(file, () => {
    const a = armed("del:" + file);
    delBtn.classList.toggle("armed", a);
    delBtn.textContent = a ? "click again to delete" : "delete run";
  });
  $("#rv-fmax").onchange = () => { store.set("reviewFmax", +$("#rv-fmax").value); openRun(file, +$("#rv-fmax").value); };
  $("#rv-log").onchange = () => renderReviewChannels();
  $("#rv-compare").onchange = async () => {
    const cf = $("#rv-compare").value;
    REVIEW.compare = null;
    if (cf) {
      try { REVIEW.compare = await api(`/api/run?file=${encodeURIComponent(cf)}&fmax=${fmax}&spec_only=1`); REVIEW.compare.file = cf; }
      catch (e) { toast(e.message, true); }
    }
    renderReviewChannels();
  };
  renderReviewChannels();
}

function interp(xs, ys, x) {
  const out = new Array(x.length);
  let j = 0;
  for (let i = 0; i < x.length; i++) {
    while (j < xs.length - 2 && xs[j + 1] < x[i]) j++;
    const t = (x[i] - xs[j]) / ((xs[j + 1] - xs[j]) || 1);
    out[i] = x[i] < xs[0] || x[i] > xs[xs.length - 1] ? null : ys[j] + Math.max(0, Math.min(1, t)) * (ys[j + 1] - ys[j]);
  }
  return out;
}

function renderReviewChannels() {
  const d = REVIEW.detail, box = $("#rv-chans");
  REVIEW.plots.forEach((u) => u.destroy()); REVIEW.plots = [];
  box.innerHTML = "";
  const colors = [css("--c0"), css("--c1"), css("--c2"), css("--c3")];
  const log = $("#rv-log").checked;
  const derived = d.report && d.report.derived;
  d.channels.forEach((c, i) => {
    const el = document.createElement("div");
    el.className = "chan";
    const st = (derived && derived[c.name]) || {};
    el.innerHTML = `<div class="chan-head"><span><span class="title">${esc(c.name)}</span> <span class="muted small">${esc(c.physical)} · ${esc(c.kind)}${c.location ? " · " + esc(c.location) : ""}</span></span>
      <span class="stats"><span>RMS <b>${fmt(st.rms)}</b> ${esc(c.units)}</span><span>peak <b>${fmt(st.peak)}</b></span><span>crest <b>${fmt(st.crest, 3)}</b></span>
      <span>range used <b>${st.range_used != null ? (st.range_used * 100).toFixed(0) + "%" : "–"}</b></span></span></div>
      <div class="plotbox" id="rw-${i}"></div>
      <div class="plots2"><div class="plotbox" id="rs-${i}"></div><div class="plotbox"><canvas class="sgram" id="rg-${i}"></canvas>
        <div class="sgram-axis"><span>spectrogram · time →</span><span id="rg-lab-${i}"></span></div></div></div>`;
    box.appendChild(el);
    const wEl = $(`#rw-${i}`), sEl = $(`#rs-${i}`);
    const wave = makePlot(wEl, { xlabel: "time [s]", ylabel: c.units, color: colors[i % 4], height: 170 });
    wave.setData([c.wave.t, c.wave.y]);
    wave.hooks.setSelect.push(async (u) => {
      if (u.select.width < 3) return;
      const t0 = u.posToVal(u.select.left, "x"), t1 = u.posToVal(u.select.left + u.select.width, "x");
      u.setSelect({ width: 0, height: 0 }, false);
      try {
        const w = await api(`/api/run/window?file=${encodeURIComponent(STATE.openFile)}&t0=${t0}&t1=${t1}`);
        REVIEW.plots.forEach((p) => {
          if (p._wave && w[p._chan]) { p._zoomed = true; p.setData([w[p._chan].t, w[p._chan].y]); }
        });
      } catch (e) { toast(e.message, true); }
    });
    wave.over.addEventListener("dblclick", () => {
      REVIEW.plots.forEach((p) => { if (p._wave) { const cc = d.channels.find((x) => x.name === p._chan); p.setData([cc.wave.t, cc.wave.y]); } });
    });
    wave._wave = true; wave._chan = c.name;
    const cmp = REVIEW.compare && REVIEW.compare.channels.find((x) => x.name === c.name);
    const extra = cmp ? [{ label: "compare: " + REVIEW.compare.file.split("/").pop(), color: css("--muted"), dash: [5, 3] }] : [];
    const spec = makePlot(sEl, {
      xlabel: "frequency [Hz]", ylabel: `${c.units} amplitude`, color: colors[i % 4], log, extraSeries: extra, height: 220,
      markers: () => markersFor(c.kind, d.meta.drive_freq_hz, derived),
    });
    const clampA = (a) => (log ? a.map((v) => (v == null ? null : Math.max(v, 1e-9))) : a);
    const data = [c.spec.f, clampA(c.spec.a)];
    if (cmp) data.push(clampA(interp(cmp.spec.f, cmp.spec.a, c.spec.f)));
    spec.setData(data);
    attach(wEl, wave); attach(sEl, spec);
    REVIEW.plots.push(wave, spec);
    drawSgram($(`#rg-${i}`), c.sgram);
    $(`#rg-lab-${i}`).textContent = `0–${Math.round(c.sgram.fmax)} Hz ↑ · ${fmt(c.sgram.lo, 3)}…${fmt(c.sgram.hi, 3)} dB`;
  });
}

const VIRIDIS = [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]];
function cmap(t) {
  t = Math.max(0, Math.min(1, t)) * (VIRIDIS.length - 1);
  const i = Math.min(VIRIDIS.length - 2, Math.floor(t)), f = t - i, a = VIRIDIS[i], b = VIRIDIS[i + 1];
  return [a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2])];
}
function drawSgram(cv, sg) {
  const nt = sg.db.length, nf = sg.nf;
  cv.width = nt; cv.height = nf;
  const ctx = cv.getContext("2d"), img = ctx.createImageData(nt, nf);
  const lo = sg.lo, hi = sg.hi;
  for (let t = 0; t < nt; t++) for (let f = 0; f < nf; f++) {
    const [r, g, b] = cmap((sg.db[t][f] - lo) / (hi - lo || 1));
    const k = ((nf - 1 - f) * nt + t) * 4;
    img.data[k] = r; img.data[k + 1] = g; img.data[k + 2] = b; img.data[k + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  cv.title = `${nt} frames × ${nf} bins, ${sg.dt.toFixed(2)} s per frame`;
}

// ============================================================================ config tab
let CFG = null;
async function loadConfig() {
  try { CFG = await api("/api/config"); } catch (e) { return toast(e.message, true); }
  $("#cfg-module").textContent = CFG.module ? `· limits for ${CFG.module}` : "";
  $("#cfg-path").textContent = STATE.st ? STATE.st.config_path : "config.toml";
  const rs = $("#cfg-rate");
  const rates = CFG.valid_rates.length ? CFG.valid_rates : [CFG.sample_rate];
  rs.innerHTML = rates.map((r) => `<option value="${r}" ${Math.abs(r - CFG.sample_rate) < 1e-3 ? "selected" : ""}>${r.toLocaleString()}</option>`).join("");
  const inputs = ["ai0", "ai1", "ai2", "ai3"].slice(0, CFG.module === "NI 9230" ? 3 : 4);
  $("#cfg-table tbody").innerHTML = CFG.channels.map((c) => {
    const acc = c.kind === "accel";
    const sensK = acc ? "sensitivity_mV_per_g" : "sensitivity_mV_per_A", rngK = acc ? "range_g" : "range_V";
    const iepe = acc ? `<select data-k="iepe_current_A">${CFG.iepe_choices.map((v) => `<option value="${v}" ${Math.abs(v - c.iepe_current_A) < 1e-6 ? "selected" : ""}>${v ? v * 1000 + " mA" : "off"}</option>`).join("")}</select>` : '<span class="muted">off</span>';
    return `<tr data-name="${esc(c.name)}"><td><b>${esc(c.name)}</b></td><td>${esc(c.kind)}</td>
      <td><select data-k="physical">${inputs.map((p) => `<option ${p === c.physical ? "selected" : ""}>${p}</option>`).join("")}</select></td>
      <td><input data-k="${sensK}" type="number" step="any" value="${c[sensK]}"><span class="unit">${acc ? "mV/g" : "mV/A"}</span></td>
      <td><input data-k="${rngK}" type="number" step="any" value="${c[rngK]}"><span class="unit">${acc ? "± g" : "± V"}</span></td>
      <td>${iepe}</td>
      <td><select data-k="coupling">${["AC", "DC"].map((v) => `<option ${v === c.coupling ? "selected" : ""}>${v}</option>`).join("")}</select></td>
      <td><input data-k="location" value="${esc(c.location || "")}"></td>
      <td class="adv">${acc
        ? "<b>Sensitivity: once</b>, to the value on the Kistler calibration certificate. Keep input ai0, range 25 g, IEPE 4 mA, AC."
        : "<b>Sensitivity must equal the clamp switch</b> (100 mV/A now; 10 if the clamp is on its 10 mV/A range). Keep ai2, 5 V, IEPE off, DC."}
        Update location only if you move the sensor.</td></tr>`;
  }).join("");
  $("#cfg-text").textContent = CFG.text;
  $("#cfg-msg").textContent = CFG.dirty ? "session config differs from config.toml (not saved)" : "";
}
async function applyConfig(save) {
  const body = { sample_rate: +$("#cfg-rate").value, save, channels: [] };
  $$("#cfg-table tbody tr").forEach((tr) => {
    const ch = { name: tr.dataset.name };
    $$("[data-k]", tr).forEach((el) => (ch[el.dataset.k] = el.type === "number" || el.dataset.k === "iepe_current_A" ? +el.value : el.value));
    body.channels.push(ch);
  });
  try {
    await api("/api/config", body);
    toast(save ? "Applied and saved to config.toml" : "Applied for this session (not saved)");
    setTimeout(loadConfig, 300);
  } catch (e) { $("#cfg-msg").textContent = e.message; $("#cfg-msg").className = "hint warn-text"; toast(e.message, true); }
}

// ============================================================================ guide
let guideLoaded = false;
async function loadGuide() {
  if (guideLoaded) return;
  try {
    const g = await api("/api/guide");
    $("#guide").innerHTML = window.marked ? marked.parse(g.markdown) : `<pre>${esc(g.markdown)}</pre>`;
    guideLoaded = true;
  } catch (e) { $("#guide").textContent = e.message; }
}

// ============================================================================ wiring
function switchTab(t) {
  STATE.tab = t;
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === t));
  $$(".tab").forEach((m) => m.classList.toggle("hidden", m.id !== "tab-" + t));
  if (t === "review") loadRuns();
  if (t === "config") loadConfig();
  if (t === "guide") loadGuide();
  if (t === "checklist") clRenderTab();
  sendOpt();
}

function init() {
  new ResizeObserver(() => fitLivePlots()).observe($("#dock"));
  $$(".tabs button").forEach((b) => (b.onclick = () => switchTab(b.dataset.tab)));
  const f = store.get("form", {});
  STATE.fault = f.fault || "";
  if (f.freq) $("#freq").value = f.freq;
  if (f.load) $("#load").value = f.load;
  if (f.operator) $("#operator").value = f.operator;
  if (f.duration) $("#duration").value = f.duration;
  if (f.settle) $("#settle").value = f.settle;
  renderFaultChips(); renderDurChips();
  clInit();
  ["#freq", "#load", "#operator", "#tag", "#notes", "#duration", "#settle"].forEach((s) => $(s).addEventListener("input", onFormChange));
  $("#btn-add-fault").onclick = addCustomFault;
  $("#fault-custom").addEventListener("keydown", (e) => { if (e.key === "Enter") addCustomFault(); });
  $("#btn-record").onclick = toggleRecord;
  $("#btn-discard").onclick = discardRecording;
  ["#opt-window", "#opt-fmax", "#opt-avg"].forEach((s) => $(s).addEventListener("change", sendOpt));
  $("#opt-log").onchange = () => buildLivePlots();
  $("#opt-markers").onchange = () => Object.values(STATE.plots).forEach((p) => p.spec.redraw());
  $("#btn-freeze").onclick = () => { STATE.frozen = !STATE.frozen; $("#btn-freeze").textContent = STATE.frozen ? "Resume" : "Freeze display"; sendOpt(); };
  $("#demo-hz").onchange = () => api("/api/demo", { drive_hz: +$("#demo-hz").value });
  $("#demo-load").onchange = () => api("/api/demo", { load: +$("#demo-load").value });
  $("#btn-runs-refresh").onclick = loadRuns;
  $("#btn-stream").onclick = async () => {
    const off = STATE.st && STATE.st.status === "stopped";
    try { await api(off ? "/api/stream/start" : "/api/stream/stop", {}); toast(off ? "Starting stream…" : "Stream stopped, DAQ task closed"); }
    catch (e) { toast(e.message, true); }
  };
  document.addEventListener("click", (e) => { const g = e.target.closest("[data-goto]"); if (g) switchTab(g.dataset.goto); });
  $("#runs-filter").addEventListener("input", renderRuns);
  $("#runs-flag").onchange = renderRuns;
  $("#btn-cfg-apply").onclick = () => applyConfig(false);
  $("#btn-cfg-save").onclick = () => applyConfig(true);
  $("#btn-cfg-reload").onclick = async () => { await api("/api/config/reload", {}); toast("Reloaded config.toml"); setTimeout(loadConfig, 300); };
  $("#btn-reconnect").onclick = () => api("/api/stream/restart", {}).then(() => toast("Reconnecting…"));
  $("#btn-selftest").onclick = () => { toast("Running self-test…"); api("/api/device/selftest", {}).then((r) => toast(r.message), (e) => toast(e.message, true)); };
  $("#btn-takeover").onclick = () => api("/api/device/reserve", { force: true }).then((r) => toast(r.message), (e) => toast(e.message, true));
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea, select")) return;
    if (e.key === "f") $("#btn-freeze").click();
  });
  loadRuns();
  pollState();
  connectWs();
  setInterval(() => { $("#fname-preview").textContent = fileName(); }, 1000);
}
document.addEventListener("DOMContentLoaded", init);  // after checklist.js has loaded
