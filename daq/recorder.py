"""RunWriter: one run -> HDF5 + a row in runs_index.csv. Shared by daq/acquire.py and the console.

Layout (unchanged from the original acquire.py):
  /            attrs: run conditions (fault, drive_freq_hz, load, notes...), versions, config text
  /vib         attrs: sample_rate, t0_unix, n_samples, device info
  /vib/<chan>  float64 samples in engineering units (g, A); attrs: units, physical channel, ...
  /temp/...    only when a TempLogger is passed
"""

import csv
import datetime as dt
import re
import socket
import time
from importlib.metadata import version
from pathlib import Path

import h5py
import numpy as np

from common import channel_units, driver_version

INDEX_COLUMNS = ["file", "start_time", "fault", "drive_freq_hz", "load", "duration_s",
                 "completed", "simulated", "operator", "notes"]


def slug(s):
    return re.sub(r"[^A-Za-z0-9.]+", "-", str(s)).strip("-") or "na"


def run_filename(start, fault, freq, load="none", tag=""):
    """<YYYYmmdd_HHMMSS>_<fault>_<freq>Hz[_<load>][_<tag>].h5"""
    parts = [f"{start:%Y%m%d_%H%M%S}", slug(fault), f"{float(freq):g}Hz"]
    if load and str(load).lower() != "none":
        parts.append(slug(load))
    if tag:
        parts.append(slug(tag))
    return "_".join(parts) + ".h5"


def _chan_attrs(ch, units):
    return {k: v for k, v in ch.items() if isinstance(v, (int, float, str))} | {"units": units}


def _versions():
    try:
        dmx = driver_version()
    except Exception:  # demo mode on a PC without NI-DAQmx
        dmx = "n/a"
    try:
        py = version("nidaqmx")
    except Exception:
        py = "n/a"
    return dmx, py


class RunWriter:
    """Create the file, then write() blocks of engineering-unit samples (n_chan, n), then close()."""

    def __init__(self, out_root, cfg, rate, dev_info, *, fault, freq, duration, load="none", notes="",
                 operator="", settle=2.0, simulated=False, tag="", source="cli", temp=None, temp_info=None,
                 extra_attrs=None):
        self.out_root = Path(out_root)
        self.start = dt.datetime.now().astimezone()
        out_dir = self.out_root / f"{self.start:%Y-%m-%d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.path = out_dir / run_filename(self.start, fault, freq, load, tag)
        self.cfg, self.rate, self.temp, self.temp_info = cfg, float(rate), temp, temp_info
        self.meta = dict(fault=fault, freq=freq, load=load, notes=notes, operator=operator, simulated=simulated)
        self.chans = cfg["vib"]["channels"]
        self.n_total = int(round(duration * self.rate))
        self.got = 0
        self.t0 = None
        chunk = max(1, int(self.rate * 0.1))

        dmx, py = _versions()
        self.f = h5py.File(self.path, "w-")
        self.f.attrs.update(
            fault=fault, drive_freq_hz=float(freq), load=load, notes=notes, operator=operator,
            duration_requested_s=float(duration), settle_s=float(settle), start_time=self.start.isoformat(),
            host=socket.gethostname(), simulated=bool(simulated), daqmx_version=dmx, nidaqmx_python=py,
            config_toml=cfg["_text"], tag=tag, source=source,
        )
        if extra_attrs:  # e.g. checklist name / item id / sweep schedule (JSON text)
            self.f.attrs.update({k: v for k, v in extra_attrs.items() if v is not None})
        self.g = self.f.create_group("vib")
        self.g.attrs.update(sample_rate=self.rate, **{f"device_{k}": str(v) for k, v in dev_info.items()})
        self.dsets = []
        for ch in self.chans:
            ds = self.g.create_dataset(ch["name"], shape=(0,), maxshape=(None,), chunks=(chunk,), dtype="f8")
            ds.attrs.update(_chan_attrs(ch, channel_units(ch)))
            self.dsets.append(ds)

    @property
    def done(self):
        return self.got >= self.n_total

    @property
    def elapsed_s(self):
        return self.got / self.rate

    def begin(self):
        """Call right before the first write(): stamps t0 and starts temperature logging."""
        self.t0 = time.time()
        self.g.attrs["t0_unix"] = self.t0
        if self.temp:
            self.temp.start()

    def write(self, block):
        """Append (n_chan, n) samples; anything beyond the requested duration is dropped."""
        if self.t0 is None:
            self.begin()
        n = min(block.shape[1], self.n_total - self.got)
        if n <= 0:
            return 0
        for ds, row in zip(self.dsets, block[:, :n]):
            ds.resize((self.got + n,))
            ds[self.got:] = row
        self.got += n
        return n

    def close(self, completed=None, error=None):
        """Finish the file and append the index row. Returns a small summary dict."""
        completed = self.done if completed is None else completed
        temp_warning = None
        try:
            if self.temp:
                self.temp.stop()
                tg = self.f.create_group("temp")
                t_unix = np.asarray(self.temp.t)
                tg.create_dataset("t_unix", data=t_unix)
                tg.create_dataset("t_s", data=t_unix - (self.t0 or 0.0))
                vals = np.asarray(self.temp.values).reshape(len(t_unix), -1)
                for i, ch in enumerate(self.cfg["temp"]["channels"]):
                    tg.create_dataset(ch["name"], data=vals[:, i]).attrs.update(_chan_attrs(ch, "degC"))
                tg.attrs.update({f"device_{k}": str(v) for k, v in (self.temp_info or {}).items()})
                if self.temp.error:
                    tg.attrs["error"] = str(self.temp.error)
                    temp_warning = f"temperature logging stopped early: {self.temp.error}"
            self.g.attrs["n_samples"] = self.got
            self.f.attrs["completed"] = bool(completed)
            self.f.attrs["duration_s"] = self.got / self.rate
            if error:
                self.f.attrs["error"] = str(error)
            rms = {ch["name"]: float(np.std(ds[:])) if self.got else float("nan")
                   for ch, ds in zip(self.chans, self.dsets)}
        finally:
            self.f.close()
        self._append_index(completed)
        return dict(path=str(self.path), file=self.rel_path, completed=bool(completed),
                    duration_s=self.got / self.rate, rms=rms, temp_warning=temp_warning,
                    error=str(error) if error else None)

    @property
    def rel_path(self):
        return self.path.relative_to(self.out_root).as_posix()

    def _append_index(self, completed):
        index = self.out_root / "runs_index.csv"
        new = not index.exists()
        m = self.meta
        with index.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(INDEX_COLUMNS)
            w.writerow([self.path.relative_to(self.out_root), self.start.isoformat(timespec="seconds"), m["fault"],
                        m["freq"], m["load"], f"{self.got / self.rate:.2f}", completed, m["simulated"],
                        m["operator"], m["notes"]])
