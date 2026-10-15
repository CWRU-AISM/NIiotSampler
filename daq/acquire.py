"""Record one run: vibration + motor current (cDAQ1) and, if [temp] is enabled, housing temperature to HDF5.

  python daq/acquire.py --duration 60 --freq 41 --fault normal --load none --notes "baseline"
  python daq/acquire.py --sim --duration 5 --freq 41 --fault test      # simulated devices

Output: data/YYYY-MM-DD/<YYYYmmdd_HHMMSS>_<fault>_<freq>Hz.h5, plus one row in data/runs_index.csv.

HDF5 layout
  /            attrs: run conditions (fault, drive_freq_hz, load, notes...), versions, config text
  /vib         attrs: sample_rate, t0_unix, device info
  /vib/<chan>  float64 samples in engineering units (g, A); attrs: units, physical channel, ...
  /temp/t_s    seconds since /vib t0 (PC clock; the two chassis share no hardware clock)
  /temp/<chan> °C
Ctrl+C stops early and keeps what was recorded (root attr completed = False).
"""

import argparse
import csv
import datetime as dt
import re
import socket
import sys
import time
from importlib.metadata import version
from pathlib import Path

import h5py
import numpy as np
import nidaqmx
from nidaqmx.stream_readers import AnalogMultiChannelReader

from common import (
    DEFAULT_CONFIG,
    PROJECT_DIR,
    TempLogger,
    build_vib_task,
    channel_units,
    check_module,
    device_info,
    device_name,
    driver_version,
    load_config,
    temp_enabled,
    volts_to_units,
)


def _slug(s):
    return re.sub(r"[^A-Za-z0-9.]+", "-", s).strip("-") or "na"


def _chan_attrs(ch, units):
    return {k: v for k, v in ch.items() if isinstance(v, (int, float, str))} | {"units": units}


def record(args, cfg):
    start = dt.datetime.now().astimezone()
    out_dir = Path(args.out) / f"{start:%Y-%m-%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{start:%Y%m%d_%H%M%S}_{_slug(args.fault)}_{args.freq:g}Hz.h5"

    chans = cfg["vib"]["channels"]
    check_module(cfg["vib"], args.sim)
    temp = None
    if temp_enabled(cfg) and not args.no_temp:
        check_module(cfg["temp"], args.sim)
        temp = TempLogger(cfg, args.sim)

    with build_vib_task(cfg, args.sim) as task, h5py.File(path, "w-") as f:
        rate = task.timing.samp_clk_rate
        chunk = max(1, int(rate * 0.1))
        n_total = int(round(args.duration * rate))
        scale = np.array([volts_to_units(c) for c in chans])[:, None]
        reader = AnalogMultiChannelReader(task.in_stream)

        f.attrs.update(
            fault=args.fault, drive_freq_hz=args.freq, load=args.load, notes=args.notes,
            operator=args.operator, duration_requested_s=args.duration, settle_s=args.settle,
            start_time=start.isoformat(), host=socket.gethostname(), simulated=args.sim,
            daqmx_version=driver_version(), nidaqmx_python=version("nidaqmx"),
            config_toml=cfg["_text"],
        )
        g = f.create_group("vib")
        g.attrs.update(sample_rate=rate, **{f"device_{k}": str(v) for k, v in
                                            device_info(device_name(cfg["vib"], args.sim)).items()})
        dsets = []
        for ch in chans:
            ds = g.create_dataset(ch["name"], shape=(0,), maxshape=(None,), chunks=(chunk,), dtype="f8")
            ds.attrs.update(_chan_attrs(ch, channel_units(ch)))
            dsets.append(ds)

        task.start()
        n_settle = int(round(args.settle * rate))
        while n_settle > 0:  # let IEPE bias / AC coupling settle; discard
            n = min(chunk, n_settle)
            reader.read_many_sample(np.empty((len(chans), n)), n, timeout=10.0)
            n_settle -= n
        t0 = time.time()
        if temp:
            temp.start()
        g.attrs["t0_unix"] = t0
        print(f"Recording {args.duration:g} s at {rate:g} S/s -> {path}")

        got, completed = 0, False
        buf = np.empty((len(chans), chunk))
        try:
            while got < n_total:
                n = min(chunk, n_total - got)
                b = buf if n == chunk else np.empty((len(chans), n))
                reader.read_many_sample(b, n, timeout=10.0)
                b *= scale
                for ds, row in zip(dsets, b):
                    ds.resize((got + n,))
                    ds[got:] = row
                got += n
                if got % int(rate) < n:
                    print(f"\r  {got / rate:6.1f} / {args.duration:g} s", end="", flush=True)
            completed = True
        except KeyboardInterrupt:
            print("\nStopped by user; saving partial run.")
        finally:
            task.stop()
            print()
            if temp:
                temp.stop()
                tg = f.create_group("temp")
                t_unix = np.asarray(temp.t)
                tg.create_dataset("t_unix", data=t_unix)
                tg.create_dataset("t_s", data=t_unix - t0)
                vals = np.asarray(temp.values).reshape(len(t_unix), -1)
                for i, ch in enumerate(cfg["temp"]["channels"]):
                    tg.create_dataset(ch["name"], data=vals[:, i]).attrs.update(_chan_attrs(ch, "degC"))
                tg.attrs.update({f"device_{k}": str(v) for k, v in
                                 device_info(device_name(cfg["temp"], args.sim)).items()})
                if temp.error:
                    tg.attrs["error"] = str(temp.error)
                    print(f"WARNING: temperature logging stopped early: {temp.error}")
            g.attrs["n_samples"] = got
            f.attrs["completed"] = completed
            f.attrs["duration_s"] = got / rate

        for ch, ds in zip(chans, dsets):
            x = ds[:]
            if len(x):
                print(f"  {ch['name']:<18} RMS(ac) {np.std(x):8.4f} {channel_units(ch)}")
        if temp and len(temp.values):
            print(f"  temperature        {np.asarray(temp.values)[-1].round(2).tolist()} °C")

    _append_index(path, start, args, got / rate, completed)
    print(f"Saved {path}")


def _append_index(path, start, args, duration, completed):
    index = Path(args.out) / "runs_index.csv"
    new = not index.exists()
    with index.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["file", "start_time", "fault", "drive_freq_hz", "load", "duration_s",
                        "completed", "simulated", "operator", "notes"])
        w.writerow([path.relative_to(args.out), start.isoformat(timespec="seconds"), args.fault,
                    args.freq, args.load, f"{duration:.2f}", completed, args.sim, args.operator, args.notes])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--duration", type=float, required=True, help="recording length [s]")
    p.add_argument("--freq", type=float, required=True, help="VFD frequency shown on the Lenze drive [Hz]")
    p.add_argument("--fault", required=True,
                   help="e.g. normal, unbalance, misalignment, bearing_inner, bearing_outer, broken_bar")
    p.add_argument("--load", default="none", help="load condition")
    p.add_argument("--notes", default="", help="anything else: sensor moves, rotor disk bolts, ...")
    p.add_argument("--operator", default="")
    p.add_argument("--settle", type=float, default=2.0, help="seconds discarded after start")
    p.add_argument("--no-temp", action="store_true",
                   help="skip the thermocouple chassis (automatic when [temp] enabled = false)")
    p.add_argument("--sim", action="store_true", help="use simulated devices")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--out", default=str(PROJECT_DIR / "data"))
    args = p.parse_args()
    args.out = Path(args.out)
    try:
        record(args, load_config(args.config))
    except nidaqmx.DaqError as e:
        print(f"DAQmx error {e.error_code}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
