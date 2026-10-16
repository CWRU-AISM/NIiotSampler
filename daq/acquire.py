"""Record one run: vibration + motor current (cDAQ1) and, if [temp] is enabled, housing temperature to HDF5.

  python daq/acquire.py --duration 60 --freq 41 --fault normal --load none --notes "baseline"
  python daq/acquire.py --sim --duration 5 --freq 41 --fault test      # simulated devices

Output: data/YYYY-MM-DD/<YYYYmmdd_HHMMSS>_<fault>_<freq>Hz[_<load>].h5, plus one row in data/runs_index.csv.
The file is written by recorder.RunWriter (the sampling console writes the same format).

HDF5 layout
  /            attrs: run conditions (fault, drive_freq_hz, load, notes...), versions, config text
  /vib         attrs: sample_rate, t0_unix, device info
  /vib/<chan>  float64 samples in engineering units (g, A); attrs: units, physical channel, ...
  /temp/t_s    seconds since /vib t0 (PC clock; temperature reads are software-timed)
  /temp/<chan> °C
Ctrl+C stops early and keeps what was recorded (root attr completed = False).
"""

import argparse
import sys
from pathlib import Path

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
    load_config,
    temp_enabled,
    volts_to_units,
)
from recorder import RunWriter


def record(args, cfg):
    chans = cfg["vib"]["channels"]
    check_module(cfg["vib"], args.sim)
    temp = temp_info = None
    if temp_enabled(cfg) and not args.no_temp:
        check_module(cfg["temp"], args.sim)
        temp = TempLogger(cfg, args.sim)
        temp_info = device_info(device_name(cfg["temp"], args.sim))

    with build_vib_task(cfg, args.sim) as task:
        rate = task.timing.samp_clk_rate
        chunk = max(1, int(rate * 0.1))
        scale = np.array([volts_to_units(c) for c in chans])[:, None]
        reader = AnalogMultiChannelReader(task.in_stream)
        w = RunWriter(args.out, cfg, rate, device_info(device_name(cfg["vib"], args.sim)),
                      fault=args.fault, freq=args.freq, duration=args.duration, load=args.load,
                      notes=args.notes, operator=args.operator, settle=args.settle, simulated=args.sim,
                      source="acquire.py", temp=temp, temp_info=temp_info)

        task.start()
        n_settle = int(round(args.settle * rate))
        while n_settle > 0:  # let IEPE bias / AC coupling settle; discard
            n = min(chunk, n_settle)
            reader.read_many_sample(np.empty((len(chans), n)), n, timeout=10.0)
            n_settle -= n
        w.begin()
        print(f"Recording {args.duration:g} s at {rate:g} S/s -> {w.path}")

        completed, error = False, None
        try:
            while not w.done:
                n = min(chunk, w.n_total - w.got)
                b = np.empty((len(chans), n))
                reader.read_many_sample(b, n, timeout=10.0)
                w.write(b * scale)
                if w.got % int(rate) < n:
                    print(f"\r  {w.elapsed_s:6.1f} / {args.duration:g} s", end="", flush=True)
            completed = True
        except KeyboardInterrupt:
            print("\nStopped by user; saving partial run.")
        except nidaqmx.DaqError as e:
            error = e
            raise
        finally:
            task.stop()
            print()
            summary = w.close(completed, error)

    for ch in chans:
        print(f"  {ch['name']:<18} RMS(ac) {summary['rms'][ch['name']]:8.4f} {channel_units(ch)}")
    if temp and len(temp.values):
        print(f"  temperature        {np.asarray(temp.values)[-1].round(2).tolist()} °C")
    if summary["temp_warning"]:
        print(f"WARNING: {summary['temp_warning']}")
    print(f"Saved {w.path}")


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
