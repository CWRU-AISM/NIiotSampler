# NIiotSampler

Data collection for the SpectraQuest induction-motor fault rig: vibration (Kistler IEPE
accelerometer), motor current (clamp) and optionally housing temperature, recorded through an
NI cDAQ-9191 with Python `nidaqmx` to HDF5 files.

**Start here: [GUIDEBOOK.md](GUIDEBOOK.md)**. It covers prerequisites, Windows setup step by step,
configuration, data format, troubleshooting, and what we learned about the hardware.

> The cDAQ-9191 needs **Windows** (NI-DAQmx + NI MAX). NI-DAQmx on Linux finds it but refuses to
> reserve it (error -201427), so Linux only works with USB or 9185/9189 cDAQ chassis.

## Quick start (Windows 11)

1. Install **NI-DAQmx** (with NI MAX) from ni.com, then **Python 3.11+** (`winget install Python.Python.3.13`).
2. Get the code and set up Python:
   ```powershell
   git clone https://github.com/CWRU-AISM/NIiotSampler.git; cd NIiotSampler
   powershell -ExecutionPolicy Bypass -File setup\windows_setup.ps1
   .venv\Scripts\Activate.ps1
   ```
3. Wire the PC to the chassis' switch. In NI MAX: *Network Devices → Find Network NI-DAQmx Devices*
   → add S/N 1D92246 → *Reserve Chassis* → name it `cDAQ1`.
4. Fill in the `TODO(onsite)` items in `daq/config.toml`, then:
   ```powershell
   python daq/daq_check.py devices
   python daq/daq_check.py test
   python daq/scope.py
   python daq/acquire.py --duration 60 --freq 41 --fault normal --notes "baseline"
   ```

Without hardware, import `setup/sim_devices.ini` and add `--sim` to any command.
