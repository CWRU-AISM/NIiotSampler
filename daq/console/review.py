"""Load saved runs (.h5) for the review tab: run list, overview plots, spectra, spectrogram, checks."""

import csv
import json
import tomllib
from pathlib import Path

import h5py
import numpy as np

from checks import amp_spectrum, run_checks
from common import channel_units

WELCH_S = 8.0  # segment length for file spectra / checks (0.125 Hz bins)


def _py(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, np.ndarray):
        return v.tolist()
    return v


def safe_path(data_dir, rel):
    p = (Path(data_dir) / rel).resolve()
    if Path(data_dir).resolve() not in p.parents or p.suffix != ".h5":
        raise ValueError("file must be a .h5 inside the data folder")
    return p


def envelope(x, npts):
    """Min/max envelope interleaved, so a line plot looks like the full-rate trace."""
    n = len(x)
    if n <= 2 * npts:
        return np.arange(n), x
    b = n // npts
    y = x[: b * npts].reshape(npts, b)
    idx = np.arange(npts) * b
    xs = np.empty(2 * npts)
    ys = np.empty(2 * npts)
    xs[0::2], xs[1::2] = idx, idx + b // 2
    lo, hi = y.min(1), y.max(1)
    first_is_min = y.argmin(1) < y.argmax(1)
    ys[0::2] = np.where(first_is_min, lo, hi)
    ys[1::2] = np.where(first_is_min, hi, lo)
    return xs, ys


def pool_spectrum(f, a, fmax, npts):
    m = f <= fmax
    f, a = f[m], a[m]
    if len(f) <= npts:
        return f, a
    b = int(np.ceil(len(f) / npts))
    k = len(f) // b * b
    ai = a[:k].reshape(-1, b)
    j = ai.argmax(1)
    return f[:k].reshape(-1, b)[np.arange(len(j)), j], ai.max(1)


def r4(arr):
    return [float(f"{v:.5g}") for v in np.asarray(arr, dtype=float).tolist()]


def _index_rows(data_dir):
    rows = {}
    idx = Path(data_dir) / "runs_index.csv"
    if idx.exists():
        with idx.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                rows[r["file"].replace("\\", "/")] = r
    return rows


def load_flags(data_dir):
    p = Path(data_dir) / "run_flags.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_flag(data_dir, rel, flag):
    flags = load_flags(data_dir)
    if flag:
        flags[rel] = flag
    else:
        flags.pop(rel, None)
    (Path(data_dir) / "run_flags.json").write_text(json.dumps(flags, indent=1), encoding="utf-8")


TRASH = "_trash"


def relabel_run(data_dir, rel, fault=None, drive_freq_hz=None, load=None, tag=None, attrs=None):
    """Correct a saved run's labels: HDF5 root attrs, filename, runs_index.csv row, flags.

    The previous labels are appended to the file's `relabelled_from` attr (JSON list), so nothing is lost.
    Returns the new relative path (unchanged if the name did not change).
    """
    import datetime as dt
    from recorder import slug as rslug

    data_dir = Path(data_dir)
    src = safe_path(data_dir, rel)
    with h5py.File(src, "r+") as f:
        old = {k: _py(f.attrs[k]) for k in ("fault", "drive_freq_hz", "load", "tag", "checklist", "checklist_item")
               if k in f.attrs}
        hist = json.loads(_py(f.attrs.get("relabelled_from", "[]")) or "[]")
        hist.append(old | {"file": rel, "at": dt.datetime.now().astimezone().isoformat(timespec="seconds")})
        f.attrs["relabelled_from"] = json.dumps(hist)
        new = {"fault": fault, "drive_freq_hz": None if drive_freq_hz is None else float(drive_freq_hz),
               "load": load, "tag": tag}
        for k, v in list(new.items()) + list((attrs or {}).items()):
            if v is not None:
                f.attrs[k] = v
        fault_, freq_, load_, tag_ = (_py(f.attrs.get("fault", "")), float(f.attrs.get("drive_freq_hz", 0)),
                                      _py(f.attrs.get("load", "none")), _py(f.attrs.get("tag", "")))
    # new filename: keep the original timestamp prefix
    stamp = src.name[:15]
    parts = [stamp, rslug(fault_), f"{freq_:g}Hz"]
    if load_ and str(load_).lower() != "none":
        parts.append(rslug(load_))
    if tag_:
        parts.append(rslug(tag_))
    dst = src.with_name("_".join(parts) + ".h5")
    if dst != src:
        if dst.exists():
            dst = dst.with_name(dst.stem + "_b.h5")
        src.replace(dst)
    new_rel = dst.relative_to(data_dir).as_posix()
    # runs_index.csv: same row, new file / labels (keep the row's own path separator style)
    index = data_dir / "runs_index.csv"
    if index.exists():
        with index.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        head = rows[0] if rows else []
        col = {h: i for i, h in enumerate(head)}
        for r in rows[1:]:
            if r and r[0].replace("\\", "/") == rel:
                sep = "\\" if "\\" in r[0] else "/"
                r[0] = new_rel.replace("/", sep)
                for k, v in (("fault", fault_), ("drive_freq_hz", f"{freq_:g}"), ("load", load_)):
                    if k in col:
                        r[col[k]] = v
        with index.open("w", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerows(rows)
    flags = load_flags(data_dir)
    if rel in flags and new_rel != rel:
        set_flag(data_dir, new_rel, flags[rel])
        set_flag(data_dir, rel, "")
    return new_rel


def delete_run(data_dir, rel):
    """Move a run to data/_trash/<same path> and drop it from runs_index.csv and the flags.

    Nothing is erased: to undo, move the file back (it then shows up again, without an index row).
    """
    data_dir = Path(data_dir)
    src = safe_path(data_dir, rel)
    if not src.exists():
        raise FileNotFoundError(rel)
    dst = data_dir / TRASH / src.relative_to(data_dir)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst = dst.with_name(f"{dst.stem}_{int(src.stat().st_mtime)}{dst.suffix}")
    src.replace(dst)
    index = data_dir / "runs_index.csv"
    if index.exists():
        with index.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        keep = [r for i, r in enumerate(rows) if i == 0 or not r or r[0].replace("\\", "/") != rel]
        if len(keep) != len(rows):
            with index.open("w", encoding="utf-8", newline="") as fh:
                csv.writer(fh).writerows(keep)
    if rel in load_flags(data_dir):
        set_flag(data_dir, rel, "")
    return dst.relative_to(data_dir).as_posix()


_attr_cache = {}


def _quick_attrs(p):
    key = (str(p), p.stat().st_mtime)
    if key not in _attr_cache:
        try:
            with h5py.File(p, "r") as f:
                a = {k: _py(f.attrs[k]) for k in ("fault", "drive_freq_hz", "load", "start_time", "duration_s",
                                                   "completed", "operator", "notes", "simulated", "source")
                     if k in f.attrs}
                a["channels"] = list(f["vib"].keys()) if "vib" in f else []
        except OSError as e:
            a = {"error": f"unreadable: {e}"}
        _attr_cache[key] = a
    return _attr_cache[key]


def list_runs(data_dir):
    data_dir = Path(data_dir)
    index, flags = _index_rows(data_dir), load_flags(data_dir)
    runs = []
    for p in sorted(data_dir.rglob("*.h5"), reverse=True):
        rel = p.relative_to(data_dir).as_posix()
        if rel.startswith(TRASH + "/"):
            continue
        a = _quick_attrs(p)
        row = index.get(rel, {})
        runs.append({
            "file": rel, "name": p.name, "size_mb": round(p.stat().st_size / 1e6, 2),
            "start_time": a.get("start_time") or row.get("start_time", ""),
            "fault": a.get("fault", row.get("fault", "")), "drive_freq_hz": a.get("drive_freq_hz", row.get("drive_freq_hz")),
            "load": a.get("load", row.get("load", "")), "duration_s": a.get("duration_s", row.get("duration_s")),
            "completed": a.get("completed"), "operator": a.get("operator", ""), "notes": a.get("notes", ""),
            "simulated": a.get("simulated"), "source": a.get("source", ""), "indexed": bool(row),
            "flag": flags.get(rel, ""), "error": a.get("error"),
        })
    return runs


def load_run(path):
    """-> meta (root attrs + vib attrs), rate, cfg used for the run, {chan: array}, {chan: attrs}."""
    with h5py.File(path, "r") as f:
        meta = {k: _py(v) for k, v in f.attrs.items()}
        g = f["vib"]
        vib = {k: _py(v) for k, v in g.attrs.items()}
        data = {k: g[k][:] for k in g.keys()}
        cattrs = {k: {a: _py(v) for a, v in g[k].attrs.items()} for k in g.keys()}
        temp = None
        if "temp" in f:
            temp = {k: f["temp"][k][:].tolist() for k in f["temp"].keys()}
    meta["n_samples"] = vib.get("n_samples", len(next(iter(data.values()), [])))
    rate = float(vib["sample_rate"])
    try:
        cfg = tomllib.loads(meta["config_toml"])
    except (KeyError, tomllib.TOMLDecodeError):
        cfg = None
    if not cfg:  # rebuild a minimal config from the channel attrs
        cfg = {"vib": {"channels": [dict(c) | {"name": n} for n, c in cattrs.items()]}}
    cfg["vib"]["channels"] = [c for c in cfg["vib"]["channels"] if c["name"] in data]
    return meta, vib, rate, cfg, data, cattrs, temp


def schedule_of(meta):
    try:
        sched = json.loads(meta.get("sweep_schedule") or "null")
    except ValueError:
        sched = None
    return sched, meta.get("sweep_step_s")


def check_file(path, motor=None):
    meta, vib, rate, cfg, data, _, _ = load_run(path)
    if motor and "motor" not in cfg:
        cfg["motor"] = motor
    sched, step = schedule_of(meta)
    return run_checks(data, cfg, rate, drive_hz=meta.get("drive_freq_hz"), meta=meta, seg_s=WELCH_S,
                      schedule=sched, step_s=step)


def spectrogram(x, rate, fmax, nt=240, nf=256):
    seg = int(2 ** np.ceil(np.log2(max(256, rate * 0.25))))
    hop = max(1, (len(x) - seg) // max(1, nt - 1)) if len(x) > seg else seg
    win = np.hanning(seg)
    f = np.fft.rfftfreq(seg, 1 / rate)
    m = f <= fmax
    starts = np.arange(0, max(1, len(x) - seg + 1), hop)[:nt]
    rows = []
    for s in starts:
        y = x[s:s + seg]
        if len(y) < seg:
            y = np.pad(y, (0, seg - len(y)))
        rows.append(np.abs(np.fft.rfft((y - y.mean()) * win))[m] * 2 / win.sum())
    S = np.array(rows)  # (time, freq)
    k = S.shape[1]
    b = max(1, int(np.ceil(k / nf)))
    S = S[:, : k // b * b].reshape(len(starts), -1, b).max(2)
    db = 20 * np.log10(S + 1e-12)
    return {"t0": float(starts[0] / rate), "dt": float(hop / rate), "fmax": float(f[m][-1]),
            "nf": int(db.shape[1]), "db": [r4(r) for r in np.round(db, 1)],
            "lo": float(np.percentile(db, 5)), "hi": float(db.max())}


def run_detail(path, fmax=2000.0, motor=None, npts=3000, spec_only=False):
    meta, vib, rate, cfg, data, cattrs, temp = load_run(path)
    if motor and "motor" not in cfg:
        cfg["motor"] = motor
    sched, step = schedule_of(meta)
    report = run_checks(data, cfg, rate, drive_hz=meta.get("drive_freq_hz"), meta=meta, seg_s=WELCH_S,
                        schedule=sched, step_s=step)
    chans = []
    for ch in cfg["vib"]["channels"]:
        x = data[ch["name"]]
        f, a = amp_spectrum(x, rate, WELCH_S)
        fp, ap = pool_spectrum(f, a, fmax, npts)
        c = {"name": ch["name"], "kind": ch["kind"], "units": channel_units(ch),
             "physical": ch.get("physical", ""), "location": ch.get("location", ""),
             "spec": {"f": r4(fp), "a": r4(ap)}}
        if not spec_only:
            xs, ys = envelope(x, npts)
            c["wave"] = {"t": r4(xs / rate), "y": r4(ys)}
            c["sgram"] = spectrogram(x, rate, fmax)
        chans.append(c)
    meta.pop("config_toml", None)
    return {"meta": meta, "vib": vib, "rate": rate, "duration_s": len(next(iter(data.values()), [])) / rate,
            "motor": cfg.get("motor"), "channels": chans, "report": report, "temp": temp,
            "config_toml": load_run_config_text(path)}


def load_run_config_text(path):
    with h5py.File(path, "r") as f:
        return _py(f.attrs.get("config_toml", ""))


def run_window(path, t0, t1, npts=3000):
    with h5py.File(path, "r") as f:
        g = f["vib"]
        rate = float(g.attrs["sample_rate"])
        i0, i1 = max(0, int(t0 * rate)), int(t1 * rate)
        out = {}
        for k in g.keys():
            x = g[k][i0:i1]
            xs, ys = envelope(x, npts)
            out[k] = {"t": r4((xs + i0) / rate), "y": r4(ys)}
    return out
