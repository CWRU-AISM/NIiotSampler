# NIiotSampler guidebook

How to set up a PC that records vibration, motor current and (optionally) temperature from the
SpectraQuest induction-motor (IM) fault rig through an NI cDAQ-9191, and what we know about the
hardware so far. The original rig survey with photo details (Chinese) is in
[IM_DAQ_设备信息.md](IM_DAQ_设备信息.md).

---

## 0. Status at a glance (2026-10-15)

| | |
|---|---|
| **Acquisition PC** | **Windows 11, 64-bit.** The cDAQ-9191 does **not** work with NI-DAQmx on Linux (tested, §9). |
| Chassis on site | **One** cDAQ-9191, S/N **1D92246**. The rig survey mentions a second chassis (1D9225B, thermocouple module); it was not there. |
| Signals available now | Vibration (Kistler accelerometer) + motor current (clamp) on the chassis' BNC module. Temperature needs a thermocouple module in a second chassis, so it is switched off in the config. |
| Software | `daq/` scripts tested end-to-end on simulated devices (NI 9234 + NI 9211). They have not yet recorded from the real chassis. |
| Still to confirm on site | Module model, which BNC is which sensor, clamp mV/A, accelerometer sensitivity (§12). |

---

## 1. The rig

```
Lenze SMVector VFD ──► Marathon 1/2 HP 3-phase IM ──► flexible coupling ──► shaft + rotor disk
                            │                     │
                    accelerometer            current clamp (on one motor supply lead)
                            └──────── BNC module ─┘
                                        │
                              NI cDAQ-9191 (1 slot)  ── Ethernet ──► TRENDnet switch ──► PC
```

**Motor:** Marathon Electric D391 / 56T34F5301J P, 56C frame, 3-phase, 2-pole, TEFC.
1/2 HP, 3450 rpm, 208-230/460 V at 60 Hz; FLA 2-2.2/1.1 A. A drive frequency of 41 Hz gives a
synchronous speed of about 2460 rpm and a real speed of about 2400 rpm. Shaft frequency is
therefore about 40 Hz.

**Drive:** Lenze AC Tech SMVector. Use ▲/▼ to set the frequency, then RUN or STOP. The display
shows the output frequency in Hz. Its PWM output puts the carrier frequency and its harmonics
into the current signal, which matters for MCSA. After power-off, the capacitors stay charged
for about 180 s.

**Sensors**

| Sensor | Details | DAQ setting |
|---|---|---|
| Accelerometer **Kistler 8702B25**, SN 5460544 | IEPE, ±25 g, nominal **200 mV/g** (use the calibration certificate value), 1 Hz–8 kHz (±5 %), 10-32 side connector. Mounted on the motor housing at the drive end, radial. | IEPE on (2 mA), AC coupling |
| Current clamp (grey body, red jaws) | Model unknown; typically 10 or 100 mV/A (label or range switch). Clamped on one motor lead. | Plain voltage, IEPE **off**, DC coupling; volts → A in software |
| Thermocouple | Taped to the housing; type probably K. Needs a thermocouple module (NI 9211/9212/9213). | Built-in CJC, 1–10 S/s |

**DAQ hardware**

| | Chassis on site | Mentioned in survey, not found |
|---|---|---|
| Model | cDAQ-9191, 1 slot, Ethernet + 802.11 Wi-Fi | cDAQ-9191 |
| P/N | 150227E-21L | 150227E-21L |
| S/N | **1D92246** | 1D9225B (photo unclear) |
| Ethernet MAC | 00:80:2F:22:86:48 | 00:80:2F:22:86:5D (unconfirmed) |
| Hostname | `cDAQ9191-1D92246` | – |
| Module | BNC/IEPE module, probably **NI 9234** (to confirm) | spring-terminal thermocouple module |

The floor equipment is a TRENDnet GREENnet Ethernet switch and a TRENDnet fibre media converter
with an orange multimode patch cord, plus the power adapters.

---

## 2. What you need

**Hardware**
- A **Windows 11 64-bit** PC with a free Ethernet port and administrator rights. NI-DAQmx 2026 Q3
  supports Windows 11, not Windows 10. On Windows 10, install an older NI-DAQmx whose readme lists
  Windows 10.
- An Ethernet patch cable from the PC to the TRENDnet switch (or straight to the chassis).
- Power for the cDAQ-9191 and the switch.

**Software** (internet needed for the install; nothing is needed once on site)
- NI-DAQmx 2026 Q3 (26.5) with **NI MAX** (NI Measurement & Automation Explorer). Free from NI.
- Python **3.11 or newer** (3.13 recommended), from python.org or `winget`.
- Git (optional; you can also download the repo as a ZIP).

**Information to bring**
- The accelerometer calibration certificate (Kistler SN 5460544) for the exact mV/g.
- The current clamp's model and range (written on the clamp).
- The open-items list in §12.

---

## 3. Windows setup, step by step

### 3.1 Install NI-DAQmx (with NI MAX)
1. Download **NI-DAQmx** from <https://www.ni.com/en/support/downloads/drivers/download.ni-daq-mx.html>.
   The 2026 Q3 online installer is
   <https://download.ni.com/support/nipkg/products/ni-d/ni-daqmx/26.5/online/ni-daqmx_26.5_online.exe>.
2. Run it. NI Package Manager opens. Keep **NI Measurement & Automation Explorer** ticked, accept
   the licences, and install. Expect a few GB and 15–30 min.
3. Reboot.

Alternative once Python and this repo are set up (§3.2–3.4): `.venv\Scripts\python -m nidaqmx installdriver`
downloads and launches the same installer.

### 3.2 Install Python and Git
In PowerShell:
```powershell
winget install Python.Python.3.13
winget install Git.Git
```
Then open a **new** PowerShell window so the new commands are on PATH. Use `py` rather than
`python`, because on a fresh Windows `python` may open the Microsoft Store.

### 3.3 Get this repository
```powershell
cd $HOME\Documents
git clone https://github.com/CWRU-AISM/NIiotSampler.git
cd NIiotSampler
```

### 3.4 Create the Python environment
```powershell
powershell -ExecutionPolicy Bypass -File setup\windows_setup.ps1
```
The script creates `.venv`, installs `requirements.txt` (nidaqmx, numpy, h5py, matplotlib) and checks
that Python can see NI-DAQmx. It was written on Linux and hasn't been run on Windows yet. If it fails,
do the same steps by hand:
```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -c "import nidaqmx.system as s; print(s.System.local().driver_version)"
```
`requirements.lock.txt` lists the exact package versions that worked on 2026-10-15, as a reference.

To use the environment in a terminal, either run `.venv\Scripts\Activate.ps1` (if PowerShell blocks
it: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`) or call `.venv\Scripts\python` directly.
The commands below assume the environment is activated (`python` = `.venv\Scripts\python`).

### 3.5 Connect the network
1. Power the cDAQ-9191 and the switch. Plug the PC into the switch.
2. Leave the PC's Ethernet adapter on its default, *Obtain an IP address automatically*. On the
   isolated switch there is no DHCP server. After about a minute both the PC and the chassis
   pick 169.254.x.x (link-local) addresses and can see each other. On 2026-10-15 the chassis came
   up at **169.254.27.243**.
3. Check: `ipconfig` shows 169.254.x.x on the Ethernet adapter, and `ping cDAQ9191-1D92246` (or the IP)
   answers.
4. Windows will call this an *Unidentified network* and treat it as **Public**. If NI MAX can't find
   the chassis although ping works, allow NI MAX/Python through the firewall on Public networks.
   Alternatively, mark the network Private (admin PowerShell):
   `Set-NetConnectionProfile -InterfaceAlias "Ethernet" -NetworkCategory Private`.

### 3.6 Add and reserve the chassis in NI MAX
1. Open **NI MAX**. Under **Devices and Interfaces**, right-click **Network Devices** →
   **Find Network NI-DAQmx Devices**.
2. Tick the cDAQ-9191 with serial **1D92246** → **Add Selected Devices**.
3. Right-click the chassis → **Reserve Chassis**. Only one PC can hold the reservation. The module
   appears under the chassis only once it is reserved.
4. Rename the chassis to **`cDAQ1`** if NI MAX gave it another name (right-click → Rename). The module
   then shows as **`cDAQ1Mod1`**, which is what `daq/config.toml` expects.
5. Right-click the module → **Self-Test**. If NI MAX offers a chassis firmware update, accept it.
6. Optional: open **Test Panels** on the module and look at ai0–ai3 (IEPE on for the accelerometer).

The same from the command line: `python daq/daq_check.py add cDAQ9191-1D92246 cDAQ1`.

### 3.7 Check from Python and fill in the config
```powershell
python daq/daq_check.py devices
```
This lists the chassis, the module model and its channels. Open `daq/config.toml` and resolve every
`TODO(onsite)`:
- `expected_module`: the model printed by `devices`, e.g. `NI 9234`.
- `physical` for each channel: which BNC (ai0–ai3) the accelerometer and the clamp are plugged into.
- `sensitivity_mV_per_g`: from the Kistler calibration certificate.
- `sensitivity_mV_per_A`: from the clamp label or range switch.

### 3.8 Check the signals (motor running)
```powershell
python daq/daq_check.py test     # mean / RMS / peak / dominant frequency per channel
python daq/scope.py              # live waveform + spectrum; close the window to stop
```
What to expect at a drive frequency of 41 Hz:
- **Current:** dominant frequency ≈ 41 Hz.
- **Accelerometer:** a 1× peak near 40 Hz (shaft speed = drive Hz × (1 − slip)).

If the current reads about 10× too high or too low, the clamp range or `sensitivity_mV_per_A` is wrong.

### 3.9 Record
One command per run (condition):
```powershell
python daq/acquire.py --duration 60 --freq 41 --fault normal --load none --notes "baseline" --operator "your name"
python daq/acquire.py --duration 60 --freq 41 --fault unbalance --notes "2 bolts on rotor disk, 180 deg"
```
- `--fault`: free text. Suggested names: `normal`, `unbalance`, `misalignment`, `bearing_inner`,
  `bearing_outer`, `broken_bar`.
- `--freq`: the frequency shown on the Lenze drive.
- Ctrl+C stops early and keeps what was recorded (`completed = False`).
- Each run writes `data\YYYY-MM-DD\<YYYYmmdd_HHMMSS>_<fault>_<freq>Hz.h5` and adds a row to
  `data\runs_index.csv`. `data/` is not in git; copy it to the shared drive.

---

## 4. Command cheat sheet

| Command | What it does |
|---|---|
| `python daq/daq_check.py devices` | List chassis, modules and channels that DAQmx knows |
| `python daq/daq_check.py find` | Linux: search the network. Windows: tells you to use NI MAX |
| `python daq/daq_check.py add <IP or hostname> cDAQ1` | Add + reserve a network chassis |
| `python daq/daq_check.py reserve cDAQ1 [--force]` | Reserve; `--force` takes it from another PC (ask its owner first) |
| `python daq/daq_check.py selftest cDAQ1Mod1` | Module self-test |
| `python daq/daq_check.py test [--sim]` | Read every configured channel and print statistics |
| `python daq/scope.py [--sim] [--window 1] [--fmax 2000]` | Live waveform + spectrum |
| `python daq/acquire.py --duration S --freq HZ --fault NAME [...] [--sim]` | Record one run to HDF5 |

Every script takes `--config <file>` to use a config other than `daq/config.toml`.

---

## 5. Configuration reference (`daq/config.toml`)

- **`[vib]`** (the BNC module)
  - `device`: DAQmx module name (`cDAQ1Mod1`).
  - `sim_device`: used with `--sim`.
  - `expected_module`: a warning is printed if the real module differs.
  - `sample_rate`: NI 9234 rates are 51 200 / n S/s (51200, 25600, 17066.7, 12800, …). 25 600 S/s
    covers the accelerometer's 8 kHz band.
- **`[[vib.channels]]`**, one block per BNC input in use
  - `kind = "accel"`: IEPE accelerometer, scaled by DAQmx to g. Fields: `sensitivity_mV_per_g`,
    `range_g`, `iepe_current_A`, `coupling = "AC"`.
  - `kind = "current_clamp"`: voltage, IEPE off, scaled in software to A by `sensitivity_mV_per_A`.
    Fields: `range_V`, `coupling = "DC"`.
  - `physical` (ai0–ai3), `name` (HDF5 dataset name) and `location` (free text, saved in the file).
- **`[temp]`**: thermocouple module, read in software at `rate_hz` while recording. **`enabled = false`**
  for now. Set it to `true` once a thermocouple module is connected (its own chassis, e.g. `cDAQ2Mod1`).
  Each channel has `physical`, `tc_type` (K, J, T, …), `min_C` and `max_C`.

The full config text is saved inside every HDF5 file, so each recording documents its own settings.

---

## 6. Data format

HDF5, one file per run:

| Path | Content |
|---|---|
| `/` attrs | `fault`, `drive_freq_hz`, `load`, `notes`, `operator`, `start_time`, `duration_s`, `completed`, `simulated`, `daqmx_version`, `nidaqmx_python`, `host`, `config_toml` |
| `/vib` attrs | `sample_rate`, `t0_unix` (PC time of the first kept sample), `n_samples`, `device_*` (model, serial) |
| `/vib/<channel>` | float64 samples in **g** or **A**; attrs: `units`, `physical`, sensitivity, … |
| `/temp/t_s`, `/temp/t_unix`, `/temp/<channel>` | only when `[temp]` is enabled: °C, time in s since `/vib` t0 |

The first `--settle` seconds (default 2 s) are discarded so the IEPE bias and AC coupling can settle.
Temperature is timed by the PC clock, so its alignment with vibration is good to about 0.1 s.

```python
import h5py
with h5py.File("data/2026-10-16/20261016_101500_normal_41Hz.h5") as f:
    fs = f["vib"].attrs["sample_rate"]
    acc = f["vib/accel_de_radial"][:]      # g
    cur = f["vib/motor_current"][:]        # A
    print(dict(f.attrs))
```
```matlab
acc = h5read('x.h5', '/vib/accel_de_radial'); fs = h5readatt('x.h5', '/vib', 'sample_rate');
```

`data/runs_index.csv` has one row per run: `file`, `start_time`, `fault`, `drive_freq_hz`, `load`,
`duration_s`, `completed`, `simulated`, `operator`, `notes`.

---

## 7. Network, reservation and Wi-Fi

- **Wired is the recommended setup.** The data rate is small (2 channels × 25.6 kS/s ≈ 1–2 Mbit/s;
  4 × 51.2 kS/s ≈ 7 Mbit/s), but a Wi-Fi dropout of a few seconds can abort a long continuous
  recording.
- The 9191 also has 802.11 Wi-Fi. It has a stored wireless address, **172.19.14.90**, but on
  2026-10-15 that address did not answer from a laptop on eduroam. Either the chassis wasn't on
  Wi-Fi, or the campus network blocks it. Wi-Fi is configured in NI MAX (chassis → *Network
  Settings*), normally while connected by cable.
- **Reservation:** a network cDAQ belongs to one host at a time. If another lab PC has reserved it,
  ask before using `reserve --force`. Release it in NI MAX (*Unreserve Chassis*) when moving to
  another PC.
- If no DHCP server answers, the chassis is in its default "DHCP or link-local" mode and falls back
  to 169.254.x.x. If the switch is connected to a lab network with DHCP, both sides get addresses
  from it instead; nothing changes in the software.

---

## 8. Testing without hardware (simulated devices)

`setup/sim_devices.ini` defines `SimDAQ1` (cDAQ-9171 + NI 9234) and `SimDAQ2` (cDAQ-9171 + NI 9211).
They're USB chassis because a simulated cDAQ-9191 can be created but can't run tasks
(error -89130/-88705). The task code is the same.

- **Windows:** in NI MAX use *File → Import* and select `setup/sim_devices.ini`. If the import complains
  about the version, edit `MajorVersion`/`MinorVersion` to the installed DAQmx. Alternatively,
  right-click Devices and Interfaces → *Create New → Simulated NI-DAQmx Device* and create a
  cDAQ-9171 with an NI 9234 in slot 1, named `SimDAQ1`.
- **Linux:** `nidaqmxconfig --import setup/sim_devices.ini`.

Then add `--sim` to any command, e.g. `python daq/acquire.py --sim --duration 5 --freq 41 --fault test`.
Simulated values are synthetic, so numbers such as −1100 °C mean nothing.

---

## 9. Linux notes (what we tried)

- NI-DAQmx 2026 Q3 installs and runs on **Ubuntu 26.04 / kernel 7.0**, using NI's Ubuntu 24.04
  ("noble") repository. Neither the OS nor the kernel is officially supported. The scripts are in
  `setup/linux/`:
  ```bash
  mkdir -p downloads/ni-linux-drivers-2026Q3 && cd downloads/ni-linux-drivers-2026Q3
  wget https://download.ni.com/support/softlib/MasterRepository/LinuxDrivers2026Q3/NILinux2026Q3DeviceDrivers.zip
  unzip NILinux2026Q3DeviceDrivers.zip && cd ../..
  sudo bash setup/linux/01_install_ni_daqmx.sh   # repo, ni-daqmx, DKMS modules, MOK, inotify, ufw
  sudo reboot                                    # Secure Boot: blue MOK screen → Enroll MOK → password
  bash setup/linux/03_setup_python.sh
  bash setup/linux/02_check_install.sh
  ```
  With Secure Boot on, the DKMS signing key `/var/lib/shim-signed/mok/MOK.der` must be enrolled.
  Avoid kernel updates, since a new kernel can break the NI modules.
- **The cDAQ-9191 is not supported on Linux.** `nidaqmxconfig --find-net-devs` finds it and
  `--add-net-dev` adds it. But `--reserve` and `--self-test` fail with
  **-201427 "The specified device is not supported within the NI-DAQmx API"**, so its module never
  appears and no task can run. NI's Linux CompactDAQ support covers only the USB chassis
  (cDAQ-9171/9174/9178/9179) and the TSN Ethernet chassis (cDAQ-9185/9189). On Linux with one of those
  chassis, the scripts here work as they are.
- Linux has no NI MAX. The equivalents are `nilsdev --verbose` (list devices) and `nidaqmxconfig`
  (`--find-net-devs`, `--add-net-dev`, `--reserve`, `--import`, `--export`, `--self-test`).
- Python `nidaqmx` 1.6.0 quirk: `Device.reserve_network_device()` must get an explicit
  `override_reservation=True/False`. The default `None` raises a ctypes TypeError.

---

## 10. Troubleshooting

| Symptom | What to check |
|---|---|
| NI MAX finds no network device | Link LED; wait ~1 min after power-up; `ipconfig` shows 169.254.x.x; ping the chassis; firewall on the Public network (§3.5); add by hostname or IP |
| Module not shown under the chassis | Chassis isn't reserved by this PC → *Reserve Chassis* |
| -201003 / "reserved by another host" | Another PC holds it; ask, then *Reserve Chassis* with override or `daq_check.py reserve cDAQ1 --force` |
| -201427 "not supported within the NI-DAQmx API" | You're on Linux with a cDAQ-9191 → use Windows (§9) |
| "Device cDAQ1Mod1 not found" (-200220) | Chassis or module name differs → rename in NI MAX or edit `device` in `config.toml` |
| WARNING: module is a …, config assumes … | Update `expected_module` and check the channel settings fit the module |
| -200279 buffer overflow | PC fell behind; close other programs. The buffer holds 10 s |
| Firmware too old / mismatch | NI MAX → right-click chassis → Update Firmware |
| Accelerometer reads a constant / huge offset | IEPE not powered (wrong `kind`), or sensor unplugged; give it the 2 s settle |
| Current wrong by 10× | `sensitivity_mV_per_A` vs the clamp's range switch |
| `ModuleNotFoundError: tomllib` | Python older than 3.11 |
| `python` opens Microsoft Store | Use `py`, or `.venv\Scripts\python` |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or call `.venv\Scripts\python` directly |

---

## 11. Repository layout

```
GUIDEBOOK.md                 this guide
IM_DAQ_设备信息.md            original rig survey (2026-10-12, Chinese)
daq/config.toml              channels, sensitivities, rates  <- edit on site (TODO(onsite))
daq/common.py                config loading + DAQmx task construction
daq/daq_check.py             devices / find / add / reserve / selftest / test
daq/scope.py                 live waveform + spectrum
daq/acquire.py               record one run to HDF5 + runs_index.csv
setup/windows_setup.ps1      Windows: create .venv, install packages, check NI-DAQmx
setup/linux/                 Linux: driver install, Python env, post-reboot check
setup/sim_devices.ini        simulated chassis/modules for testing without hardware
requirements.txt             Python packages; requirements.lock.txt = known-good versions
```

---

## 12. Open items (待确认)

- [ ] Module model in the cDAQ-9191 (NI MAX / `daq_check.py devices` will show it)
- [ ] Which BNC input carries the accelerometer and which the current clamp
- [ ] Current clamp model and mV/A range; which motor phase it is on
- [ ] Accelerometer sensitivity from the calibration certificate (nominal 200 mV/g)
- [ ] Second chassis (S/N 1D9225B) and thermocouple module: where are they? Thermocouple type?
- [ ] Is the 9191's Wi-Fi in use, and on which network?
- [ ] Does the lab have existing LabVIEW programs or an acquisition protocol?

## 13. Log

- **2026-10-12**: rig surveyed from photos and nameplates → `IM_DAQ_设备信息.md`.
- **2026-10-15**:
  - NI-DAQmx 2026 Q3 installed on an Ubuntu 26.04 laptop, and the scripts tested on simulated devices.
  - On site, wired to the switch: one cDAQ-9191 (1D92246) found at 169.254.27.243. Linux can't
    reserve it (-201427).
  - Decision: acquire with a Windows tower over wired Ethernet.
