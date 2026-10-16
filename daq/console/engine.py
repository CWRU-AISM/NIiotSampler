"""Acquisition engine: one background thread streams the DAQ (or a demo signal) into a ring buffer.

Recording taps the same stream (RunWriter), so what the page shows is what gets saved.
On a DAQmx error the source is closed and reopened every few seconds until it works again.
"""

import threading
import time
import traceback

import numpy as np
import nidaqmx
from nidaqmx.stream_readers import AnalogMultiChannelReader

from common import (
    TempLogger,
    build_vib_task,
    channel_units,
    device_info,
    device_name,
    temp_enabled,
    volts_to_units,
)
from recorder import RunWriter

RING_S = 40.0  # seconds kept in memory for the live view / averaging / live checks

ERROR_HINTS = {
    -201420: ("The chassis is reserved by another computer.", "reserve"),
    -201401: ("Lost the network connection to the chassis.", None),
    -201003: ("The device is not reachable: check the Ethernet cable / power and that the PC has a "
              "169.254.x.x address.", None),
    -50103: ("The module is in use by another task: close scope.py / acquire.py / NI MAX test panels.", None),
    -200279: ("The PC fell behind the DAQ (buffer overflow). The stream was restarted.", None),
    -200284: ("Read timed out: the chassis stopped delivering samples.", None),
}


class DaqSource:
    def __init__(self, cfg, sim=False):
        self.cfg, self.sim = cfg, sim
        name = device_name(cfg["vib"], sim)
        self.info = device_info(name)
        expected = cfg["vib"].get("expected_module")
        self.module_warning = None
        if expected and self.info["product_type"] != expected:
            self.module_warning = f"{name} is a {self.info['product_type']}, config assumes {expected}"
        self.task = build_vib_task(cfg, sim)
        self.rate = float(self.task.timing.samp_clk_rate)
        self.scale = np.array([volts_to_units(c) for c in cfg["vib"]["channels"]])[:, None]
        self.reader = AnalogMultiChannelReader(self.task.in_stream)
        self.task.start()

    def read(self, n):
        buf = np.empty((len(self.scale), n))
        self.reader.read_many_sample(buf, n, timeout=10.0)
        return buf * self.scale

    def close(self):
        try:
            self.task.close()
        except Exception:
            pass


class DemoSource:
    """Synthetic 2-pole motor on a VFD: current at drive Hz (+3rd/5th), accel 1x/2x + a 3 kHz resonance."""

    def __init__(self, cfg, drive_hz=21.25, slip=0.027):
        self.cfg = cfg
        self.load = 0.0
        self.rate = float(cfg["vib"]["sample_rate"])
        self.drive_hz, self.slip = drive_hz, slip
        self.info = {"name": "demo", "product_type": "demo (synthetic)", "simulated": True}
        self.module_warning = None
        self.n = 0
        self.t_start = None
        self.rng = np.random.default_rng(0)
        self.res_state = np.zeros(2)

    def read(self, n):
        # pace like real hardware; if we fell behind (startup, debugger), drop the backlog
        now = time.time()
        if self.t_start is None or now - (self.t_start + self.n / self.rate) > 0.5:
            self.t_start = now - self.n / self.rate
        due = self.t_start + (self.n + n) / self.rate
        wait = due - time.time()
        if wait > 0:
            time.sleep(wait)
        t = (self.n + np.arange(n)) / self.rate
        self.n += n
        fe = self.drive_hz
        slip = self.slip + 0.006 * self.load
        amp = 2.4 * (1 + 0.15 * self.load)
        fr = fe * (1 - slip)
        out = []
        for ch in self.cfg["vib"]["channels"]:
            if ch["kind"] == "current_clamp":
                x = (amp * np.sin(2 * np.pi * fe * t) + 0.08 * np.sin(2 * np.pi * 3 * fe * t + 0.3)
                     + 0.03 * np.sin(2 * np.pi * 5 * fe * t) + 0.01 * self.rng.standard_normal(n))
            else:
                x = (0.02 * np.sin(2 * np.pi * fr * t) + 0.004 * np.sin(2 * np.pi * 2 * fr * t + 1.0)
                     + 0.11 * np.sin(2 * np.pi * 3043 * t) * (1 + 0.3 * np.sin(2 * np.pi * fr * t))
                     + 0.01 * self.rng.standard_normal(n))
            out.append(x)
        return np.array(out)

    def close(self):
        pass


class Engine:
    def __init__(self, cfg, data_dir, mode="daq"):
        self.cfg, self.data_dir, self.mode = cfg, data_dir, mode  # mode: daq | sim | demo
        self.lock = threading.RLock()
        self.status, self.error, self.error_code = "starting", None, None
        self.info, self.module_warning, self.rate = {}, None, float(cfg["vib"]["sample_rate"])
        self.ring = None
        self.total = 0           # samples since this stream started
        self.stream_t0 = None
        self.writer = None
        self.rec_params = None
        self.rec_stop = False
        self.rec_discard = False
        self.rec_settle_n = 0
        self.last_run = None     # summary of the most recent recording (+ report when ready)
        self.on_saved = None     # callback(summary) -> report, run in a worker thread
        self.on_discard = None   # callback(rel_path) -> where the discarded file went
        self.demo_drive_hz = 21.25
        self.demo_load = 0.0
        self.user_stopped = False  # live stream switched off from the page (DAQ task closed, buffer freed)
        self._restart = threading.Event()
        self._paused = threading.Event()
        self._idle = threading.Event()
        self._quit = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="engine")

    # ---------------------------------------------------------------- public API
    def start(self):
        self.thread.start()

    def shutdown(self):
        self._quit.set()
        self._restart.set()
        self.thread.join(timeout=15)

    @property
    def chans(self):
        return self.cfg["vib"]["channels"]

    def snapshot(self, seconds):
        """Last `seconds` of data, (n_chan, n) in engineering units, oldest first."""
        with self.lock:
            if self.ring is None or self.total == 0:
                return np.zeros((len(self.chans), 0))
            size = self.ring.shape[1]
            n = min(int(seconds * self.rate), self.total, size)
            end = self.total % size
            idx = np.arange(end - n, end) % size
            return self.ring[:, idx].copy()

    def apply_config(self, cfg):
        with self.lock:
            if self.writer:
                raise RuntimeError("cannot change the configuration while recording")
            self.cfg = cfg
        self.restart()

    def restart(self):
        self._restart.set()

    def pause(self):
        """Close the source (for reserve / self-test) and wait until it is closed."""
        with self.lock:
            if self.writer:
                raise RuntimeError("busy recording")
        self._idle.clear()
        self._paused.set()
        self._restart.set()
        self._idle.wait(timeout=15)

    def resume(self):
        """Reopen the source after pause() - unless the user has switched the stream off."""
        if self.user_stopped:
            return
        self._paused.clear()
        self._restart.set()

    def stop_stream(self):
        """User 'Stop stream': close the DAQ task and free the ring buffer until start_stream()."""
        self.pause()  # raises while recording
        self.user_stopped = True
        with self.lock:
            self.ring, self.total, self.stream_t0 = None, 0, None
            self.status = "stopped"

    def start_stream(self):
        self.user_stopped = False
        self.resume()

    def wait_running(self, timeout=15.0):
        t_end = time.time() + timeout
        while time.time() < t_end:
            if self.status == "running":
                return True
            if self.status == "error":
                return False
            time.sleep(0.1)
        return False

    def start_recording(self, p):
        """p: fault, freq, duration, load, notes, operator, settle, tag. Returns the file path."""
        with self.lock:
            if self.writer:
                raise RuntimeError("already recording")
            if self.status != "running":
                raise RuntimeError(f"stream is not running ({self.status})")
            temp = temp_info = None
            if temp_enabled(self.cfg) and self.mode != "demo":
                temp = TempLogger(self.cfg, self.mode == "sim")
                temp_info = device_info(device_name(self.cfg["temp"], self.mode == "sim"))
            w = RunWriter(self.data_dir, self.cfg, self.rate, self.info,
                          fault=p["fault"], freq=p["freq"], duration=p["duration"], load=p.get("load", "none"),
                          notes=p.get("notes", ""), operator=p.get("operator", ""), settle=p.get("settle", 2.0),
                          simulated=self.mode != "daq", tag=p.get("tag", ""), source=f"console-{self.mode}",
                          temp=temp, temp_info=temp_info, extra_attrs=p.get("attrs"))
            # settle counts from the stream start; a long-running stream is already settled
            self.rec_settle_n = int(p.get("settle", 2.0) * self.rate)
            self.writer, self.rec_params, self.rec_stop, self.rec_discard = w, dict(p), False, False
            self.last_run = None
            return w.rel_path

    def stop_recording(self, discard=False):
        """Stop early (keeps a partial file), or with discard=True stop and delete the run."""
        with self.lock:
            self.rec_stop = True
            self.rec_discard = discard

    def recording_state(self):
        with self.lock:
            w = self.writer
            if not w:
                return None
            return {"file": w.rel_path, "elapsed_s": w.elapsed_s, "duration_s": w.n_total / w.rate,
                    "settling": w.got == 0 and self.total < self.rec_settle_n, **self.rec_params}

    # ---------------------------------------------------------------- thread
    def _open(self):
        if self.mode == "demo":
            src = DemoSource(self.cfg, self.demo_drive_hz)
        else:
            src = DaqSource(self.cfg, sim=self.mode == "sim")
        return src

    def _run(self):
        while not self._quit.is_set():
            if self._paused.is_set():
                self.status = "stopped" if self.user_stopped else "paused"
                self._idle.set()
                self._restart.wait()
                self._restart.clear()
                continue
            self._restart.clear()
            src = None
            try:
                self.status, self.error, self.error_code = "connecting", None, None
                src = self._open()
                with self.lock:
                    self.info, self.module_warning, self.rate = src.info, src.module_warning, src.rate
                    self.ring = np.zeros((len(self.chans), int(RING_S * self.rate)))
                    self.total = 0
                    self.stream_t0 = time.time()
                self.status = "running"
                chunk = max(1, int(self.rate * 0.1))
                while not self._restart.is_set():
                    if isinstance(src, DemoSource):
                        src.drive_hz, src.load = self.demo_drive_hz, self.demo_load
                    block = src.read(chunk)
                    self._push(block)
                self._finish_recording(error="stream restarted")
            except Exception as e:  # DAQmx errors and anything else: report, retry
                code = getattr(e, "error_code", None)
                with self.lock:
                    self.status, self.error, self.error_code = "error", str(e).strip(), code
                if not isinstance(e, nidaqmx.DaqError):
                    traceback.print_exc()
                self._finish_recording(error=e)
            finally:
                if src:
                    src.close()
            if self.status == "error":
                self._restart.wait(timeout=3.0)  # retry soon, or immediately on restart()

    def _push(self, block):
        n = block.shape[1]
        with self.lock:
            size = self.ring.shape[1]
            start = self.total % size
            first = min(n, size - start)
            self.ring[:, start:start + first] = block[:, :first]
            if first < n:
                self.ring[:, :n - first] = block[:, first:]
            prev = self.total
            self.total += n
            w = self.writer
            if not w:
                return
            skip = max(0, self.rec_settle_n - prev)  # still settling
            if skip < n:
                w.write(block[:, skip:])
            if w.done or self.rec_stop:
                self._finish_recording()

    def _finish_recording(self, error=None):
        with self.lock:
            w, self.writer = self.writer, None
            if not w:
                return
            try:
                summary = w.close(completed=w.done, error=error if not w.done else None)
            except Exception as e:  # file problems must not kill the stream
                summary = {"file": w.rel_path, "error": f"saving failed: {e}", "completed": False}
            summary["report"] = None
            if self.rec_discard and self.on_discard and "path" in summary:
                try:
                    summary = {"file": w.rel_path, "discarded": True, "trash": self.on_discard(w.rel_path)}
                except Exception as e:
                    summary["error"] = f"discard failed: {e}"
            self.last_run = summary
        if self.on_saved and "path" in summary:
            threading.Thread(target=self._post_check, args=(summary,), daemon=True).start()

    def _post_check(self, summary):
        try:
            summary["report"] = self.on_saved(summary)
        except Exception as e:
            summary["report"] = {"error": str(e)}

    def error_hint(self):
        hint, action = ERROR_HINTS.get(self.error_code, (None, None))
        return hint, action

    def channel_list(self):
        from checks import channel_range
        return [{"name": c["name"], "kind": c["kind"], "units": channel_units(c), "physical": c["physical"],
                 "range": channel_range(c), "location": c.get("location", "")} for c in self.chans]
