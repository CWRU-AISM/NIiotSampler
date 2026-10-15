"""Device setup and quick signal check (works on Windows and Linux; on Windows NI MAX does the same).

  python daq/daq_check.py devices              # list chassis/modules DAQmx knows about
  python daq/daq_check.py find                 # look for cDAQ chassis on the network
  python daq/daq_check.py add 169.254.x.y cDAQ1    # add + reserve the chassis as "cDAQ1"
  python daq/daq_check.py reserve cDAQ1 --force    # take over a reservation held by another PC
  python daq/daq_check.py selftest cDAQ1Mod1
  python daq/daq_check.py test [--sim]         # read every configured channel, print stats
"""

import argparse
import subprocess
import sys

import numpy as np
import nidaqmx
from nidaqmx.constants import ProductCategory
from nidaqmx.system import Device, System

from common import (
    DEFAULT_CONFIG,
    build_temp_task,
    build_vib_task,
    channel_units,
    check_module,
    driver_version,
    load_config,
    temp_enabled,
    volts_to_units,
)


def _fmt_dev(dev):
    parts = [f"{dev.name:<20} {dev.product_type:<12}"]
    try:
        parts.append(f"S/N {dev.serial_num:X}")
    except nidaqmx.DaqError:
        pass
    if dev.is_simulated:
        parts.append("(simulated)")
    try:
        ip = dev.tcpip_ethernet_ip
        if ip:
            parts.append(f"IP {ip}")
    except nidaqmx.DaqError:
        pass
    return "  ".join(parts)


def cmd_devices(args):
    print(f"NI-DAQmx {driver_version()}")
    devices = list(System.local().devices)
    if not devices:
        print("No devices. Use `find` then `add <IP> <name>`.")
        return
    shown = set()
    for dev in devices:
        if dev.product_category != ProductCategory.COMPACT_DAQ_CHASSIS:
            continue
        print(_fmt_dev(dev))
        for m in dev.chassis_module_devices:
            shown.add(m.name)
            print(f"    slot {m.compact_daq_slot_num}: {_fmt_dev(m)}")
            print(f"      AI: {', '.join(m.ai_physical_chans.channel_names)}")
        shown.add(dev.name)
    for dev in devices:
        if dev.name not in shown:
            print(_fmt_dev(dev))


def cmd_find(args):
    if sys.platform == "win32":  # nidaqmxconfig is Linux-only
        print("On Windows, search in NI MAX: Devices and Interfaces > right-click Network Devices >\n"
              "Find Network NI-DAQmx Devices. Or add the chassis directly by address or hostname:\n"
              "  python daq/daq_check.py add cDAQ9191-1D92246 cDAQ1\n"
              "  python daq/daq_check.py add 169.254.x.y cDAQ1")
        return 0
    print("Searching for network DAQ devices (broadcast on all interfaces)...", flush=True)
    r = subprocess.run(["nidaqmxconfig", "--find-net-devs"])
    print("\nNothing found? Check the link LED, that the PC has a 169.254.x.x or same-subnet "
          "address on the wired port (ip -br addr), then try:\n"
          "  sudo arp-scan -I enp6s0 --localnet        # DHCP network\n"
          "  sudo arp-scan -I enp6s0 169.254.0.0/16    # link-local (no DHCP); ~1 min\n"
          "and look for MAC 00:80:2f:22:86:48 / 00:80:2f:22:86:5d (NI).")
    return r.returncode


def cmd_add(args):
    dev = Device.add_network_device(args.ip, device_name=args.name,
                                    attempt_reservation=False, timeout=args.timeout)
    print(f"Added {dev.name}")
    # Reserve separately: add_network_device(attempt_reservation=True) can fail silently.
    dev.reserve_network_device(override_reservation=False)
    print(f"Reserved {dev.name}")
    cmd_devices(args)


def cmd_reserve(args):
    Device(args.name).reserve_network_device(override_reservation=args.force)
    print(f"Reserved {args.name}")


def cmd_selftest(args):
    Device(args.name).self_test_device()
    print(f"{args.name}: self-test passed")


def _dominant_freq(x, rate, fmin=2.0):
    x = x - x.mean()
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1.0 / rate)
    band = f >= fmin
    return f[band][np.argmax(spec[band])]


def cmd_test(args):
    cfg = load_config(args.config)
    print("cDAQ #1 (vibration + current)")
    print(f"  module: {check_module(cfg['vib'], args.sim)}")
    with build_vib_task(cfg, args.sim) as task:
        rate = task.timing.samp_clk_rate
        n_settle = int(rate * args.settle)
        n = int(rate * args.seconds)
        raw = task.read(number_of_samples_per_channel=n_settle + n,
                        timeout=args.settle + args.seconds + 10)
    data = np.atleast_2d(np.asarray(raw))[:, n_settle:]
    print(f"  sample rate {rate:g} S/s, {n} samples/channel after {args.settle:g} s settling")
    for ch, x in zip(cfg["vib"]["channels"], data):
        x = x * volts_to_units(ch)
        u = channel_units(ch)
        ac = x - x.mean()
        print(f"  {ch['name']:<18} mean {x.mean():+9.4f} {u}   RMS(ac) {np.sqrt(np.mean(ac**2)):8.4f} {u}"
              f"   peak {np.abs(ac).max():8.4f} {u}   dominant {_dominant_freq(x, rate):7.2f} Hz")
    print("  (expect: current dominant ≈ drive frequency, accel 1x ≈ shaft speed ≈ drive Hz × (1-slip))")

    if not temp_enabled(cfg):
        print("temperature: disabled in config ([temp] enabled = false)")
        return
    print("cDAQ #2 (temperature)")
    print(f"  module: {check_module(cfg['temp'], args.sim)}")
    with build_temp_task(cfg, args.sim) as task:
        vals = np.atleast_1d(task.read())
    for ch, v in zip(cfg["temp"]["channels"], vals):
        print(f"  {ch['name']:<18} {v:7.2f} °C  (type {ch['tc_type']})")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices").set_defaults(func=cmd_devices)
    sub.add_parser("find").set_defaults(func=cmd_find)
    a = sub.add_parser("add")
    a.add_argument("ip", help="IP address or hostname, e.g. cDAQ9191-1D92246")
    a.add_argument("name", help="e.g. cDAQ1 (vibration chassis) or cDAQ2 (temperature chassis)")
    a.add_argument("--timeout", type=float, default=15.0)
    a.set_defaults(func=cmd_add)
    r = sub.add_parser("reserve")
    r.add_argument("name")
    r.add_argument("--force", action="store_true", help="override another host's reservation")
    r.set_defaults(func=cmd_reserve)
    s = sub.add_parser("selftest")
    s.add_argument("name")
    s.set_defaults(func=cmd_selftest)
    t = sub.add_parser("test")
    t.add_argument("--sim", action="store_true", help="use simulated devices")
    t.add_argument("--config", default=str(DEFAULT_CONFIG))
    t.add_argument("--seconds", type=float, default=1.0)
    t.add_argument("--settle", type=float, default=2.0, help="IEPE/AC-coupling settling time to discard")
    t.set_defaults(func=cmd_test)
    args = p.parse_args()
    try:
        return args.func(args)
    except nidaqmx.DaqError as e:
        print(f"DAQmx error {e.error_code}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
