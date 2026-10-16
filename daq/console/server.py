"""Sampling console: local web page for live view, recording and review of the IM fault rig.

  python daq/console/server.py            # real hardware (cDAQ1)
  python daq/console/server.py --demo     # synthetic motor signals, no hardware needed
  python daq/console/server.py --sim      # NI-DAQmx simulated devices
  then open http://127.0.0.1:8050

See daq/console/DESIGN.md.
"""

import argparse
import asyncio
import json
import sys
import threading
import time
import tomllib
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # daq/: common, checks, recorder

import numpy as np  # noqa: E402
import tomlkit  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import checklist  # noqa: E402
import review  # noqa: E402
from checks import DEFAULT_MOTOR, amp_spectrum, peak_in, run_checks  # noqa: E402
from common import DEFAULT_CONFIG, PROJECT_DIR, driver_version, load_config  # noqa: E402
from engine import Engine  # noqa: E402

# Module limits for the config editor: sample rate = base / n, IEPE choices [A], max input [V]
MODULES = {
    "NI 9230": {"base": 12800.0, "nmax": 13, "iepe": [0.0, 0.004], "vmax": 30.0},
    "NI 9234": {"base": 51200.0, "nmax": 31, "iepe": [0.0, 0.002], "vmax": 5.0},
    "NI 9232": {"base": 102400.0, "nmax": 31, "iepe": [0.0, 0.004], "vmax": 30.0},
}
EDITABLE = ["physical", "sensitivity_mV_per_g", "sensitivity_mV_per_A", "range_g", "range_V",
            "iepe_current_A", "coupling", "location"]

app = FastAPI(title="IM rig sampling console")
S = {}  # engine, cfg_path, data_dir, mode


@app.middleware("http")
async def no_cache(request, call_next):
    """Always revalidate the page / JS, so an updated console is picked up on reload."""
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def module_limits():
    exp = S["engine"].cfg["vib"].get("expected_module", "")
    actual = S["engine"].info.get("product_type", "")
    for key, lim in MODULES.items():
        if actual.startswith(key) or (not actual.startswith("NI ") and exp.startswith(key)):
            return key, lim
    return None, None


def valid_rates():
    _, lim = module_limits()
    if not lim:
        return []
    return [round(lim["base"] / n, 3) for n in range(1, lim["nmax"] + 1)]


def clean(o):
    """JSON-safe: NaN/Inf -> None, numpy scalars -> Python."""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, float) and not np.isfinite(o):
        return None
    return o


def motor_cfg():
    return DEFAULT_MOTOR | S["engine"].cfg.get("motor", {})


# ----------------------------------------------------------------------------- state / device
@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/guide")
def guide():
    return {"markdown": (HERE / "USER_GUIDE.md").read_text(encoding="utf-8")}


@app.get("/api/state")
def state():
    e = S["engine"]
    hint, action = e.error_hint()
    try:
        dmx = driver_version()
    except Exception:
        dmx = "n/a"
    return clean({
        "mode": e.mode, "status": e.status, "error": e.error, "error_code": e.error_code,
        "error_hint": hint, "error_action": action, "device": e.info, "module_warning": e.module_warning,
        "rate": e.rate, "channels": e.channel_list(), "motor": motor_cfg(), "recording": e.recording_state(),
        "last_run": e.last_run, "uptime_s": time.time() - e.stream_t0 if e.stream_t0 else 0,
        "buffered_s": min(e.total, e.ring.shape[1] if e.ring is not None else 0) / e.rate if e.rate else 0,
        "data_dir": str(S["data_dir"]), "config_path": str(S["cfg_path"]), "daqmx": dmx,
        "demo_drive_hz": e.demo_drive_hz if e.mode == "demo" else None,
    })


def _chassis_name():
    e = S["engine"]
    name = e.info.get("chassis")
    if name:
        return name
    from nidaqmx.system import Device
    return Device(e.cfg["vib"]["device"]).compact_daq_chassis_device.name


@app.post("/api/device/reserve")
def reserve(force: bool = Body(True, embed=True)):
    e = S["engine"]
    if e.mode == "demo":
        return {"ok": True, "message": "demo mode"}
    from nidaqmx.system import Device
    e.pause()
    try:
        name = _chassis_name()
        Device(name).reserve_network_device(override_reservation=force)
        return {"ok": True, "message": f"reserved {name}"}
    except Exception as ex:
        raise HTTPException(400, f"reserve failed: {ex}")
    finally:
        e.resume()


@app.post("/api/device/selftest")
def selftest():
    e = S["engine"]
    if e.mode == "demo":
        return {"ok": True, "message": "demo mode: nothing to test"}
    from nidaqmx.system import Device
    e.pause()
    try:
        name = e.cfg["vib"]["sim_device" if e.mode == "sim" else "device"]
        Device(name).self_test_device()
        return {"ok": True, "message": f"{name}: self-test passed"}
    except Exception as ex:
        raise HTTPException(400, f"self-test failed: {ex}")
    finally:
        e.resume()


@app.post("/api/stream/restart")
def stream_restart():
    S["engine"].start_stream()
    S["engine"].restart()
    return {"ok": True}


@app.post("/api/stream/stop")
def stream_stop():
    try:
        S["engine"].stop_stream()
    except RuntimeError as ex:
        raise HTTPException(409, str(ex))
    return {"ok": True}


@app.post("/api/stream/start")
def stream_start():
    S["engine"].start_stream()
    return {"ok": True}


@app.post("/api/demo")
def demo(body: dict = Body(...)):
    if "drive_hz" in body:
        S["engine"].demo_drive_hz = float(body["drive_hz"])
    if "load" in body:
        S["engine"].demo_load = float(body["load"])
    return {"ok": True}


# ----------------------------------------------------------------------------- config
@app.get("/api/config")
def get_config():
    e = S["engine"]
    key, lim = module_limits()
    return {"sample_rate": e.cfg["vib"]["sample_rate"], "valid_rates": valid_rates(), "module": key,
            "iepe_choices": lim["iepe"] if lim else [0.0, 0.002, 0.004], "vmax": lim["vmax"] if lim else None,
            "channels": [{k: c[k] for k in ["name", "kind"] + EDITABLE if k in c} for c in e.cfg["vib"]["channels"]],
            "dirty": e.cfg["_text"] != Path(S["cfg_path"]).read_text(encoding="utf-8"),
            "text": e.cfg["_text"]}


@app.post("/api/config")
def post_config(body: dict = Body(...)):
    e = S["engine"]
    if e.recording_state():
        raise HTTPException(409, "stop the recording before changing the configuration")
    doc = tomlkit.parse(e.cfg["_text"])
    errs = []
    _, lim = module_limits()
    if "sample_rate" in body:
        rate = float(body["sample_rate"])
        vr = valid_rates()
        if vr and not any(abs(rate - r) < 1e-3 for r in vr):
            errs.append(f"sample rate {rate:g} is not valid for this module")
        doc["vib"]["sample_rate"] = rate
    by_name = {c["name"]: c for c in doc["vib"]["channels"]}
    for upd in body.get("channels", []):
        c = by_name.get(upd.get("name"))
        if c is None:
            errs.append(f"unknown channel {upd.get('name')}")
            continue
        for k in EDITABLE:
            if k not in upd or k not in c:
                continue
            v = upd[k]
            if k in ("physical", "coupling", "location"):
                v = str(v).strip()
            else:
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    errs.append(f"{c['name']}.{k}: not a number")
                    continue
                if k != "iepe_current_A" and v <= 0:
                    errs.append(f"{c['name']}.{k} must be > 0")
            c[k] = v
        if c.get("coupling") not in ("AC", "DC"):
            errs.append(f"{c['name']}: coupling must be AC or DC")
        if lim and c["kind"] == "accel":
            if not any(abs(float(c["iepe_current_A"]) - i) < 1e-6 for i in lim["iepe"]):
                errs.append(f"{c['name']}: IEPE must be one of {[f'{i * 1000:g} mA' for i in lim['iepe']]}")
            v_fs = float(c["sensitivity_mV_per_g"]) * float(c["range_g"]) / 1000
            if v_fs > lim["vmax"]:
                errs.append(f"{c['name']}: {c['range_g']} g x {c['sensitivity_mV_per_g']} mV/g = {v_fs:.1f} V "
                            f"exceeds the ±{lim['vmax']:g} V input")
        if lim and c["kind"] == "current_clamp" and float(c["range_V"]) > lim["vmax"]:
            errs.append(f"{c['name']}: range_V exceeds ±{lim['vmax']:g} V")
    allp = [str(c["physical"]) for c in doc["vib"]["channels"]]
    if len(set(allp)) != len(allp):
        errs.append("two channels use the same physical input")
    if errs:
        raise HTTPException(400, "; ".join(errs))
    text = tomlkit.dumps(doc)
    cfg = tomllib.loads(text)
    cfg["_path"], cfg["_text"] = str(S["cfg_path"]), text
    if body.get("save"):
        Path(S["cfg_path"]).write_text(text, encoding="utf-8", newline="\n")
    try:
        e.apply_config(cfg)
    except RuntimeError as ex:
        raise HTTPException(409, str(ex))
    return {"ok": True, "saved": bool(body.get("save"))}


@app.post("/api/config/reload")
def reload_config():
    S["engine"].apply_config(load_config(S["cfg_path"]))
    return {"ok": True}


# ----------------------------------------------------------------------------- recording
@app.post("/api/record/start")
def record_start(p: dict = Body(...)):
    try:
        p["freq"] = float(p.get("freq") or 0)
        p["duration"] = float(p.get("duration") or 0)
        p["settle"] = float(p.get("settle", 2.0))
    except (TypeError, ValueError):
        raise HTTPException(400, "drive Hz, duration and settle must be numbers")
    if not str(p.get("fault", "")).strip():
        raise HTTPException(400, "choose a fault label")
    if p["duration"] <= 0:
        raise HTTPException(400, "duration must be > 0")
    e = S["engine"]
    item_ref = p.pop("item", None)
    item = None
    if item_ref:
        doc = S["checklists"].load(item_ref.get("checklist"))
        item = next((it for it in (doc or {}).get("items", []) if it["id"] == item_ref.get("id")), None)
        if item is None:
            raise HTTPException(404, f"checklist item {item_ref.get('id')} not found")
        p["attrs"] = {"checklist": doc["name"], "checklist_item": item["id"], "round": item.get("round"),
                      "session": item.get("session"), "kind": item["kind"], "load_level": str(item["load"])}
        if item.get("schedule"):
            p["duration"] = float(item["duration_s"])  # a sweep's length is fixed by its schedule
            p["attrs"]["sweep_schedule"] = json.dumps(item["schedule"])
            p["attrs"]["sweep_step_s"] = float(item.get("step_s") or 10.0)
    if e.status in ("stopped", "paused"):
        # Record pressed while the live stream is off: start it; give IEPE / AC coupling >= 5 s to settle
        e.start_stream()
        if not e.wait_running(20):
            raise HTTPException(409, f"could not start the stream ({e.status}): {e.error or ''}")
        p["settle"] = max(p["settle"], 5.0)
    try:
        f = e.start_recording(p)
    except (RuntimeError, OSError) as ex:
        raise HTTPException(409, str(ex))
    if item:
        S["pending"][f] = (p["attrs"]["checklist"], item["id"])
    return {"ok": True, "file": f}


@app.post("/api/record/stop")
def record_stop(discard: bool = Body(False, embed=True)):
    S["engine"].stop_recording(discard=discard)
    return {"ok": True}


def delete_run(rel):
    e = S["engine"]
    rec = e.recording_state()
    if rec and rec["file"] == rel:
        raise RuntimeError("this run is still being recorded: use Discard")
    trash = review.delete_run(S["data_dir"], rel)
    S["pending"].pop(rel, None)
    S["checklists"].forget_file(rel)
    if e.last_run and e.last_run.get("file") == rel:
        e.last_run = {"file": rel, "deleted": True, "trash": trash}
    return trash


@app.post("/api/run/delete")
def run_delete(file: str = Body(..., embed=True)):
    _run_path(file)
    try:
        trash = delete_run(file)
    except FileNotFoundError:
        raise HTTPException(404, "file not found")
    except RuntimeError as ex:
        raise HTTPException(409, str(ex))
    return {"ok": True, "trash": trash}


def _post_check(summary):
    report = review.check_file(summary["path"], motor_cfg())
    ref = S["pending"].pop(summary["file"], None)
    if ref:  # checklist recording: mark the item done / attention
        try:
            summary["checklist_item"] = S["checklists"].record_result(ref[0], ref[1], summary["file"], report,
                                                                      summary.get("completed", False))
        except (KeyError, OSError, ValueError) as ex:
            report.setdefault("checks", []).append({"id": "checklist", "channel": "file", "status": "warn",
                                                    "title": f"checklist not updated: {ex}", "detail": ""})
    return report


# ----------------------------------------------------------------------------- checklists
def _cl_doc(doc):
    if not doc:
        return None
    co = float((doc.get("plan") or {}).get("changeover_s", 45))
    todo = [it for it in doc["items"] if it["status"] == "todo"]
    return doc | {"estimate_total_s": checklist.estimate_s(doc["items"], co),
                  "estimate_todo_s": checklist.estimate_s(todo, co), "changeover_s": co}


@app.get("/api/checklists")
def checklists():
    st = S["checklists"]
    return {"active": st.active, "list": st.names()}


@app.get("/api/checklist")
def get_checklist(name: str = ""):
    try:
        return _cl_doc(S["checklists"].load(name or None))
    except OSError:
        raise HTTPException(404, "checklist not found")


@app.post("/api/checklist/generate")
def checklist_generate(body: dict = Body(...)):
    try:
        plan = body["plan"]
        return _cl_doc(S["checklists"].create(body.get("name") or "checklist", plan=plan))
    except (KeyError, ValueError, TypeError) as ex:
        raise HTTPException(400, f"cannot build the checklist: {ex}")


@app.post("/api/checklist/import")
def checklist_import(doc: dict = Body(...)):
    try:
        return _cl_doc(S["checklists"].import_doc(doc))
    except (KeyError, ValueError, TypeError) as ex:
        raise HTTPException(400, f"cannot import: {ex}")


@app.get("/api/checklist/export")
def checklist_export(name: str):
    from fastapi.responses import Response
    try:
        doc = S["checklists"].load(name)
    except OSError:
        raise HTTPException(404, "checklist not found")
    return Response(json.dumps(doc, indent=1, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{checklist.slug(name)}.json"'})


@app.post("/api/checklist/active")
def checklist_active(name: str = Body("", embed=True)):
    S["checklists"].active = name
    return {"ok": True}


@app.post("/api/checklist/item")
def checklist_item(body: dict = Body(...)):
    changes = {k: body[k] for k in ("status", "note") if k in body}
    if changes.get("status") not in (None, *checklist.STATUSES):
        raise HTTPException(400, "bad status")
    if changes.get("status") == "todo":
        changes.update(file=None, summary=None, recorded_at=None)
    try:
        return S["checklists"].update_item(body["name"], body["id"], **changes)
    except (KeyError, OSError):
        raise HTTPException(404, "item not found")


def item_tag(it):
    tag = f"R{it.get('round', 1)}"
    if int(it.get("repeat", 1)) > 1:
        tag += f"-k{it['repeat']}"
    if it["kind"] != "steady":
        tag += "-" + it["kind"].replace("_", "-")
    return tag


def link_file(name, item, file, relabel=True):
    """Link a saved run to a checklist item; optionally relabel the file to match the item. Returns the item."""
    st = S["checklists"]
    rec = S["engine"].recording_state()
    if rec and rec["file"] == file:
        raise HTTPException(409, "this run is still being recorded")
    path = _run_path(file)
    if not path.exists():
        raise HTTPException(404, f"file not found: {file}")
    if relabel:
        attrs = {"checklist": name, "checklist_item": item["id"], "round": item.get("round"),
                 "session": item.get("session"), "kind": item["kind"], "load_level": str(item["load"])}
        if item.get("schedule"):
            attrs["sweep_schedule"] = json.dumps(item["schedule"])
            attrs["sweep_step_s"] = float(item.get("step_s") or 10.0)
        new = review.relabel_run(S["data_dir"], file, fault=item["fault"], drive_freq_hz=item["speed_hz"],
                                 load=f"L{item['load']}", tag=item_tag(item), attrs=attrs)
        if new != file:
            st.rename_file(file, new)
            e = S["engine"]
            if e.last_run and e.last_run.get("file") == file:
                e.last_run = dict(e.last_run, file=new, path=str(_run_path(new)))
        file, path = new, _run_path(new)
    report = review.check_file(path, motor_cfg())
    with h5py_file(path) as f:
        completed = bool(f.attrs.get("completed", True))
    return st.link(name, item["id"], file, report, completed)


def h5py_file(path):
    import h5py
    return h5py.File(path, "r")


def _find_item(name, item_id):
    doc = S["checklists"].load(name)
    it = next((x for x in (doc or {}).get("items", []) if x["id"] == item_id), None)
    if not it:
        raise HTTPException(404, f"checklist item {item_id} not found")
    return it


@app.post("/api/checklist/link")
def checklist_link(body: dict = Body(...)):
    it = _find_item(body["name"], body["id"])
    try:
        return link_file(body["name"], it, body["file"], bool(body.get("relabel", True)))
    except OSError as ex:
        raise HTTPException(409, f"cannot update the file (open elsewhere?): {ex}")


@app.post("/api/checklist/unlink")
def checklist_unlink(body: dict = Body(...)):
    try:
        return S["checklists"].unlink(body["name"], body["id"])
    except KeyError:
        raise HTTPException(404, "item not found")


@app.post("/api/checklist/move")
def checklist_move(body: dict = Body(...)):
    """Move every recorded item of one session to the matching items (kind, load, speed, repeat) of another."""
    name, src, dst = body["name"], body["from_session"], body["to_session"]
    relabel = bool(body.get("relabel", True))
    doc = S["checklists"].load(name)
    key = lambda it: (it["kind"], str(it["load"]), float(it["speed_hz"]), int(it.get("repeat", 1)))
    targets = {key(it): it for it in doc["items"] if it["session"] == dst}
    moved, skipped = [], []
    for it in [x for x in doc["items"] if x["session"] == src and x.get("file")]:
        tgt = targets.get(key(it))
        if not tgt:
            skipped.append(f"{it['id']}: no matching item in {dst}")
            continue
        if tgt.get("file"):
            skipped.append(f"{it['id']}: {tgt['id']} already has a file")
            continue
        try:
            r = link_file(name, tgt, it["file"], relabel)
            moved.append({"from": it["id"], "to": r["id"], "file": r["file"], "status": r["status"]})
        except (HTTPException, OSError) as ex:
            skipped.append(f"{it['id']}: {getattr(ex, 'detail', ex)}")
    return {"moved": moved, "skipped": skipped}


@app.post("/api/checklist/delete")
def checklist_delete(name: str = Body(..., embed=True)):
    S["checklists"].delete(name)
    return {"ok": True}


# ----------------------------------------------------------------------------- review
@app.get("/api/runs")
def runs():
    return review.list_runs(S["data_dir"])


def _run_path(file):
    try:
        return review.safe_path(S["data_dir"], file)
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.get("/api/run")
def run(file: str, fmax: float = 2000.0, spec_only: bool = False):
    p = _run_path(file)
    if not p.exists():
        raise HTTPException(404, "file not found")
    if S["engine"].recording_state() and S["engine"].recording_state()["file"] == file:
        raise HTTPException(409, "this run is still being recorded")
    return clean(review.run_detail(p, fmax=fmax, motor=motor_cfg(), spec_only=spec_only))


@app.get("/api/run/window")
def run_window(file: str, t0: float, t1: float):
    return review.run_window(_run_path(file), t0, t1)


@app.post("/api/run/flag")
def run_flag(file: str = Body(...), flag: str = Body("")):
    _run_path(file)
    review.set_flag(S["data_dir"], file, flag)
    return {"ok": True}


# ----------------------------------------------------------------------------- live stream
_check_cache = {"t": 0.0, "key": None, "report": None}


def live_checks(e, drive_hz):
    """Checks on the last 4 s, recomputed at most once a second (shared by all clients)."""
    now = time.time()
    key = (drive_hz, e.stream_t0)
    if _check_cache["report"] is not None and _check_cache["key"] == key and now - _check_cache["t"] < 1.0:
        return _check_cache["report"]
    x = e.snapshot(4.0)
    if x.shape[1] < int(e.rate * 1.0):
        rep = None
    else:
        rep = run_checks({c["name"]: x[i] for i, c in enumerate(e.chans)}, e.cfg, e.rate, drive_hz=drive_hz)
    _check_cache.update(t=now, key=key, report=rep)
    return rep


def live_frame(e, opt):
    window = float(min(max(opt.get("window_s", 1.0), 0.05), 10.0))
    avg = int(min(max(opt.get("avg", 1), 1), 8))
    fmax = float(opt.get("fmax", 2000.0))
    x = e.snapshot(window * avg)
    rate = e.rate
    n_win = min(x.shape[1], int(window * rate))
    out = {"t": time.time(), "rate": rate, "status": e.status, "recording": e.recording_state(),
           "n": int(n_win), "channels": []}
    if n_win < 32:
        return out
    for i, c in enumerate(e.chans):
        xi = x[i]
        w = xi[-n_win:]
        xs, ys = review.envelope(w, 900)
        f, a = amp_spectrum(xi, rate, seg_s=window if avg > 1 else None)
        fp, ap = review.pool_spectrum(f, a, fmax, 1500)
        fd, _ = peak_in(f, a, 2.0, fmax)
        ac = w - w.mean()
        rms = float(np.sqrt(np.mean(ac ** 2)))
        peak = float(np.abs(w).max())
        rng = e.channel_list()[i]["range"]
        out["channels"].append({
            "name": c["name"], "wave": {"t": review.r4(xs / rate), "y": review.r4(ys)},
            "spec": {"f": review.r4(fp), "a": review.r4(ap)},
            "stats": {"mean": float(w.mean()), "rms": rms, "peak": peak, "crest": peak / rms if rms else 0,
                      "dominant_hz": fd, "range_used": peak / rng if rng else 0},
        })
    if opt.get("checks", True):
        out["checks"] = live_checks(e, opt.get("drive_hz"))
    return out


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    opt = {"window_s": 1.0, "fmax": 2000.0, "avg": 1, "drive_hz": None, "fps": 5, "paused": False}

    async def receiver():
        while True:
            msg = await ws.receive_text()
            try:
                opt.update(json.loads(msg))
            except ValueError:
                pass

    rx = asyncio.create_task(receiver())
    try:
        while not rx.done():
            e = S["engine"]
            if not opt.get("paused") and e.status == "running":
                frame = await asyncio.to_thread(live_frame, e, dict(opt))
                await ws.send_text(json.dumps(clean(frame)))
                await asyncio.sleep(1.0 / max(1, min(10, opt.get("fps", 5))))
            else:
                await asyncio.sleep(0.5)  # stream off / page frozen: send nothing
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        rx.cancel()


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--demo", action="store_true", help="synthetic signals, no NI hardware or driver needed")
    g.add_argument("--sim", action="store_true", help="NI-DAQmx simulated devices")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--data", default=str(PROJECT_DIR / "data"), help="folder for recorded runs")
    p.add_argument("--port", type=int, default=8050)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--stopped", action="store_true", help="start with the live stream off")
    args = p.parse_args()

    mode = "demo" if args.demo else "sim" if args.sim else "daq"
    data_dir = Path(args.data).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    e = Engine(load_config(args.config), data_dir, mode)
    e.on_saved = _post_check
    e.on_discard = delete_run
    if args.stopped:
        e.user_stopped = True
        e._paused.set()
    S.update(engine=e, cfg_path=Path(args.config).resolve(), data_dir=data_dir, mode=mode,
             checklists=checklist.Store(data_dir), pending={})
    e.start()
    url = f"http://127.0.0.1:{args.port}"
    print(f"Sampling console ({mode}) at {url}   data -> {data_dir}   Ctrl+C to quit")
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        e.shutdown()


if __name__ == "__main__":
    sys.exit(main())
