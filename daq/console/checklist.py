"""Recording checklists: a plan (faults x loads x speeds x rounds, plus sweeps) expanded into items, with progress.

Stored as JSON in data/checklists/<name>.json. Import/export use the same file, so a checklist can be prepared
elsewhere, moved between PCs, and archived with the data as a lab log.

File format ("format": "nisampler-checklist/1"):
{
  "format": "nisampler-checklist/1",
  "name": "im-fault-study",
  "plan": {                                  # optional when "items" is given; used to (re)generate items
    "faults": ["health", "unbalanced"], "loads": [0, 1, 2], "speeds_hz": [10, 20, 30],
    "rounds": 3, "repeats": 1, "duration_s": 60, "order": "serpentine",     # ascending | serpentine | shuffled
    "changeover_s": 45,                      # time estimate only: wait for steady speed + paperwork per item
    "sweeps": {
      "speed": {"enabled": true, "step_s": 10, "loads": [0, 5]},              # steps = speeds_hz
      "load":  {"enabled": true, "step_s": 20, "speeds_hz": [20, 35, 50]}     # steps = loads
    }
  },
  "items": [                                 # one per recording
    {"id": "R1-health-L0-10Hz", "session": "R1-health", "round": 1, "fault": "health", "kind": "steady",
     "load": 0, "speed_hz": 10, "duration_s": 60, "repeat": 1,
     "status": "todo",                       # todo | done | attention | skipped
     "file": null, "summary": null, "recorded_at": null, "note": ""},
    {"id": "R1-health-sweep-speed-L0", "kind": "sweep_speed", "schedule": [{"t": 0, "speed_hz": 10, "load": 0}, ...], ...}
  ]
}
"""

import datetime as dt
import json
import random
import re
import threading
from pathlib import Path

FORMAT = "nisampler-checklist/1"
STATUSES = ("todo", "done", "attention", "skipped")
_lock = threading.RLock()


def slug(s):
    return re.sub(r"[^A-Za-z0-9.]+", "-", str(s)).strip("-") or "na"


def _num(v):
    f = float(v)
    return int(f) if f.is_integer() else f


def _order_speeds(speeds, mode, block, rnd):
    s = list(speeds)
    if mode == "serpentine" and block % 2 == 1:
        s.reverse()
    elif mode == "shuffled":
        rnd.shuffle(s)
    return s


def expand(plan):
    """plan -> items. Deterministic (shuffled order is seeded by name + round)."""
    faults = [str(f).strip() for f in plan["faults"] if str(f).strip()]
    loads = [_num(x) for x in plan["loads"]]
    speeds = [_num(x) for x in plan["speeds_hz"]]
    rounds, repeats = int(plan.get("rounds", 1)), int(plan.get("repeats", 1))
    dur, order = float(plan.get("duration_s", 60)), plan.get("order", "serpentine")
    sweeps = plan.get("sweeps", {}) or {}
    if not faults or not loads or not speeds:
        raise ValueError("plan needs at least one fault, load and speed")
    items = []
    for r in range(1, rounds + 1):
        for fault in faults:
            sess = f"R{r}-{slug(fault)}"
            rnd = random.Random(f"{plan.get('name', '')}|{r}|{fault}")
            base = dict(session=sess, round=r, fault=fault, status="todo", file=None, summary=None,
                        recorded_at=None, note="")
            for b, load in enumerate(loads):
                for sp in _order_speeds(speeds, order, b, rnd):
                    for k in range(1, repeats + 1):
                        iid = f"{sess}-L{load}-{sp:g}Hz" + (f"-k{k}" if repeats > 1 else "")
                        items.append(dict(base, id=iid, kind="steady", load=load, speed_hz=sp, duration_s=dur, repeat=k))
            sw = sweeps.get("speed") or {}
            if sw.get("enabled"):
                step = float(sw.get("step_s", 10))
                steps = [_num(x) for x in sw.get("steps_hz") or speeds]
                for load in [_num(x) for x in (sw.get("loads") or loads)]:
                    sched = [{"t": i * step, "speed_hz": s, "load": load} for i, s in enumerate(steps)]
                    items.append(dict(base, id=f"{sess}-sweep-speed-L{load}", kind="sweep_speed", load=load,
                                      speed_hz=steps[0], duration_s=step * len(steps), repeat=1, schedule=sched,
                                      step_s=step))
            sw = sweeps.get("load") or {}
            if sw.get("enabled"):
                step = float(sw.get("step_s", 20))
                steps = [_num(x) for x in sw.get("steps") or loads]
                for sp in [_num(x) for x in (sw.get("speeds_hz") or speeds)]:
                    sched = [{"t": i * step, "speed_hz": sp, "load": ld} for i, ld in enumerate(steps)]
                    items.append(dict(base, id=f"{sess}-sweep-load-{sp:g}Hz", kind="sweep_load", load=steps[0],
                                      speed_hz=sp, duration_s=step * len(steps), repeat=1, schedule=sched,
                                      step_s=step))
    ids = [it["id"] for it in items]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate items: check for repeated faults / loads / speeds in the plan")
    return items


def validate_items(items):
    req = ("id", "fault", "kind", "speed_hz", "load", "duration_s")
    seen = set()
    out = []
    for i, it in enumerate(items):
        miss = [k for k in req if k not in it]
        if miss:
            raise ValueError(f"item {i}: missing {', '.join(miss)}")
        if it["id"] in seen:
            raise ValueError(f"duplicate item id {it['id']}")
        seen.add(it["id"])
        if it["kind"] not in ("steady", "sweep_speed", "sweep_load"):
            raise ValueError(f"item {it['id']}: unknown kind {it['kind']}")
        if it["kind"] != "steady" and not it.get("schedule"):
            raise ValueError(f"item {it['id']}: a sweep needs a schedule")
        it = dict(it)
        it.setdefault("round", 1)
        it.setdefault("session", f"R{it['round']}-{slug(it['fault'])}")
        it.setdefault("repeat", 1)
        if it.get("status") not in STATUSES:
            it["status"] = "todo"
        for k in ("file", "summary", "recorded_at"):
            it.setdefault(k, None)
        it.setdefault("note", "")
        out.append(it)
    return out


# ----------------------------------------------------------------------------- storage
class Store:
    def __init__(self, data_dir):
        self.dir = Path(data_dir) / "checklists"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name):
        return self.dir / f"{slug(name)}.json"

    def names(self):
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                n = len(d.get("items", []))
                done = sum(it.get("status") in ("done", "skipped") for it in d.get("items", []))
                out.append({"name": d.get("name", p.stem), "items": n, "done": done, "updated": d.get("updated")})
            except (OSError, ValueError):
                continue
        return out

    @property
    def active(self):
        p = self.dir / "_active.txt"
        name = p.read_text(encoding="utf-8").strip() if p.exists() else ""
        return name if name and self._path(name).exists() else None

    @active.setter
    def active(self, name):
        (self.dir / "_active.txt").write_text(name or "", encoding="utf-8")

    def load(self, name=None):
        name = name or self.active
        if not name:
            return None
        return json.loads(self._path(name).read_text(encoding="utf-8"))

    def save(self, doc):
        doc["format"] = FORMAT
        doc["updated"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        p = self._path(doc["name"])
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    def create(self, name, plan=None, items=None, overwrite=False):
        with _lock:
            name = slug(name)
            if self._path(name).exists() and not overwrite:
                k = 2
                while self._path(f"{name}-{k}").exists():
                    k += 1
                name = f"{name}-{k}"
            if items is None:
                items = expand(plan | {"name": name})
            doc = {"format": FORMAT, "name": name,
                   "created": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                   "plan": plan, "items": validate_items(items)}
            self.save(doc)
            self.active = name
            return doc

    def import_doc(self, doc):
        if not isinstance(doc, dict):
            raise ValueError("not a checklist JSON object")
        fmt = doc.get("format", FORMAT)
        if not str(fmt).startswith("nisampler-checklist/"):
            raise ValueError(f"unknown format {fmt!r}")
        name = doc.get("name") or "imported"
        if doc.get("items"):
            return self.create(name, plan=doc.get("plan"), items=doc["items"])
        if doc.get("plan"):
            return self.create(name, plan=doc["plan"])
        raise ValueError("the file has neither 'items' nor 'plan'")

    def delete(self, name):
        with _lock:
            p = self._path(name)
            if p.exists():
                p.replace(p.with_suffix(".json.deleted"))
            if self.active == slug(name):
                self.active = ""

    def update_item(self, name, item_id, **changes):
        with _lock:
            doc = self.load(name)
            for it in doc["items"]:
                if it["id"] == item_id:
                    it.update(changes)
                    self.save(doc)
                    return it
            raise KeyError(item_id)

    def record_result(self, name, item_id, file, report, completed):
        """Called after a checklist recording is saved and checked."""
        n = {s: 0 for s in ("pass", "warn", "fail")}
        for c in (report or {}).get("checks", []):
            if c["status"] in n:
                n[c["status"]] += 1
        ok = completed and n["fail"] == 0 and not (report or {}).get("error")
        bad = [c["title"] for c in (report or {}).get("checks", []) if c["status"] == "fail"]
        if not completed:
            bad.insert(0, "stopped early")
        return self.update_item(name, item_id, status="done" if ok else "attention", file=file,
                                summary=n | {"problems": bad[:4]},
                                recorded_at=dt.datetime.now().astimezone().isoformat(timespec="seconds"))

    def link(self, name, item_id, file, report, completed=True):
        """Attach an existing recording to an item (it is detached from any other item first)."""
        with _lock:
            self.forget_file(file)
            return self.record_result(name, item_id, file, report, completed)

    def unlink(self, name, item_id):
        return self.update_item(name, item_id, status="todo", file=None, summary=None, recorded_at=None)

    def rename_file(self, old, new):
        with _lock:
            for p in self.dir.glob("*.json"):
                doc = json.loads(p.read_text(encoding="utf-8"))
                hit = False
                for it in doc.get("items", []):
                    if it.get("file") == old:
                        it["file"] = new
                        hit = True
                if hit:
                    self.save(doc)

    def forget_file(self, file):
        """A run was deleted: put its checklist item back to 'todo' (searches all checklists)."""
        with _lock:
            hits = []
            for p in self.dir.glob("*.json"):
                doc = json.loads(p.read_text(encoding="utf-8"))
                changed = False
                for it in doc.get("items", []):
                    if it.get("file") == file:
                        it.update(status="todo", file=None, summary=None, recorded_at=None)
                        hits.append((doc["name"], it["id"]))
                        changed = True
                if changed:
                    self.save(doc)
            return hits


def estimate_s(items, changeover_s=45.0):
    return sum(float(it["duration_s"]) + changeover_s for it in items)
