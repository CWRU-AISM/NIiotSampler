"use strict";
// Recording checklists (see checklist.py). Uses helpers from app.js: $, $$, api, esc, fmt, toast, store, STATE,
// switchTab, toggleRecord, onFormChange, renderFaultChips, DEFAULT_FAULTS, armed, arm, openRun.

const CL = {
  doc: null, session: null, itemId: null, handledFile: null,
  steady: { runStart: null, samples: [] }, autoFired: null, cueStep: -1,
};
const KIND_LABEL = { steady: "steady", sweep_speed: "speed sweep", sweep_load: "load sweep" };
const ST_ICON = { todo: "○", done: "✓", attention: "!", skipped: "–" };

function clItems() { return (CL.doc && CL.doc.items) || []; }
function clItem(id = CL.itemId) { return clItems().find((it) => it.id === id) || null; }
function clSessions() { return [...new Set(clItems().map((it) => it.session))]; }
function hms(s) {
  s = Math.round(s || 0);
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h} h ${String(m).padStart(2, "0")} min` : `${m} min`;
}
function parseList(s, num = true) {
  return String(s).split(",").map((x) => x.trim()).filter(Boolean).map((x) => (num ? Number(x) : x)).filter((x) => !num || isFinite(x));
}

// ---------------------------------------------------------------------------- loading
async function clLoad(name) {
  try {
    CL.doc = await api("/api/checklist" + (name ? `?name=${encodeURIComponent(name)}` : ""));
  } catch { CL.doc = null; }
  if (CL.doc) {
    const sessions = clSessions();
    const saved = store.get("cl.sel", {});
    if (saved.name === CL.doc.name) { CL.session = saved.session; CL.itemId = saved.item; }
    if (!sessions.includes(CL.session)) {
      CL.session = (clItems().find((it) => it.status === "todo") || clItems()[0] || {}).session || null;
      CL.itemId = null;
    }
    if (CL.itemId && !clItem()) CL.itemId = null;
  } else { CL.session = CL.itemId = null; }
  clRenderCard();
  if (STATE.tab === "checklist") clRenderTab();
}
function clSaveSel() { store.set("cl.sel", { name: CL.doc && CL.doc.name, session: CL.session, item: CL.itemId }); }

// ---------------------------------------------------------------------------- selection
function itemTag(it) {
  return `R${it.round}` + (it.repeat > 1 ? `-k${it.repeat}` : "") + (it.kind !== "steady" ? "-" + it.kind.replace("_", "-") : "");
}
function clSelect(id, fill = true) {
  const it = clItem(id);
  CL.itemId = it ? it.id : null;
  if (it) CL.session = it.session;
  CL.steady = { runStart: null, samples: [] };
  clSaveSel();
  if (it && fill) {
    const custom = store.get("customFaults", []);
    if (!DEFAULT_FAULTS.includes(it.fault) && !custom.includes(it.fault)) store.set("customFaults", [...custom, it.fault]);
    STATE.fault = it.fault;
    $("#freq").value = it.speed_hz;
    $("#load").value = `L${it.load}`;
    $("#tag").value = itemTag(it);
    $("#duration").value = it.duration_s;
    renderFaultChips(); onFormChange();
  }
  clRenderCard();
  if (STATE.tab === "checklist") clRenderTab();
  if (it && fill && STATE.tab === "acquire") clCheckLoad();
}
function clNextTodo(fromId, dir = 1) {
  const items = clItems().filter((it) => it.session === CL.session);
  if (!items.length) return null;
  let i = Math.max(-1, items.findIndex((it) => it.id === fromId));
  for (let k = 0; k < items.length; k++) {
    i = (i + dir + items.length) % items.length;
    if (items[i].status === "todo") return items[i].id;
  }
  return null;
}
function clStep(dir) {
  const items = clItems().filter((it) => it.session === CL.session);
  const i = items.findIndex((it) => it.id === CL.itemId);
  const j = Math.min(items.length - 1, Math.max(0, (i < 0 ? 0 : i + dir)));
  if (items[j]) clSelect(items[j].id);
}

// modal ----------------------------------------------------------------------
function showModal(html, bind) {
  $("#modal-box").innerHTML = html;
  $("#modal").classList.remove("hidden");
  if (bind) bind($("#modal-box"));
}
function hideModal() { $("#modal").classList.add("hidden"); $("#modal-box").innerHTML = ""; }

// load-change confirmation -----------------------------------------------------
function loadKey() { return CL.doc ? `${CL.doc.name}|${CL.session}` : ""; }
function confirmedLoad() { const s = store.get("cl.load", {}); return s.key === loadKey() ? s.load : null; }
function setConfirmedLoad(load) { store.set("cl.load", { key: loadKey(), load: String(load) }); }
function neededLoad(it) { return String(it.kind === "sweep_load" ? it.schedule[0].load : it.load); }
function clLoadPending() { const it = clItem(); return !!(it && confirmedLoad() !== neededLoad(it)); }

function clCheckLoad() {
  const it = clItem();
  if (!it || !clLoadPending()) return false;
  const need = neededLoad(it), prev = confirmedLoad();
  beep(660, 250);
  showModal(`<h3>Change the load</h3>
    <div class="huge">${prev !== null ? `<span class="from">L${esc(prev)} →</span> ` : ""}L${esc(need)}</div>
    <p>Next item <b>${esc(it.id)}</b> needs <b>load ${esc(need)}</b>${it.kind === "sweep_load" ? " (the first step of the load sweep)" : ""}.
      Set the load on the rig, let the current settle, then confirm. Recording is blocked until you confirm.</p>
    <div class="acts"><button class="ghost" id="ld-later">Not now</button>
      <button class="big" id="ld-ok">✓ Load is set to ${esc(need)}</button></div>`, (b) => {
    $("#ld-ok", b).onclick = () => { setConfirmedLoad(need); hideModal(); CL.steady = { runStart: null, samples: [] }; clRenderCard(); toast(`Load ${need} confirmed`); };
    $("#ld-later", b).onclick = () => { hideModal(); clRenderCard(); };
  });
  return true;
}

// hooks used by app.js -------------------------------------------------------
function clRecordBlocked() { return clItemRef() && clCheckLoad(); }
function clItemRef() { return CL.doc && CL.itemId ? { checklist: CL.doc.name, id: CL.itemId } : null; }

function sweepOf(rec) {
  try { return rec && rec.attrs && rec.attrs.sweep_schedule ? JSON.parse(rec.attrs.sweep_schedule) : null; } catch { return null; }
}
function clLiveDriveHz() {
  const rec = STATE.st && STATE.st.recording;
  const sched = sweepOf(rec);
  if (sched) {
    const step = rec.attrs.sweep_step_s || 10;
    const k = Math.min(sched.length - 1, Math.floor((rec.elapsed_s || 0) / step));
    return sched[k].speed_hz;
  }
  const it = clItem();
  return it ? it.speed_hz : null;
}

async function clOnLastRun(lr) {
  if (!lr || !lr.report || !lr.checklist_item || lr.file === CL.handledFile) return;
  CL.handledFile = lr.file;
  const it = lr.checklist_item;
  if (it.kind === "sweep_load" && it.schedule) setConfirmedLoad(it.schedule[it.schedule.length - 1].load);
  await clLoad(CL.doc && CL.doc.name);
  if (it.status === "done") {
    const next = $("#cl-autoadv") && $("#cl-autoadv").checked ? clNextTodo(it.id) : null;
    if (next) { clSelect(next); const n = clItem(next); toast(`✓ ${it.id} done. Next: ${KIND_LABEL[n.kind]} · load ${n.load} · ${n.speed_hz} Hz. Set the drive / load, then wait for steady.`); }
    else toast(`✓ ${it.id} done` + (clNextTodo(it.id) ? "" : ". Session complete!"));
  } else {
    toast(`! ${it.id} needs attention: ${(it.summary && it.summary.problems || []).join("; ") || "check the report"}. Re-record, or accept it in the Checklist tab.`, true);
  }
}

async function clOnDeleted(file) {
  if (!CL.doc) return;
  const hit = clItems().find((it) => it.file === file);
  await clLoad(CL.doc.name);
  if (hit) { clSelect(hit.id); toast(`${hit.id} is back on the checklist (to do)`); }
}

// steady detector + auto-record + sweep cue --------------------------------------
function clOnFrame(fr) {
  const box = $("#cl-steady");
  if (!box) return;
  const target = clLiveDriveHz() || +$("#freq").value || null;
  const d = fr.checks && fr.checks.derived;
  const curCh = (STATE.st && STATE.st.channels || []).find((c) => c.kind === "current_clamp");
  const cur = curCh && (fr.channels || []).find((c) => c.name === curCh.name);
  const now = Date.now() / 1000;
  const hz = d && d.current_hz;
  const S = CL.steady;
  const badge = (cls, msg) => `<div class="steady-badge ${cls}">
      <div class="sb-row"><span>MEASURED</span><b>${hz ? hz.toFixed(2) + " Hz" : "–"}</b></div>
      <div class="sb-row"><span>TARGET</span><b>${target ? target + " Hz" : "–"}</b></div>
      <div class="sb-msg">${msg}</div></div>`;
  if (!target || !hz) { S.runStart = null; box.innerHTML = target ? badge("bad", "no current signal: motor off or clamp not connected?") : ""; return; }
  const ok = Math.abs(hz - target) <= Math.max(0.3, 0.02 * target);
  S.samples.push({ t: now, rms: cur ? cur.stats.rms : null });
  S.samples = S.samples.filter((s) => s.t > now - 10);
  if (!ok) S.runStart = null; else if (S.runStart === null) S.runStart = now;
  const r = S.samples.map((s) => s.rms).filter((x) => x != null).sort((a, b) => a - b);
  const rmsStable = r.length > 5 && (r[r.length - 1] - r[0]) / (r[Math.floor(r.length / 2)] || 1) < 0.05;
  const forS = ok ? now - S.runStart : 0;
  const rec = STATE.st && STATE.st.recording;
  const loadPend = clLoadPending();
  if (rec) box.innerHTML = badge(ok ? "ok" : "bad", ok ? "● recording · on target" : "● recording · OFF TARGET: check the drive");
  else if (!ok) box.innerHTML = badge("bad", "NOT at target: set the drive to the target speed");
  else if (loadPend) box.innerHTML = badge("wait", `on speed · confirm the load change (L${esc(neededLoad(clItem()))}) first`);
  else if (!rmsStable) box.innerHTML = badge("wait", `on speed ${forS.toFixed(0)} s · current still changing (load settling)`);
  else box.innerHTML = badge("ok", `STEADY ✓ for ${forS.toFixed(0)} s`);
  if (rec) return;
  // auto-record
  const it = clItem();
  const auto = $("#cl-auto") && $("#cl-auto").checked;
  const need = +($("#cl-auto-s") && $("#cl-auto-s").value) || 30;
  if (auto && it && it.status === "todo" && !loadPend && ok && rmsStable && forS >= need && CL.autoFired !== it.id && STATE.st.status === "running") {
    CL.autoFired = it.id;
    toast(`Auto-record: steady for ${need} s → recording ${it.id}`);
    toggleRecord();
  }
}

let audioCtx;
function beep(freq = 880, ms = 180) {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.frequency.value = freq; g.gain.value = 0.15;
    o.connect(g); g.connect(audioCtx.destination); o.start(); o.stop(audioCtx.currentTime + ms / 1000);
  } catch { /* no audio */ }
}

function clOnState(st) {
  const cue = $("#sweep-cue");
  const rec = st.recording, sched = sweepOf(rec);
  if (!sched) { cue.classList.add("hidden"); CL.cueStep = -1; return; }
  const step = rec.attrs.sweep_step_s || 10, el = rec.elapsed_s || 0;
  const k = Math.min(sched.length - 1, Math.floor(el / step));
  const left = Math.max(0, (k + 1) * step - el);
  const kind = rec.attrs.kind;
  const what = (s) => (kind === "sweep_load" ? `load ${s.load}` : `${s.speed_hz} Hz`);
  if (!rec.settling && k !== CL.cueStep) { if (CL.cueStep >= 0) beep(); CL.cueStep = k; }
  const nxt = sched[k + 1];
  cue.classList.remove("hidden");
  cue.innerHTML = rec.settling
    ? `<div class="big">Hold ${what(sched[0])}</div><div>settling… the schedule starts when recording starts</div>`
    : `<div class="small muted">sweep step ${k + 1} / ${sched.length}</div><div class="big">${what(sched[k])}</div>
       <div>${nxt ? `next: <b>${what(nxt)}</b> in <b>${left.toFixed(0)} s</b> (beep)` : `last step · ends in ${left.toFixed(0)} s`}</div>`;
}

// ---------------------------------------------------------------------------- Acquire card
function clRenderCard() {
  const body = $("#cl-body");
  if (!body) return;
  if (!CL.doc) {
    body.innerHTML = `<div class="muted small">No checklist yet. <a data-goto="checklist">Create or import one →</a></div>`;
    return;
  }
  const sessions = clSessions(), items = clItems().filter((it) => it.session === CL.session);
  const done = items.filter((it) => it.status === "done" || it.status === "skipped").length;
  const att = items.filter((it) => it.status === "attention").length;
  const todoS = items.filter((it) => it.status === "todo").reduce((a, it) => a + it.duration_s + CL.doc.changeover_s, 0);
  const it = clItem();
  let cur = `<div class="muted small">No item selected. <a id="cl-pick-next">Select the next item to do</a></div>`;
  if (it) {
    const sweep = it.kind !== "steady";
    cur = `<div class="cl-item ${it.status}">
      <div class="row between"><b>${esc(it.id)}</b><span class="stpill ${it.status}">${ST_ICON[it.status]} ${it.status}</span></div>
      <div>${KIND_LABEL[it.kind]} · <b>load ${esc(it.load)}</b> · <b>${it.speed_hz} Hz</b> · ${it.duration_s} s</div>
      ${sweep ? `<div class="small">${it.kind === "sweep_speed"
        ? `speed steps every ${it.step_s} s: ${it.schedule.map((s) => s.speed_hz).join(" → ")} Hz`
        : `load steps every ${it.step_s} s: ${it.schedule.map((s) => s.load).join(" → ")} at ${it.speed_hz} Hz`}<br>
        <span class="muted">Set the first step, wait for steady, press Record, then change at each beep.</span></div>` : ""}
      ${it.file ? `<div class="small">file <a data-open="${esc(it.file)}">${esc(it.file.split("/").pop())}</a>${it.summary ? ` · ✓${it.summary.pass} !${it.summary.warn} ✗${it.summary.fail}` : ""}</div>` : ""}
      ${it.summary && it.summary.problems && it.summary.problems.length ? `<div class="small warn-text">${esc(it.summary.problems.join("; "))}</div>` : ""}
    </div>
    <div class="row"><button class="ghost small" id="cl-prev">◀ prev</button><button class="ghost small" id="cl-next">next ▶</button>
      <button class="ghost small" id="cl-next-todo">next to do</button><button class="ghost small" id="cl-skip">${it.status === "skipped" ? "unskip" : "skip"}</button>
      <button class="ghost small" id="cl-detach" title="Record without linking to the checklist">detach</button></div>
    ${clLoadPending() ? `<div class="loadwarn">⚠ load change to L${esc(neededLoad(it))} not confirmed · <a id="cl-load-confirm">confirm</a></div>` : ""}
    <div id="cl-steady"></div>
    <div class="row small"><a id="cl-link-here">link an already saved file to this item…</a></div>`;
  }
  body.innerHTML = `
    <div class="row"><select id="cl-session-sel">${sessions.map((s) => {
      const its = clItems().filter((x) => x.session === s), d = its.filter((x) => x.status === "done" || x.status === "skipped").length;
      return `<option value="${esc(s)}" ${s === CL.session ? "selected" : ""}>${esc(s)} (${d}/${its.length})</option>`; }).join("")}</select></div>
    <div class="progress slim"><div class="bar ok" style="width:${items.length ? (100 * done / items.length).toFixed(1) : 0}%"></div>
      <span class="ptext">${done}/${items.length} done${att ? ` · <span class="warn-text">${att} need attention</span>` : ""} · ~${hms(todoS)} left</span></div>
    ${cur}
    <div class="row small"><label><input type="checkbox" id="cl-autoadv" ${store.get("cl.autoadv", true) ? "checked" : ""}> go to next item after a good run</label></div>
    <div class="row small"><label><input type="checkbox" id="cl-auto" ${store.get("cl.auto", false) ? "checked" : ""}> auto-record when steady for</label>
      <input id="cl-auto-s" type="number" class="tiny" min="5" value="${store.get("cl.autoS", 30)}"> s</div>`;
  $("#cl-session-sel").onchange = (e) => { CL.session = e.target.value; CL.itemId = null; clSaveSel(); clSelect(clNextTodo(null)); };
  const pick = $("#cl-pick-next"); if (pick) pick.onclick = () => clSelect(clNextTodo(null));
  if (it) {
    $("#cl-prev").onclick = () => clStep(-1);
    $("#cl-next").onclick = () => clStep(1);
    $("#cl-next-todo").onclick = () => { const n = clNextTodo(it.id); n ? clSelect(n) : toast("Nothing left to do in this session"); };
    $("#cl-skip").onclick = () => clSetStatus(it.id, it.status === "skipped" ? "todo" : "skipped");
    const lc = $("#cl-load-confirm"); if (lc) lc.onclick = () => clCheckLoad();
    $("#cl-link-here").onclick = () => clLinkPicker(it.id);
    $("#cl-detach").onclick = () => { CL.itemId = null; clSaveSel(); clRenderCard(); toast("Detached: the next recording is not linked to the checklist"); };
  }
  $("#cl-autoadv").onchange = (e) => store.set("cl.autoadv", e.target.checked);
  $("#cl-auto").onchange = (e) => { store.set("cl.auto", e.target.checked); CL.autoFired = null; };
  $("#cl-auto-s").onchange = (e) => store.set("cl.autoS", +e.target.value);
}

async function clSetStatus(id, status) {
  try {
    await api("/api/checklist/item", { name: CL.doc.name, id, status });
    await clLoad(CL.doc.name);
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------------------- Checklist tab
const GEN_DEFAULTS = {
  name: "im-fault-study", faults: "health, unbalanced, faulted rotor, bowed rotor, broken rotor, phaseloss",
  loads: "0, 1, 2, 3, 4, 5", speeds: "24, 28, 32, 35, 38, 41, 44, 46, 48, 50",
  rounds: 3, repeats: 1, duration: 60, order: "serpentine", changeover: 45,
  sw_speed: true, sw_speed_step: 10, sw_speed_loads: "0, 1, 2, 3, 4, 5",
  sw_load: true, sw_load_step: 20, sw_load_speeds: "24, 38, 50",
};
function genVals() {
  const g = {};
  $$("#cl-gen [data-g]").forEach((el) => (g[el.dataset.g] = el.type === "checkbox" ? el.checked : el.value));
  return g;
}
function genPlan(g) {
  return {
    name: g.name, faults: parseList(g.faults, false), loads: parseList(g.loads), speeds_hz: parseList(g.speeds),
    rounds: +g.rounds || 1, repeats: +g.repeats || 1, duration_s: +g.duration || 60, order: g.order, changeover_s: +g.changeover || 45,
    sweeps: {
      speed: { enabled: !!g.sw_speed, step_s: +g.sw_speed_step || 10, loads: parseList(g.sw_speed_loads) },
      load: { enabled: !!g.sw_load, step_s: +g.sw_load_step || 20, speeds_hz: parseList(g.sw_load_speeds) },
    },
  };
}
function genPreview() {
  const g = genVals(); store.set("cl.gen.v2", g);
  const p = genPlan(g), F = p.faults.length, R = p.rounds, co = p.changeover_s;
  const steady = p.loads.length * p.speeds_hz.length * p.repeats;
  const ss = p.sweeps.speed.enabled ? (p.sweeps.speed.loads.length || p.loads.length) : 0;
  const ls = p.sweeps.load.enabled ? (p.sweeps.load.speeds_hz.length || p.speeds_hz.length) : 0;
  const tSess = steady * (p.duration_s + co) + ss * (p.speeds_hz.length * p.sweeps.speed.step_s + co) + ls * (p.loads.length * p.sweeps.load.step_s + co);
  const perSess = steady + ss + ls;
  $("#cl-gen-preview").innerHTML = `<b>${perSess}</b> recordings per session (${steady} steady + ${ss} speed sweeps + ${ls} load sweeps) ·
    <b>${F * R}</b> sessions (${F} faults × ${R} rounds) · <b>${perSess * F * R}</b> recordings in total<br>
    ≈ <b>${hms(tSess)}</b> per session (recording + ${co} s changeover each) · ≈ <b>${hms(tSess * F)}</b> per round · ≈ <b>${hms(tSess * F * R)}</b> in total<br>
    speed sweep ${p.speeds_hz.length} steps × ${p.sweeps.speed.step_s} s = ${p.speeds_hz.length * p.sweeps.speed.step_s} s · load sweep ${p.loads.length} steps × ${p.sweeps.load.step_s} s = ${p.loads.length * p.sweeps.load.step_s} s`;
}

async function clRenderTab() {
  let lst = { list: [], active: null };
  try { lst = await api("/api/checklists"); } catch { /* offline */ }
  $("#cl-list").innerHTML = lst.list.length
    ? lst.list.map((c) => `<div class="cl-li ${CL.doc && c.name === CL.doc.name ? "sel" : ""}" data-name="${esc(c.name)}">
        <b>${esc(c.name)}</b> <span class="muted small">${c.done}/${c.items} · ${esc((c.updated || "").slice(0, 16).replace("T", " "))}</span></div>`).join("")
    : '<div class="muted small">none yet: create one below or import a JSON file</div>';
  $$("#cl-list .cl-li").forEach((el) => (el.onclick = async () => { await api("/api/checklist/active", { name: el.dataset.name }); store.set("cl.sel", {}); await clLoad(el.dataset.name); }));
  const doc = CL.doc;
  $("#cl-export").disabled = $("#cl-delete").disabled = !doc;
  if (!doc) { $("#cl-title").textContent = "No checklist"; $("#cl-table tbody").innerHTML = ""; $("#cl-progress").textContent = ""; return; }
  $("#cl-title").textContent = doc.name;
  const all = doc.items, n = (s) => all.filter((it) => it.status === s).length;
  $("#cl-progress").innerHTML = `✓ ${n("done")} · ! ${n("attention")} · – ${n("skipped")} · ○ ${n("todo")} of ${all.length} · ~${hms(doc.estimate_todo_s)} left`;
  const ss = $("#cl-session");
  const sessions = clSessions();
  ss.innerHTML = sessions.map((s) => {
    const its = all.filter((x) => x.session === s), d = its.filter((x) => x.status === "done" || x.status === "skipped").length;
    return `<option value="${esc(s)}" ${s === CL.session ? "selected" : ""}>${esc(s)} · ${d}/${its.length}</option>`; }).join("");
  const mv = $("#cl-move-to");
  mv.innerHTML = sessions.filter((s) => s !== CL.session).map((s) => `<option>${esc(s)}</option>`).join("");
  const sf = $("#cl-status-filter").value;
  const rows = all.filter((it) => it.session === CL.session && (!sf || it.status === sf));
  $("#cl-table tbody").innerHTML = rows.map((it, i) => `<tr class="${it.id === CL.itemId ? "sel" : ""} st-${it.status}" data-id="${esc(it.id)}">
    <td>${i + 1}</td><td><span class="stpill ${it.status}">${ST_ICON[it.status]} ${it.status}</span></td>
    <td>${esc(KIND_LABEL[it.kind])}</td><td>${esc(it.load)}</td><td>${it.speed_hz}</td><td>${it.duration_s}</td>
    <td>${it.file ? `<a data-open="${esc(it.file)}">${esc(it.file.split("/").pop())}</a>` : ""}</td>
    <td>${it.summary ? `✓${it.summary.pass} !${it.summary.warn} ✗${it.summary.fail}` : ""}${it.summary && it.summary.problems && it.summary.problems.length ? `<div class="small warn-text">${esc(it.summary.problems.join("; "))}</div>` : ""}</td>
    <td class="acts"><button class="ghost small" data-act="use">use</button>
      <button class="ghost small" data-act="link">link…</button>${it.file ? '<button class="ghost small" data-act="unlink">unlink</button>' : ""}
      ${it.status === "attention" ? '<button class="ghost small" data-act="done">accept</button>' : ""}
      ${it.status === "todo" ? '<button class="ghost small" data-act="skipped">skip</button>' : '<button class="ghost small" data-act="todo">reset</button>'}</td></tr>`).join("");
  $$("#cl-table tbody tr").forEach((tr) => $$("[data-act]", tr).forEach((b) => (b.onclick = () => {
    const id = tr.dataset.id, act = b.dataset.act;
    if (act === "use") { clSelect(id, true); switchTab("acquire"); clCheckLoad(); }
    else if (act === "link") clLinkPicker(id);
    else if (act === "unlink") clUnlink(id);
    else clSetStatus(id, act).then(() => clRenderTab());
  })));
}

// link / unlink / move ---------------------------------------------------------
async function clLinkPicker(itemId) {
  const it = clItem(itemId);
  if (!it) return;
  let runs = [];
  try { runs = await api("/api/runs"); } catch (e) { return toast(e.message, true); }
  const linked = {};
  clItems().forEach((x) => { if (x.file) linked[x.file] = x.id; });
  const sameHz = (r) => Number(r.drive_freq_hz) === Number(it.speed_hz);
  const score = (r) => (sameHz(r) ? 0 : 2) + (linked[r.file] ? 1 : 0);
  runs.sort((a, b) => score(a) - score(b) || String(b.start_time).localeCompare(String(a.start_time)));
  const rowsHtml = (q) => runs.filter((r) => !q || [r.name, r.fault, r.load, r.notes].join(" ").toLowerCase().includes(q))
    .slice(0, 300).map((r) => `<tr class="${sameHz(r) ? "cand" : ""}">
      <td>${esc((r.start_time || "").slice(5, 16).replace("T", " "))}</td><td>${esc(r.fault)}</td><td>${fmt(r.drive_freq_hz, 4)}</td>
      <td>${esc(r.load)}</td><td>${fmt(r.duration_s, 3)}</td><td class="small">${linked[r.file] ? esc(linked[r.file]) : ""}</td>
      <td><button class="small" data-file="${esc(r.file)}">link</button></td></tr>`).join("");
  showModal(`<h3>Link a saved file to ${esc(it.id)}</h3>
    <p class="small">${KIND_LABEL[it.kind]} · fault <b>${esc(it.fault)}</b> · load <b>${esc(it.load)}</b> · <b>${it.speed_hz} Hz</b>.
      Files at ${it.speed_hz} Hz are listed first (bold). A file linked to another item is moved here.</p>
    <label class="inline"><input type="checkbox" id="lk-relabel" checked> relabel the file to match this item
      (fault → ${esc(it.fault)}, load → L${esc(it.load)}, tag → ${esc(itemTag(it))}; renames the file; old labels are kept inside it)</label>
    <div class="row"><input id="lk-q" placeholder="filter…"></div>
    <div class="tablewrap"><table><thead><tr><th>start</th><th>fault</th><th>Hz</th><th>load</th><th>s</th><th>linked to</th><th></th></tr></thead>
      <tbody id="lk-rows">${rowsHtml("")}</tbody></table></div>
    <div class="acts"><button class="ghost" id="lk-cancel">Cancel</button></div>`, (b) => {
    const bindRows = () => $$("[data-file]", b).forEach((btn) => (btn.onclick = async () => {
      btn.disabled = true;
      try {
        const r = await api("/api/checklist/link", { name: CL.doc.name, id: it.id, file: btn.dataset.file, relabel: $("#lk-relabel", b).checked });
        hideModal();
        toast(`Linked → ${r.id}: ${r.status}${r.file !== btn.dataset.file ? " (file renamed to " + r.file.split("/").pop() + ")" : ""}`);
        await clLoad(CL.doc.name); loadRuns();
      } catch (e) { btn.disabled = false; toast(e.message, true); }
    }));
    bindRows();
    $("#lk-q", b).oninput = (e) => { $("#lk-rows", b).innerHTML = rowsHtml(e.target.value.toLowerCase()); bindRows(); };
    $("#lk-cancel", b).onclick = hideModal;
  });
}

async function clUnlink(id) {
  try {
    await api("/api/checklist/unlink", { name: CL.doc.name, id });
    toast(`${id}: unlinked (back to "to do"; the file is kept)`);
    await clLoad(CL.doc.name);
  } catch (e) { toast(e.message, true); }
}

function clMoveDialog() {
  const to = $("#cl-move-to").value, from = CL.session;
  if (!to) return;
  const recs = clItems().filter((it) => it.session === from && it.file);
  if (!recs.length) return toast(`Nothing recorded in ${from}`, true);
  const toFault = (clItems().find((it) => it.session === to) || {}).fault;
  showModal(`<h3>Move recordings ${esc(from)} → ${esc(to)}</h3>
    <p><b>${recs.length}</b> recorded items of <b>${esc(from)}</b> will be linked to the matching items (same kind, load, speed) of <b>${esc(to)}</b>.
      The ${esc(from)} items go back to "to do".</p>
    <label class="inline"><input type="checkbox" id="mv-relabel" checked> relabel the files: fault → <b>${esc(toFault)}</b>, tag/load to match, files renamed
      (old labels stay inside each file as <code>relabelled_from</code>)</label>
    <div id="mv-result" class="small"></div>
    <div class="acts"><button class="ghost" id="mv-cancel">Cancel</button><button class="big" id="mv-go">Move ${recs.length} recordings</button></div>`, (b) => {
    $("#mv-cancel", b).onclick = hideModal;
    $("#mv-go", b).onclick = async () => {
      $("#mv-go", b).disabled = true; $("#mv-go", b).textContent = "moving…";
      try {
        const r = await api("/api/checklist/move", { name: CL.doc.name, from_session: from, to_session: to, relabel: $("#mv-relabel", b).checked });
        $("#mv-result", b).innerHTML = `<p><b>${r.moved.length} moved</b>${r.skipped.length ? `, <span class="warn-text">${r.skipped.length} skipped</span>` : ""}.</p>` +
          (r.skipped.length ? `<ul>${r.skipped.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : "") +
          (r.moved.some((m) => m.status !== "done") ? `<p class="warn-text">Some moved items need attention (see the table).</p>` : "");
        $("#mv-go", b).classList.add("hidden"); $("#mv-cancel", b).textContent = "Close";
        CL.session = to; clSaveSel();
        await clLoad(CL.doc.name); loadRuns();
      } catch (e) { toast(e.message, true); $("#mv-go", b).disabled = false; }
    };
  });
}

async function clImport(file) {
  try {
    const doc = JSON.parse(await file.text());
    const r = await api("/api/checklist/import", doc);
    store.set("cl.sel", {});
    await clLoad(r.name);
    toast(`Imported checklist "${r.name}" (${r.items.length} items)`);
  } catch (e) { toast("Import failed: " + e.message, true); }
}

function clInit() {
  const g = Object.assign({}, GEN_DEFAULTS, store.get("cl.gen.v2", {}));
  $$("#cl-gen [data-g]").forEach((el) => {
    const v = g[el.dataset.g];
    if (el.type === "checkbox") el.checked = !!v; else el.value = v;
    el.addEventListener("input", genPreview); el.addEventListener("change", genPreview);
  });
  genPreview();
  $("#cl-new").onclick = () => { $("#cl-gen").classList.toggle("hidden"); genPreview(); };
  $("#cl-gen-reset").onclick = () => { store.set("cl.gen.v2", {}); $$("#cl-gen [data-g]").forEach((el) => { const v = GEN_DEFAULTS[el.dataset.g]; if (el.type === "checkbox") el.checked = !!v; else el.value = v; }); genPreview(); };
  $("#cl-gen-create").onclick = async () => {
    const g = genVals();
    try {
      const r = await api("/api/checklist/generate", { name: g.name, plan: genPlan(g) });
      store.set("cl.sel", {});
      await clLoad(r.name);
      $("#cl-gen").classList.add("hidden");
      toast(`Created "${r.name}": ${r.items.length} items`);
    } catch (e) { toast(e.message, true); }
  };
  $("#cl-import").onchange = (e) => { if (e.target.files[0]) clImport(e.target.files[0]); e.target.value = ""; };
  $("#cl-export").onclick = () => { if (CL.doc) window.location.href = `/api/checklist/export?name=${encodeURIComponent(CL.doc.name)}`; };
  $("#cl-delete").onclick = async () => {
    if (!CL.doc) return;
    const key = "cldel:" + CL.doc.name, b = $("#cl-delete");
    if (!armed(key)) {
      arm(key, () => { const a = armed(key); b.classList.toggle("armed", a); b.textContent = a ? "click again to delete" : "Delete"; });
      return;
    }
    STATE.confirm = null;
    await api("/api/checklist/delete", { name: CL.doc.name });
    b.classList.remove("armed"); b.textContent = "Delete";
    toast(`Checklist "${CL.doc.name}" deleted (file renamed to .json.deleted; recordings untouched)`);
    store.set("cl.sel", {});
    await clLoad();
  };
  $("#cl-session").onchange = (e) => { CL.session = e.target.value; clSaveSel(); clRenderTab(); clRenderCard(); };
  $("#cl-status-filter").onchange = () => clRenderTab();
  $("#cl-move").onclick = clMoveDialog;
  document.addEventListener("click", (e) => {
    const o = e.target.closest("[data-open]");
    if (o) { switchTab("review"); openRun(o.dataset.open); }
  });
  clLoad();
}
