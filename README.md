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

## Sampling console (web page)

Live waveform and spectrum, signal checks (drive Hz, shaft 1x / slip, clipping, dead channels),
quick run naming, recording checklists, and a review tab that loads any saved run to check and plot
it. It writes the same HDF5 files as `acquire.py`. User guide:
[daq/console/USER_GUIDE.md](daq/console/USER_GUIDE.md) (also in the console's Guide tab). Design
notes: [daq/console/DESIGN.md](daq/console/DESIGN.md).

### Run it on any Windows PC

1. Install **Python 3.11+** (`winget install Python.Python.3.13`, or python.org; tick "Add to PATH").
2. Get the project: `git clone https://github.com/CWRU-AISM/NIiotSampler.git`, or on GitHub
   *Code → Download ZIP* and unzip it.
3. Double-click in the project folder:
   - **`Start-Console-Demo.bat`**: simulated motor signals, **no NI hardware or driver needed**.
     Good for trying the page and for training. Demo recordings go to `demo_data\`.
   - **`Start-Console.bat`**: the real cDAQ.

   The first start creates `.venv` and installs the packages (needs internet, a few minutes). After
   that it starts in seconds. The page opens at http://127.0.0.1:8050. Close the black window, or
   press Ctrl+C in it, to quit.

For real hardware on a new PC, also:
- install **NI-DAQmx** with NI MAX, then add and reserve the chassis (Quick start step 3 above);
- edit **`daq/config.toml`** for that rig: `device`, each channel's `physical` input (ai0–ai3) and
  sensitivity (mV/g, mV/A), and `[motor] poles`. The values in the repo are this rig's. Or edit
  them in the console's *Sampling config* tab.

From a terminal instead: `.venv\Scripts\python daq\console\server.py` (`--demo`, `--stopped`,
`--port 8060`, `--data D:\runs`). The console only listens on this PC (127.0.0.1); it is not
reachable from the network.
