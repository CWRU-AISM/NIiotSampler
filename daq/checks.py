"""Signal-nature checks: is each channel carrying the signal it should? Used live and on saved runs.

  from checks import run_checks
  report = run_checks({"accel_de_radial": x, "motor_current": i}, cfg, rate, drive_hz=21.25)

Each check is {"id", "channel", "status": pass|warn|fail|info, "title", "detail"}.
Physics used (2-pole motor on a VFD): current fundamental = drive Hz; shaft 1x = current Hz / pole pairs
x (1 - slip), slip a few %; 60 Hz dominating while the drive is elsewhere means pickup / ground loop.
"""

import numpy as np

from common import volts_to_units

NOISE_FLOOR = {"accel": 0.003, "current_clamp": 0.03}  # RMS(ac) below this = no signal [g, A]
DEFAULT_MOTOR = {"poles": 2, "current_rms_A": [0.3, 4.0], "line_hz": 60.0}


# ----------------------------------------------------------------------------- spectra
def amp_spectrum(x, rate, seg_s=None):
    """Hann-windowed amplitude spectrum (a sine of amplitude A shows as A).

    With seg_s and a signal longer than one segment: Welch average of 50 %-overlapped segments.
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    seg = n if not seg_s else min(n, max(16, int(seg_s * rate)))
    win = np.hanning(seg)
    norm = 2.0 / win.sum()
    if seg >= n:
        a = np.abs(np.fft.rfft((x - x.mean()) * win)) * norm
    else:
        step = seg // 2
        starts = range(0, n - seg + 1, step)
        p = np.zeros(seg // 2 + 1)
        for s in starts:
            y = x[s:s + seg]
            p += np.abs(np.fft.rfft((y - y.mean()) * win)) ** 2
        a = np.sqrt(p / len(starts)) * norm
    return np.fft.rfftfreq(seg, 1.0 / rate), a


def peak_in(f, a, lo, hi):
    """Largest bin in [lo, hi], frequency refined by parabolic interpolation. Returns (Hz, amp) or (nan, 0)."""
    idx = np.nonzero((f >= lo) & (f <= hi))[0]
    if len(idx) == 0:
        return float("nan"), 0.0
    k = idx[np.argmax(a[idx])]
    fk, ak = f[k], a[k]
    if 0 < k < len(a) - 1:
        y0, y1, y2 = np.log(a[k - 1] + 1e-30), np.log(a[k] + 1e-30), np.log(a[k + 1] + 1e-30)
        den = y0 - 2 * y1 + y2
        if den < 0:
            d = 0.5 * (y0 - y2) / den
            fk = f[k] + d * (f[1] - f[0])
    return float(fk), float(ak)


def amp_at(f, a, f0, half_width):
    """Largest amplitude within f0 ± half_width."""
    m = (f >= f0 - half_width) & (f <= f0 + half_width)
    return float(a[m].max()) if m.any() else 0.0


def channel_range(ch):
    """Full-scale input range in engineering units."""
    if ch["kind"] == "accel":
        return float(ch["range_g"])
    return float(ch["range_V"]) * volts_to_units(ch)


def _longest_constant_run(x):
    if len(x) < 2:
        return 0
    change = np.nonzero(np.diff(x) != 0)[0]
    if len(change) == 0:
        return len(x)
    edges = np.concatenate([[-1], change, [len(x) - 1]])
    return int(np.diff(edges).max())


# ----------------------------------------------------------------------------- checks
def _c(out, cid, ch, status, title, detail=""):
    out.append({"id": cid, "channel": ch, "status": status, "title": title, "detail": detail})


def frequency_track(x, rate, win_s=2.0, hop_s=0.5, lo=2.0, hi=150.0):
    """Current fundamental over time: (t_center [s], f [Hz], rms) per window."""
    n, h = int(win_s * rate), int(hop_s * rate)
    out_t, out_f, out_r = [], [], []
    for s in range(0, max(0, len(x) - n) + 1, h):
        y = x[s:s + n]
        f, a = amp_spectrum(y, rate)
        fk, _ = peak_in(f, a, lo, hi)
        out_t.append((s + n / 2) / rate)
        out_f.append(fk)
        out_r.append(float(np.std(y)))
    return np.array(out_t), np.array(out_f), np.array(out_r)


def check_schedule(out, name, x, rate, schedule, step_s):
    """Sweep: did each step reach its target speed? Uses the last half of every step (after the ramp)."""
    t, fr, rms = frequency_track(x, rate)
    if len(t) == 0:
        _c(out, "sweep", name, "warn", "record too short to follow the schedule")
        return
    hit, missed, rms_steps = 0, [], []
    for i, st in enumerate(schedule):
        t0 = float(st["t"]) + 0.5 * step_s
        t1 = float(st["t"]) + step_s
        m = (t - 1.0 >= t0 - 1e-9) & (t + 1.0 <= t1 + 1e-9)  # whole 2 s window inside the step's second half
        if not m.any():
            missed.append(f"{st['speed_hz']:g} Hz (no data)")
            continue
        f_med = float(np.median(fr[m]))
        rms_steps.append(float(np.median(rms[m])))
        target = float(st["speed_hz"])
        if abs(f_med - target) <= max(0.5, 0.03 * target):
            hit += 1
        else:
            missed.append(f"step {i + 1}: {target:g} Hz (measured {f_med:.1f})")
    status = "pass" if not missed else ("warn" if hit >= len(schedule) / 2 else "fail")
    _c(out, "sweep", name, status, f"schedule followed: {hit}/{len(schedule)} steps at the target speed",
       "; ".join(missed[:4]) if missed else f"speed track {np.nanmin(fr):.1f}-{np.nanmax(fr):.1f} Hz")
    if rms_steps:
        _c(out, "sweep_rms", name, "info", "current RMS per step: " + ", ".join(f"{r:.2f}" for r in rms_steps) + " A")


def run_checks(data, cfg, rate, drive_hz=None, meta=None, seg_s=None, schedule=None, step_s=None):
    """data: {channel name: samples in engineering units}. meta: file attrs for integrity checks (file mode).

    seg_s: Welch segment length for long records (None = one FFT over everything).
    schedule: for sweep records, [{"t", "speed_hz", "load"}, ...]; replaces the steady drive / shaft checks.
    Returns {"checks": [...], "derived": {...}, "summary": worst status}.
    """
    motor = DEFAULT_MOTOR | cfg.get("motor", {})
    pp = max(1, int(motor["poles"]) // 2)
    line = float(motor["line_hz"])
    chans = [c for c in cfg["vib"]["channels"] if c["name"] in data]
    drive_hz = float(drive_hz) if drive_hz else None
    out, derived, spectra, alive = [], {}, {}, {}

    # --- generic per-channel health
    for ch in chans:
        name, kind = ch["name"], ch["kind"]
        x = np.asarray(data[name], dtype=float)
        units = "g" if kind == "accel" else "A"
        if len(x) < 16:
            _c(out, "alive", name, "fail", "no samples")
            alive[name] = False
            continue
        if not np.all(np.isfinite(x)):
            _c(out, "finite", name, "fail", "NaN / Inf in data", f"{np.count_nonzero(~np.isfinite(x))} bad samples")
            x = np.nan_to_num(x)
        mean, rms = float(x.mean()), float(np.std(x))
        peak = float(np.abs(x).max())
        full = channel_range(ch)
        floor = NOISE_FLOOR.get(kind, 0.0)
        alive[name] = rms >= floor
        if alive[name]:
            _c(out, "alive", name, "pass", f"signal present (RMS {rms:.4g} {units})")
        else:
            _c(out, "alive", name, "fail", f"no signal (RMS {rms:.2g} {units} < {floor:g})",
               "motor off, cable unplugged, or sensor on a different input")
        frac = peak / full if full else 0.0
        if frac >= 0.98:
            _c(out, "clipping", name, "fail", f"clipping: peak {peak:.4g} {units} = {frac:.0%} of range",
               "raise the range in the sampling config")
        elif frac >= 0.9:
            _c(out, "clipping", name, "warn", f"near full scale: {frac:.0%} of range")
        else:
            _c(out, "clipping", name, "pass", f"peak uses {frac:.0%} of ±{full:g} {units}")
        run = _longest_constant_run(x)
        if run > max(32, 0.05 * rate):
            _c(out, "flatline", name, "fail", f"stuck value for {run / rate:.2f} s", "dropout or disconnected input")
        if kind == "accel" and ch.get("coupling", "AC") == "AC" and alive[name]:
            if abs(mean) > max(0.01, 0.5 * rms):
                _c(out, "dc_offset", name, "warn", f"DC offset {mean:+.3g} g on an AC-coupled input",
                   "IEPE still settling or sensor/cable fault")
        spectra[name] = amp_spectrum(x, rate, seg_s)
        derived[name] = {"mean": mean, "rms": rms, "peak": peak, "crest": peak / rms if rms else 0.0,
                         "range_used": frac}

    cur = next((c for c in chans if c["kind"] == "current_clamp"), None)
    accels = [c for c in chans if c["kind"] == "accel"]

    # --- current: drive frequency, level, line pickup
    current_hz = None
    if cur and alive.get(cur["name"]):
        name = cur["name"]
        f, a = spectra[name]
        fh, ah = peak_in(f, a, 2.0, 150.0)
        current_hz = fh
        derived["current_hz"] = fh
        if schedule:
            check_schedule(out, name, np.asarray(data[name], dtype=float), rate, schedule,
                           step_s or (float(schedule[1]["t"]) - float(schedule[0]["t"]) if len(schedule) > 1 else 10.0))
        elif drive_hz:
            tol = max(0.3, 0.02 * drive_hz)
            if abs(fh - drive_hz) <= tol:
                _c(out, "drive_freq", name, "pass", f"fundamental {fh:.2f} Hz = drive {drive_hz:g} Hz")
            else:
                _c(out, "drive_freq", name, "fail", f"fundamental {fh:.2f} Hz, drive set to {drive_hz:g} Hz",
                   "wrong drive Hz entered, or this is not the motor current")
        else:
            _c(out, "drive_freq", name, "info", f"fundamental {fh:.2f} Hz (no drive Hz entered)")
        rms = derived[name]["rms"]
        lo, hi = motor["current_rms_A"]
        if lo <= rms <= hi:
            _c(out, "current_level", name, "pass", f"RMS {rms:.3g} A within {lo:g}-{hi:g} A")
        else:
            hint = ""
            if lo <= rms * 10 <= hi:
                hint = "about 10x too small: check the clamp mV/A setting"
            elif lo <= rms / 10 <= hi:
                hint = "about 10x too large: check the clamp mV/A setting"
            _c(out, "current_level", name, "warn", f"RMS {rms:.3g} A outside {lo:g}-{hi:g} A", hint)
        if abs(fh - line) < 0.5 and not (drive_hz and abs(drive_hz - line) < 2):
            _c(out, "line_pickup", name, "warn", f"{line:g} Hz dominates the current",
               "mains pickup: is the clamp on a motor lead (after the VFD)?")
        f3 = amp_at(f, a, 3 * fh, 0.5)
        if ah > 0:
            _c(out, "harmonics", name, "info", f"3rd harmonic {f3 / ah:.1%} of fundamental ({ah:.3g} A peak)")

    # --- accelerometers: shaft 1x, slip, line pickup, hints (steady records only)
    if schedule:
        accels = []
        if not (cur and alive.get(cur["name"])):
            _c(out, "sweep", "file", "warn", "no current signal: cannot verify the sweep schedule")
    base = current_hz or drive_hz
    for ch in accels:
        name = ch["name"]
        if not alive.get(name):
            continue
        f, a = spectra[name]
        fd, ad = peak_in(f, a, 2.0, f[-1])
        derived[name]["dominant_hz"] = fd
        _c(out, "dominant", name, "info", f"strongest component {fd:.1f} Hz ({ad:.3g} g)")
        lf, la = peak_in(f, a, 5.0, 200.0)
        if abs(lf - line) < 0.5 or abs(lf - 2 * line) < 0.5:
            near = base and min(abs(base - line), abs(2 * base / pp - 2 * line)) < 2
            if not near:
                _c(out, "line_pickup", name, "warn", f"{lf:.1f} Hz (line) is the strongest low-frequency peak",
                   "ground loop or cable pickup")
        if not base:
            _c(out, "shaft_1x", name, "info", "enter the drive Hz to check the shaft 1x")
            continue
        sync = base / pp
        df = f[1] - f[0]
        f1, a1 = peak_in(f, a, sync * 0.92, sync - 0.5 * df)
        ref = a[(f >= sync * 0.5) & (f <= sync * 1.5)]
        prom = a1 / (np.median(ref) + 1e-15) if len(ref) else 0.0
        derived["shaft_hz"] = f1
        if prom >= 4:
            slip = 1.0 - f1 / sync
            derived["slip"] = slip
            src = "measured current" if current_hz else "entered drive Hz"
            if 0.001 <= slip <= 0.07:
                _c(out, "shaft_1x", name, "pass",
                   f"shaft 1x {f1:.2f} Hz ({f1 * 60:.0f} rpm), slip {slip:.1%}",
                   f"sync {sync:.2f} Hz from {src}; 1x {a1:.3g} g, {prom:.0f}x above local floor")
            else:
                _c(out, "shaft_1x", name, "warn", f"1x candidate {f1:.2f} Hz gives slip {slip:.1%}",
                   "outside 0.1-7 %: check drive Hz / pole count")
            a2 = amp_at(f, a, 2 * f1, 2 * df)
            _c(out, "hint_2x", name, "info", f"2x/1x = {a2 / a1:.2f}" + ("  (high: misalignment?)" if a2 > a1 else ""))
        else:
            _c(out, "shaft_1x", name, "warn", f"no clear 1x peak below {sync:.2f} Hz",
               "sensor not on the motor, motor stopped, or wrong drive Hz")

    # --- broken-bar sidebands (needs fine resolution)
    if cur and current_hz and "slip" in derived and not schedule:
        f, a = spectra[cur["name"]]
        df = f[1] - f[0]
        s = derived["slip"]
        if 2 * s * current_hz > 3 * df:
            a0 = amp_at(f, a, current_hz, df)
            lo_sb = amp_at(f, a, current_hz * (1 - 2 * s), df)
            hi_sb = amp_at(f, a, current_hz * (1 + 2 * s), df)
            db = 20 * np.log10(max(lo_sb, hi_sb, 1e-15) / (a0 + 1e-15))
            _c(out, "hint_bar", cur["name"], "info",
               f"f(1±2s) sidebands {db:.0f} dB below fundamental" + ("  (high: broken bar?)" if db > -40 else ""))

    # --- file integrity
    if meta is not None:
        req, got = meta.get("duration_requested_s"), meta.get("n_samples")
        if meta.get("completed") is False or (meta.get("completed") is None and req):
            _c(out, "integrity", "file", "warn", "run stopped early (completed = False)")
        if req and got is not None:
            exp = int(round(req * rate))
            if got == exp:
                _c(out, "integrity", "file", "pass", f"{got} samples = {req:g} s x {rate:g} S/s")
            else:
                _c(out, "integrity", "file", "warn", f"{got} samples, expected {exp}")
        missing = [k for k in ("fault", "drive_freq_hz", "operator") if not meta.get(k)]
        if missing:
            _c(out, "metadata", "file", "warn", "missing metadata: " + ", ".join(missing))
        else:
            _c(out, "metadata", "file", "pass", "fault, drive Hz, operator recorded")

    order = {"fail": 3, "warn": 2, "pass": 1, "info": 0}
    worst = max((c["status"] for c in out), key=order.get, default="info")
    return {"checks": out, "derived": derived, "summary": worst}
