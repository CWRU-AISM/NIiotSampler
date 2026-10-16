# Sampling console: design plan

A local web page for running the IM fault rig's data collection. It lets you set up sampling, watch the
signals live, name and record runs quickly, and load any saved run to check it and plot it.

```
python daq/console/server.py            # real hardware (cDAQ1)
python daq/console/server.py --demo     # synthetic motor signals, no hardware needed
python daq/console/server.py --sim      # NI-DAQmx simulated devices (setup/sim_devices.ini)
→ open http://127.0.0.1:8050
```

## 1. Strategy

- **It runs locally on the acquisition PC.** The page needs the cDAQ, and only this PC can reach it,
  so the server binds to `127.0.0.1` only.
- **Reuse, don't fork.** Tasks are built by `common.build_vib_task()` from `config.toml`, exactly as
  in `acquire.py`, `daq_check.py` and `scope.py`. The page and the CLI cannot disagree about channel
  settings.
- **One acquisition engine.** While the server runs, the DAQ task streams continuously into a ring
  buffer. **Record taps that same stream** and writes it to HDF5, so the live view shows exactly
  what is saved. If the stream started less than `settle` s ago, the samples before then are
  discarded first (IEPE / AC-coupling settling).
- **Same files as the CLI.** HDF5 writing lives in `daq/recorder.py`, and `acquire.py` uses the same
  code. The layout (`/vib/<chan>`, root attrs, `config_toml`), the `data/YYYY-MM-DD/` folders and
  the `runs_index.csv` columns are unchanged.
- **One set of checks.** `daq/checks.py` holds the signal-nature checks. They run on the live window,
  on every run right after it is saved, and on any file loaded later.
- **Light browser load.** The server computes spectra and thins the waveforms to min/max envelopes
  (about 1000 points per trace, about 5 updates/s). Full-rate data only goes to disk.
- **Works offline.** uPlot is in `static/vendor/`. There is no build step and no CDN.

## 2. Page modules

| # | Module | What it does |
|---|---|---|
| 0 | Status bar | Chassis, module type and serial, sample rate, stream state, recording state. DAQmx errors in plain words; for -201420 (reserved by another PC) a **Take over** button. Self-test, reconnect. |
| 1 | Sampling config | Sample rate limited to valid NI 9230 rates (12.8 kHz / n, n = 1…13). Per channel: input, sensitivity, range, IEPE (0 / 4 mA), coupling, location. Validated against module limits. **Apply** (this session) or **Save to config.toml** (comments kept). Locked while recording. |
| 2 | Run naming | Fault buttons (your own labels; checklist faults are added automatically), drive Hz, load, operator (remembered), notes, tag. Live filename preview. Duplicate warning. Optional test matrix (faults × Hz × repeats) that moves to the next run after each save. |
| 3 | Live view | Per channel: waveform and spectrum (window, f-max, lin/log, averaging, freeze). Markers from the drive Hz: f and its odd harmonics on current; 1×, 2×, 3× shaft and 60 Hz line on the accelerometer. Readouts: RMS, peak, crest factor, dominant Hz, range used. |
| 4 | Record | Duration, settle, Start/Stop, progress. Stop keeps a partial file (`completed = False`). When the file is saved, the checks run on it and a result card appears. |
| 5 | Signal checks | Pass / warn / fail list (see §3). Live (last 4 s, refreshed every second) and per file. |
| 6 | Runs & review | Every `.h5` under `data/`, joined with `runs_index.csv`. Filter by fault, date, flag. Open a run: metadata, full-length zoomable waveform, averaged spectrum with markers, spectrogram, check report. Overlay a second run's spectrum to compare. Flag keep/bad (stored in `data/run_flags.json`, so the CSV format is unchanged). |

## 3. Signal-nature checks (`daq/checks.py`)

The motor is 2-pole (Marathon 1/2 HP, 3450 rpm), so the shaft speed is drive Hz × (1 − slip).
`[motor]` in `config.toml` sets poles and the plausible current band.

| Check | Channel | Rule |
|---|---|---|
| alive | all | RMS(ac) above the sensor's noise floor, otherwise "no signal: motor off / cable / wrong input" |
| clipping | all | peak < 90 % of range (warn), < 98 % (fail) |
| flatline / NaN | all | no NaN; no long runs of identical samples |
| DC offset | accel (AC) | \|mean\| small compared with RMS, otherwise IEPE fault or still settling |
| drive frequency | current | dominant 2–150 Hz peak within ±max(0.3 Hz, 2 %) of the entered drive Hz |
| current level | current | RMS inside `[motor] current_rms_A`. A value about 10× out of band points at a wrong mV/A setting |
| line pickup | current, accel | 60 Hz dominating while the drive is not near 60 Hz → ground-loop / pickup warning |
| shaft 1× | accel | clear peak in [f_s(1 − 8 %), f_s], where f_s = measured current Hz (or entered Hz) / pole pairs; reports slip |
| slip plausible | accel + current | slip in 0.2–6 % |
| integrity | file | sample count = duration × rate, `completed`, required metadata present |
| fault hints | info only | 1× level, 2×/1× ratio (misalignment), broken-bar sidebands at f(1 ± 2s) in current, dB below f |

Spectra: Hann window, amplitude-scaled. Long files use Welch averaging (8 s segments, 0.125 Hz bins),
and peak frequencies are refined by parabolic interpolation.

## 4. Code layout

```
daq/recorder.py      RunWriter: HDF5 + runs_index.csv (used by acquire.py and the console)
daq/checks.py        spectra helpers + signal-nature checks (usable without the console)
daq/console/
  DESIGN.md          this plan
  server.py          FastAPI app: REST + /ws/live WebSocket, static files; CLI entry point
  engine.py          acquisition thread: DAQ / demo source, ring buffer, recording hook, reconnect
  review.py          load saved .h5 files: summaries, envelopes, spectra, spectrogram
  static/            index.html, app.js, style.css, vendor/uPlot
```

API (all JSON):

- `GET /api/state` (status, device, config, recording)
- `GET|POST /api/config` (`{apply, save}`)
- `POST /api/device/reserve`, `POST /api/device/selftest`, `POST /api/stream/restart`
- `POST /api/record/start`, `POST /api/record/stop`
- `GET /api/runs`, `GET /api/run?file=`, `GET /api/run/window?file=&t0=&t1=`, `POST /api/run/flag`
- `WS /ws/live`. The client sends `{window_s, fmax, avg, drive_hz}`; the server pushes frames of
  envelopes, spectra, stats and checks.

## 5. Build phases

1. Engine, live view, status bar.
2. Naming, recording, checks on save. **This is enough to collect data.**
3. Live checks panel, sampling-config editor.
4. Runs browser and review (load, check and plot saved files), comparison, test matrix.

Dependencies added: `fastapi`, `uvicorn`, `tomlkit`.

## 6. Status (2026-10-15)

All four phases have a first working version:
- tested in `--demo`;
- the hardware stream and self-test tested on the cDAQ-9191 / NI 9230;
- the checks validated on a motor-on probe (21.25 Hz, slip 2.7 %).

Added after the first build:
- **Stop / Start stream** (and `--stopped`) closes the DAQ task and frees the ring buffer. Record
  restarts it with at least 5 s settling. The page receives no frames while the stream is off,
  frozen, or on another tab.
- A **Guide** tab renders `USER_GUIDE.md` (configuration advice: what to change and when), and the
  config table has a "should I change it?" column.

- **Discard / delete**: discard while recording, delete from the saved-run card or the Review
  tab. Two clicks are needed, and the file is moved to `data/_trash/` (never erased). The
  `runs_index.csv` row and the flag are removed, and a test-matrix item goes back to "to do".

- **Checklists** (`checklist.py`, `static/checklist.js`, Checklist tab and card):
  - a plan of faults × loads × speeds × rounds, plus speed and load sweeps, expanded into items
    stored as JSON in `data/checklists/`, with import and export;
  - recording from an item fills the form and writes `checklist` / `checklist_item` /
    `sweep_schedule` attributes into the file;
  - after the post-recording checks the item becomes done or attention;
  - a deleted run puts its item back to "to do";
  - a steady detector (current frequency at target + RMS stable), optional auto-record, and a
    sweep cue banner with a beep at each step;
  - a sweep check (current-frequency track against the schedule) in `checks.py`.

Changes outside the console:
- `acquire.py` now writes through `recorder.RunWriter`. The file layout is unchanged, but the
  filename gets a `_<load>` suffix when the load is not `none`.
- `config.toml` gained a `[motor]` section for the checks.

Ideas not built yet:
- a log-frequency axis for the spectrogram;
- bearing defect frequencies (BPFO / BPFI), once the bearing geometry is known;
- adding or removing channels from the page;
- a temperature panel, for when a thermocouple module is added.
