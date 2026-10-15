"""Live view of the vibration/current channels (waveform + spectrum) and temperature.
Use it on site to check every sensor before recording (stands in for NI MAX's Test Panel).

  python daq/scope.py [--sim] [--window 1.0] [--fmax 2000]
"""

import argparse
import sys

import matplotlib.pyplot as plt
import numpy as np
import nidaqmx
from matplotlib.animation import FuncAnimation
from nidaqmx.stream_readers import AnalogMultiChannelReader

from common import (
    DEFAULT_CONFIG,
    TempLogger,
    build_vib_task,
    channel_units,
    load_config,
    temp_enabled,
    volts_to_units,
)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sim", action="store_true", help="use simulated devices")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--window", type=float, default=1.0, help="seconds shown / used for the FFT")
    p.add_argument("--fmax", type=float, default=2000.0, help="spectrum x-axis limit [Hz]")
    p.add_argument("--no-temp", action="store_true")
    args = p.parse_args()

    cfg = load_config(args.config)
    chans = cfg["vib"]["channels"]
    task = build_vib_task(cfg, args.sim)
    rate = task.timing.samp_clk_rate
    n_win = int(rate * args.window)
    nch = len(chans)
    reader = AnalogMultiChannelReader(task.in_stream)
    scale = np.array([volts_to_units(c) for c in chans])[:, None]
    hist = np.zeros((nch, n_win))
    temp = TempLogger(cfg, args.sim) if temp_enabled(cfg) and not args.no_temp else None

    t = np.arange(n_win) / rate
    f = np.fft.rfftfreq(n_win, 1.0 / rate)
    win = np.hanning(n_win)
    band = f >= 2.0
    fig, axes = plt.subplots(nch, 2, figsize=(13, 3.2 * nch), squeeze=False)
    lines_t, lines_f, peaks = [], [], []
    for i, ch in enumerate(chans):
        u = channel_units(ch)
        (lt,) = axes[i, 0].plot(t, hist[i], lw=0.6)
        axes[i, 0].set(xlabel="time [s]", ylabel=f"{ch['name']} [{u}]")
        (lf,) = axes[i, 1].semilogy(f, np.ones_like(f), lw=0.6)
        axes[i, 1].set(xlim=(0, args.fmax), xlabel="frequency [Hz]", ylabel=f"amplitude [{u}]")
        peaks.append(axes[i, 1].text(0.98, 0.92, "", transform=axes[i, 1].transAxes, ha="right"))
        lines_t.append(lt)
        lines_f.append(lf)
    fig.tight_layout(rect=(0, 0, 1, 0.95))

    def update(_frame):
        nonlocal hist
        avail = task.in_stream.avail_samp_per_chan
        if avail == 0:
            return
        buf = np.empty((nch, avail))
        reader.read_many_sample(buf, avail, timeout=5.0)
        hist = np.concatenate([hist, buf * scale], axis=1)[:, -n_win:]
        for i in range(nch):
            x = hist[i]
            lines_t[i].set_ydata(x)
            lo, hi = x.min(), x.max()
            pad = 0.1 * (hi - lo) or 1e-3
            axes[i, 0].set_ylim(lo - pad, hi + pad)
            spec = 2.0 * np.abs(np.fft.rfft((x - x.mean()) * win)) / win.sum()
            lines_f[i].set_ydata(spec + 1e-12)
            axes[i, 1].set_ylim(max(spec[band].max() * 1e-5, 1e-9), spec[band].max() * 3 + 1e-12)
            k = np.argmax(spec[band])
            peaks[i].set_text(f"peak {f[band][k]:.2f} Hz   RMS(ac) {np.std(x):.4g}")
        t_txt = "--"
        if temp and temp.values:
            t_txt = ", ".join(f"{c['name']} {v:.1f} °C" for c, v in zip(cfg["temp"]["channels"], temp.values[-1]))
        elif temp and temp.error:
            t_txt = f"error {temp.error.error_code}"
        fig.suptitle(f"{rate:g} S/s    temperature: {t_txt}" + ("    [SIMULATED]" if args.sim else ""))

    try:
        task.start()
        if temp:
            temp.start()
        _ani = FuncAnimation(fig, update, interval=200, cache_frame_data=False)
        plt.show()
    except nidaqmx.DaqError as e:
        print(f"DAQmx error {e.error_code}: {e}", file=sys.stderr)
        return 1
    finally:
        if temp:
            temp.stop()
        task.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
