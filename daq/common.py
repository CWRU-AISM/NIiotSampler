"""Shared helpers: config loading and DAQmx task construction for the IM fault rig."""

import threading
import time
import tomllib
from pathlib import Path

import numpy as np
import nidaqmx
from nidaqmx.constants import (
    AcquisitionType,
    CJCSource,
    Coupling,
    ExcitationSource,
    TemperatureUnits,
    ThermocoupleType,
)
from nidaqmx.system import Device, System

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_DIR / "daq" / "config.toml"


def load_config(path=DEFAULT_CONFIG):
    path = Path(path)
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    cfg["_path"] = str(path)
    cfg["_text"] = path.read_text(encoding="utf-8")
    return cfg


def device_name(section, sim):
    return section["sim_device"] if sim else section["device"]


def temp_enabled(cfg):
    """[temp] can be switched off with `enabled = false` (no thermocouple chassis connected)."""
    return "temp" in cfg and cfg["temp"].get("enabled", True)


def device_info(name):
    """Product type / serial / chassis info for a module or chassis, for logging."""
    dev = Device(name)
    info = {"name": name, "product_type": dev.product_type, "simulated": dev.is_simulated}
    try:
        info["serial"] = f"{dev.serial_num:X}"
    except nidaqmx.DaqError:
        pass
    try:
        chassis = dev.compact_daq_chassis_device
        info["chassis"] = chassis.name
        info["chassis_product_type"] = chassis.product_type
        info["chassis_serial"] = f"{chassis.serial_num:X}"
    except nidaqmx.DaqError:
        pass
    return info


def driver_version():
    v = System.local().driver_version
    return f"{v.major_version}.{v.minor_version}.{v.update_version}"


def _try_set(chan, attr, value):
    """Set a channel property; skip quietly if this module doesn't support it."""
    try:
        setattr(chan, attr, value)
    except nidaqmx.DaqError as e:
        print(f"  note: {chan.name}.{attr} not set ({e.error_code})")


def add_vib_channels(task, cfg, sim=False):
    """Accelerometer (IEPE) + current clamp (plain voltage) on the BNC module."""
    dev = device_name(cfg["vib"], sim)
    for ch in cfg["vib"]["channels"]:
        phys = f"{dev}/{ch['physical']}"
        if ch["kind"] == "accel":
            c = task.ai_channels.add_ai_accel_chan(
                phys,
                name_to_assign_to_channel=ch["name"],
                min_val=-ch["range_g"],
                max_val=ch["range_g"],
                sensitivity=ch["sensitivity_mV_per_g"],
                current_excit_source=ExcitationSource.INTERNAL,
                current_excit_val=ch["iepe_current_A"],
            )
        elif ch["kind"] == "current_clamp":
            # Read volts with IEPE off; converted to amps in software (volts_to_units()).
            c = task.ai_channels.add_ai_voltage_chan(
                phys,
                name_to_assign_to_channel=ch["name"],
                min_val=-ch["range_V"],
                max_val=ch["range_V"],
            )
            _try_set(c, "ai_excit_src", ExcitationSource.NONE)
        else:
            raise ValueError(f"unknown channel kind {ch['kind']!r}")
        _try_set(c, "ai_coupling", Coupling[ch["coupling"]])


def volts_to_units(ch):
    """Scale factor applied in software (1.0 for channels DAQmx already scales)."""
    if ch["kind"] == "current_clamp":
        return 1000.0 / ch["sensitivity_mV_per_A"]
    return 1.0


def channel_units(ch):
    return {"accel": "g", "current_clamp": "A"}[ch["kind"]]


def build_vib_task(cfg, sim=False, buffer_s=10.0):
    task = nidaqmx.Task("vib")
    try:
        add_vib_channels(task, cfg, sim)
        rate = cfg["vib"]["sample_rate"]
        task.timing.cfg_samp_clk_timing(rate, sample_mode=AcquisitionType.CONTINUOUS)
        task.in_stream.input_buf_size = int(rate * buffer_s)
    except Exception:
        task.close()
        raise
    return task


def build_temp_task(cfg, sim=False):
    """On-demand (software-timed) thermocouple task; works on NI 9211/9212/9213."""
    task = nidaqmx.Task("temp")
    try:
        dev = device_name(cfg["temp"], sim)
        for ch in cfg["temp"]["channels"]:
            task.ai_channels.add_ai_thrmcpl_chan(
                f"{dev}/{ch['physical']}",
                name_to_assign_to_channel=ch["name"],
                min_val=ch["min_C"],
                max_val=ch["max_C"],
                units=TemperatureUnits.DEG_C,
                thermocouple_type=ThermocoupleType[ch["tc_type"]],
                cjc_source=CJCSource.BUILT_IN,
            )
    except Exception:
        task.close()
        raise
    return task


def check_module(section, sim):
    """Warn if the module in the chassis is not the type the config assumes."""
    name = device_name(section, sim)
    actual = Device(name).product_type
    expected = section.get("expected_module")
    if expected and actual != expected:
        print(f"  WARNING: {name} is a {actual}, config assumes {expected} — check channel settings")
    return actual


class TempLogger(threading.Thread):
    """Polls the thermocouple task at cfg['temp']['rate_hz'] until stop()."""

    def __init__(self, cfg, sim):
        super().__init__(daemon=True)
        self.task = build_temp_task(cfg, sim)
        self.period = 1.0 / cfg["temp"]["rate_hz"]
        self._stop_evt = threading.Event()
        self.t, self.values, self.error = [], [], None

    def run(self):
        try:
            next_t = time.time()
            while not self._stop_evt.is_set():
                v = np.atleast_1d(self.task.read())
                self.t.append(time.time())
                self.values.append(v)
                next_t += self.period
                self._stop_evt.wait(max(0.0, next_t - time.time()))
        except nidaqmx.DaqError as e:
            self.error = e
        finally:
            self.task.close()

    def stop(self):
        self._stop_evt.set()
        if self.ident is None:  # never started
            self.task.close()
        else:
            self.join(timeout=10)
