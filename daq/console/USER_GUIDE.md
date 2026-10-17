# Sampling console: user guide

> **Short version.** Leave the configuration alone, with two one-time exceptions:
> 1. the **accelerometer sensitivity**, from its calibration certificate;
> 2. the **clamp mV/A**, which must match the switch on the clamp.
>
> Keep every setting identical for all runs in a data set. Per run, you only fill in the
> **fault**, the **target drive Hz**, and the **duration**.

> ⚠ **IMPORTANT: set the speed by the console's MEASURED value, not by the Lenze display.**
>
> The Lenze SMVector runs open loop (V/Hz): there is no speed or frequency feedback, so the
> keypad number is only a command. Its real output differs from the keypad, and the
> difference changes from day to day and with temperature and load. Observed on this rig:
> keypad *n* gave *n* + 1.2 Hz on 2026-10-15, *n* + 1.45 Hz on 2026-10-16, and
> *n* + 1.7 Hz right after a cold start.
> Slip compensation (output raised with motor current) and analog-reference drift are the
> likely causes.
>
> **MEASURED** is the fundamental frequency of the motor current, taken from the DAQ.
> On the saved runs it agrees with the true frequency to about ±0.01 Hz. So:
> 1. **Turn the drive until MEASURED = TARGET** (badge green). Ignore what the keypad says.
> 2. **Warm up 10–15 min** before the first recording of a session; the offset shrinks as
>    the rig warms.
> 3. **Re-check MEASURED at every speed and load change.** The offset depends on load, so a
>    load step moves the frequency even if you do not touch the keypad.
>    Re-tune to the **same** MEASURED Hz at every load. The **shaft** speed (shaft 1× in Signal
>    checks) will still be a little lower at higher load. That is motor slip, part of the
>    load's signature, so leave it: do not try to hold the shaft rpm constant.
>    In a **load sweep** recording, do not touch the drive during the sweep; just set the
>    speed before pressing Record.
> 4. Write **target** Hz in the file name (the checklist does this). For analysis, use the
>    measured frequency (Review → checks, computed from the file), not the label.
>
> Matching MEASURED is what makes runs from different days and rounds comparable.

## 1. Start and stop

```powershell
cd C:\Users\xxz1277\Desktop\NIsampler\NIiotSampler
.venv\Scripts\Activate.ps1
python daq\console\server.py              # opens http://127.0.0.1:8050
python daq\console\server.py --stopped    # same, but the live stream starts switched off
python daq\console\server.py --demo       # fake motor signals, for practice without hardware
```

To quit, press **Ctrl+C** in the terminal. Closing the browser tab does **not** stop the server or
release the DAQ.

**Only one program can use the NI 9230 at a time.** While the console's stream is on,
`acquire.py`, `scope.py`, `daq_check.py test` and the NI MAX test panels fail with
"resource reserved". Press **■ Stop stream** in the console, or quit it, before using them.

## 2. Recording a run

1. **Start the motor**, turn the Lenze until **MEASURED = target** (not the keypad number), and **wait until speed and current are steady**.
   About 30 s after a speed change is a good habit.
2. On the **Acquire** tab, check the live view. The **Signal checks** panel should show no ✗:
   - `motor_current`: "fundamental … Hz = drive … Hz";
   - `accel_de_radial`: "shaft 1x … Hz, slip …%".
3. Fill in **Run naming**:
   - Click a **fault** button. For anything else, type a label and press *add*; it is remembered.
   - **Drive Hz**: the **target** frequency. Adjust the Lenze until the "measured current
     fundamental" below the field equals it. Do **not** copy the Lenze display: it is open loop
     and drifts (see ⚠ at the top). *use* copies the measured value into the field.
   - **Load**, **operator** and **notes** are optional, but they are stored in the file, so use
     them. Put anything unusual in notes (sensor moved, bolts changed, extra noise…).
   - **Tag** is added to the filename, e.g. `r1`, `disk2`.
4. Pick a **duration** (see §5) and press **● Record**. The bar shows progress. **■ Stop** ends
   early and keeps a partial file (`completed = False`).
5. When the file is saved, the checks run on it and a card appears. A ✗ or ! there means
   *look before you continue*. **Open in review** shows the full file.

For a planned campaign, use a **checklist** instead of typing every run (§2b).

## 2b. Checklists (planned campaigns)

A checklist is the plan of a whole campaign, held in one JSON file:
faults × loads × speeds × rounds, plus optional speed and load sweeps.
The console walks you through it and ticks off each run once it is recorded **and checked**.

**Create one.** Open the **Checklist** tab and press **New…**. The form is pre-filled with the
current study:
- faults: health, unbalanced, faulted rotor, bowed rotor, broken rotor, phaseloss;
- loads: 0–5;
- speeds: 24, 28, 32, 35, 38, 41, 44, 46, 48, 50 Hz;
- 3 rounds, 60 s per point;
- a speed sweep (10 s steps) and a load sweep (20 s steps).

The preview shows the number of recordings and an estimated time. **Create checklist** saves
it to `data\checklists\<name>.json`.

**Structure.**
- One **session** = one fault in one round (e.g. `R1-health`). Do one session per mounting of
  the fault; rounds are the repeats on other days.
- Inside a session, items are ordered **by load, then speed**, so the load changes only 6 times.
- *serpentine* (the default) runs the speeds up at one load and down at the next. This avoids a
  big speed jump, and it stops "speed" from being confounded with "time since the motor started"
  (warm-up). *shuffled* breaks that link completely, at the cost of more speed changes.

**Recording with it** (Acquire tab, Checklist card at the top):
1. Pick the session. The card shows the current item, and **fills in the form** (fault, drive
   Hz, load `L<n>`, tag `R<round>`, duration).
2. Set the drive and the load. The card's **steady** line turns green when the measured current
   frequency equals the item's speed **and** the current RMS has stopped changing (load settled).
   The counter shows for how long.
3. Press **Record**, or tick **auto-record when steady for N s**: the console then starts by
   itself once the rig has been steady that long (30 s recommended).
4. After saving, the file is checked:
   - all checks OK → the item becomes **✓ done** and the card moves to the next item;
   - any ✗ → the item becomes **! attention**, with the reason. Re-record it (use the item
     again), or accept it in the Checklist tab if the reason is understood.
5. Deleting or discarding a checklist run puts its item back to **to do**.

**Speed badge.** The card shows **MEASURED** and **TARGET** speed in a coloured box:
- green: steady, ready to record;
- amber: on speed, but the current is still changing or the load change is not confirmed;
- red: off target or no signal.

During a recording it stays red if the speed drifts.

MEASURED is the real supply frequency from the current clamp, and it is the value to match.
Turn the drive until MEASURED = TARGET. The Lenze keypad number is open loop and drifts by
1–2 Hz between days (⚠ at the top of this guide).

**Load changes.** When the next item needs a different load, a pop-up asks you to change it
(e.g. "L2 → L3"). Recording and auto-record are blocked until you press **✓ Load is set to 3**.

**Fixing mistakes.**
- **link…** (Checklist tab row, or "link an already saved file" on the card) attaches any saved
  run to an item. Use it for runs recorded without the checklist.
- **relabel** (ticked by default when linking) rewrites the file's fault, load and tag, renames
  the file and updates `runs_index.csv`. The old labels are kept inside the file
  (`relabelled_from`).
- **unlink** puts an item back to "to do" and keeps the file.
- **Move…** relinks *all* recordings of the shown session to the matching items of another
  session, e.g. runs recorded under `R1-health` while the rig had the unbalance fault →
  `R1-unbalanced`.

**Sweeps** (successive changes, one recording):

| Kind | What you do | How it is checked |
|---|---|---|
| speed sweep | Set the first speed, wait for steady, press Record. A big banner shows the current target and counts down; at each **beep** set the next speed on the Lenze. | The current fundamental is tracked every 0.5 s. Each step must reach its target speed in the second half of the step. |
| load sweep | Same, changing the load at each beep, with the speed fixed. | The speed must stay at its value; the current RMS per step is listed (it should rise with load). |

The step length must cover the Lenze ramp (acceleration / deceleration time; P104 / P105 on an
SMVector). Check them: with a 20 s 0→60 Hz ramp, a 5 Hz step takes ~1.7 s, which leaves ~8 s
of steady data in a 10 s step. The sweep check only uses the second half of every step.

**Import / export.**
- **Export JSON** downloads the checklist with its progress (status, file, check summary of
  every item). It is a lab log of the campaign: keep it with the data.
- **Import JSON** takes such a file, or a hand-written file with only a `"plan"`; the items are
  then generated. The format is described at the top of `daq/console/checklist.py`.
- Importing never overwrites: a name that already exists gets `-2`, `-3`, … appended.

### How long to record each point

Your motor is **2-pole**: at light load the shaft 1× sits only ~0.5 Hz below the supply frequency
(we measured 20.7 Hz vs 21.3 Hz). To separate the two, and to see broken-bar sidebands at
f(1 ± 2s), an analysis window needs ≈ 0.25 Hz resolution, i.e. a **4 s window**. This is the same
choice as the MFS part of the Interpretable-motor study (4 s windows, 0.5 s hop, 45 s of steady
data per record). Its NSTDA rig is 4-pole: 1× is half the line frequency there, so 1 s windows
were enough.

Windows per record, with 4 s windows:

| Steady duration | 50 % overlap (2 s hop) | non-overlapping |
|---|---|---|
| 45 s | 21 | 11 |
| **60 s** (default) | **29** | **15** |
| 90 s | 44 | 22 |

- Overlapping windows are not independent: 29 windows from 60 s carry about as much new
  information as the 15 non-overlapping ones.
- If you keep a time-blocked validation part inside each record (e.g. the last 15 s, as the MFS
  study did), the training part of a 60 s record still gives **21 windows at 2 s hop**.
- **≥ 20 windows per condition** is met by 60 s with 50 % overlap, or by 90 s non-overlapping.
  The rounds on other days add 2 more independent records per condition, and those matter more
  for generalisation than a longer single record.
- **Recommendation: 60 s per point, 3 rounds.** Use 90 s only if you need ≥ 20 non-overlapping
  windows inside one record.

**Phase loss.** A motor running on two phases draws much more current in the remaining phases
and heats quickly, and the VFD may trip. Keep phase-loss runs short (30 s), skip or limit the
high loads, and watch the motor temperature and the current. *skip* any item you decide not to
run.

## 3. Stream on/off, freeze, and memory

| Control | What it does | When to use it |
|---|---|---|
| **■ Stop stream** | Closes the DAQ task and frees its 40 s buffer (about 8 MB). CPU use drops to almost zero and the chassis is free for other programs. | Breaks, long idle times, before using `acquire.py` or NI MAX. |
| **▶ Start stream** | Reopens the DAQ task. | Before you want to look at signals. |
| **● Record** while the stream is off | Starts the stream, discards the first **5 s** while the IEPE / AC coupling settles, then records. | Fine for routine runs. You won't see the signal first, but the saved-file checks still run. |
| **✗ Discard this recording** | Stops the current recording and moves the file to `data\_trash` (two clicks). | See §7, *Deleting runs*. |
| **Freeze display** (key `f`) | Stops updating the plots only. The DAQ keeps running and recording is unaffected. | To read values or compare by eye. |
| Other tabs (Review, Config, Guide) | The page stops receiving live frames while you are not on *Acquire*. | Automatic. |

Memory use is small either way. The server holds at most 40 s of samples, and the page only
receives about 1000 points per trace. The real savings from **Stop stream** are CPU (FFTs five
times a second) and freeing the chassis.

## 4. Configuration: should I change it?

You can edit these on the **Sampling config** tab:
- **Apply (this session)** uses the change until the console restarts.
- **Apply & save** writes it to `daq/config.toml`, keeping the comments.

The stream restarts in both cases. **Every recorded file stores the configuration that was in
effect** (`config_toml` attribute), so you can always see later what a run used.

| Setting | Current value | Change it? | Why |
|---|---|---|---|
| **Sample rate** | 12,800 S/s | **No.** Keep it for the whole data set. | The NI 9230 maximum. The usable band is about 0–5.8 kHz, which covers the accelerometer's range, bearing and resonance content (e.g. the ~3 kHz peak on this rig) and current harmonics. Lower rates only lose information; a 60 s run is ~12 MB. Mixing rates within a data set complicates every later analysis. |
| Accel **input** | ai0 | **Only if you move the cable.** | Confirmed with the motor running. The *alive* check flags a wrong input. |
| Accel **sensitivity** | 200 mV/g (nominal) | **Yes, once:** the value on the Kistler 8702B25 (S/N 5460544) calibration certificate. Then never again. | Scales volts to g. A wrong value scales every amplitude, but the waveform shape and the frequencies stay correct. Runs recorded before the fix can be corrected later: `g_true = g_saved × sens_used / sens_true`. |
| Accel **range** | ±25 g | **No.** | Matches the sensor's ±25 g rating (25 g × 200 mV/g = 5 V). The NI 9230 has one ±30 V hardware range, so this mostly sets the clipping warning. Resolution does not change. |
| Accel **IEPE** | 4 mA | **Never turn off** for the accelerometer. | The Kistler is an IEPE sensor and needs this current to work. Without it the channel reads nothing. 4 mA is the only IEPE level the NI 9230 offers. |
| Accel **coupling** | AC | **No.** | IEPE sensors sit on a ~10 V DC bias; AC coupling removes it. DC would add a large offset and waste range. |
| Clamp **input** | ai2 | **Only if you move the cable.** | Confirmed with the motor running; ai1 is empty. |
| Clamp **sensitivity** | 100 mV/A | **Check once that it matches the switch on the clamp.** If the clamp is on its 10 mV/A range, enter 10. Change it whenever you flip the switch. | Scales volts to amps. The *current level* check flags a 10× mismatch: with the motor's ~1.7 A, a wrong setting shows ~17 A or ~0.17 A. |
| Clamp **range** | ±5 V | **No.** | At 100 mV/A that is ±50 A, far above this motor (FLA ~2.2 A). |
| Clamp **IEPE** | off | **Never turn on.** | The clamp outputs a plain voltage. IEPE current would bias its output and can upset it. |
| Clamp **coupling** | DC | **No.** | Keeps the true waveform. The analysis removes the mean anyway. |
| **Location** text | as configured | **When you move a sensor.** | Stored in every file and shown in the plots. Also write the change in the run notes. |

Settings only in `config.toml` (edit the file, then restart the console):

| Setting | Change it? | Notes |
|---|---|---|
| `device`, `sim_device` | Only if NI MAX renames the module. | `cDAQ1Mod1` = the NI 9230 in the cDAQ-9191. |
| `expected_module` | No. | Gives a warning if a different module is plugged in. |
| `[motor] poles = 2` | No, unless the motor is replaced. | Marathon 1/2 HP, 3450 rpm → 2-pole. Used to predict the shaft speed. |
| `[motor] current_rms_A = [0.3, 4.0]` | Widen it if heavy-load runs warn while the current looks right. | Only a plausibility band for the check. It does not affect the data. |
| `[motor] line_hz = 60` | No (US mains). | Used by the line-pickup check. |
| `[temp] enabled = false` | Only if you add a thermocouple module. | The cDAQ-9191 has one slot, already used by the NI 9230. |
| Adding a channel (e.g. a 2nd accelerometer on ai1) | When you add a sensor. | Copy an `[[vib.channels]]` block, set `name` / `physical` / `sensitivity`, save, restart. Note the change in the run notes: files before and after it have different channels. |

### Before collecting the real data set
- [ ] Enter the accelerometer sensitivity from the calibration certificate, then **Apply & save**.
- [ ] Look at the clamp's range switch and make the mV/A match. **Apply & save** if you changed it.
- [ ] With the motor running, every live check is ✓ or *info*.
- [ ] Record a 10 s test run, open it in Review, then flag it `bad` so it is easy to exclude.

## 5. Per-run settings

| Field | Recommendation |
|---|---|
| **Duration** | **60 s** as the default. It gives 14 averaged 8 s spectra (0.125 Hz resolution), enough for slip and broken-bar sidebands. Use the same duration for all runs in a data set. Rather than one very long run, record **3 or more repeats** per condition (tag `r1`, `r2`, …). |
| **Settle** | Leave it at **2 s**. See *What "settle" means* below. |
| **Drive Hz** | The **target** Hz, matched with MEASURED, not the Lenze display (⚠ at top). It goes into the filename and the metadata, and the checks compare it with the measured current. |
| **Fault / load / tag / notes** | Use consistent spelling. The fault buttons help. Labels end up in the filename, so keep them short. |

### What "settle" means

**Settle is the number of seconds of DAQ data thrown away right after the DAQ stream starts.**
It is about the measurement electronics, **not** the motor.

When the DAQ task starts, two things need a moment before the samples are trustworthy:
- **IEPE / AC coupling.** The NI 9230 switches on the 4 mA current that powers the accelerometer.
  The sensor's output rises to its ~10 V bias, and the AC-coupling filter that removes this bias
  needs a few seconds to settle. Until then the accelerometer trace drifts or shows a decaying
  offset.
- **ADC filter warm-up.** The converter's digital filter needs a short moment after the start.

How the console applies it:

| Situation | What Settle does |
|---|---|
| Stream already running (the normal case, plots live) | **Nothing.** The stream settled long ago, so recording starts immediately. |
| Stream just (re)started (Start stream, Reconnect, config Apply) | Record waits until the stream has run for *settle* seconds, then starts saving. |
| Record pressed while the stream is off | The stream starts and at least **5 s** are discarded, whatever the field says. |
| `acquire.py --settle` | Same meaning: every CLI run starts a fresh task, so the settle time is always discarded. |

The saved file starts **after** the settle period. The requested duration is fully kept.

Settle does **not** wait for the motor. After changing the drive speed, wait about 30 s for speed
and current to become steady **before** you press Record (§2, step 1).

## 6. Signal checks: what they mean and what to do

| Check | ✗ / ! means | Do this |
|---|---|---|
| **signal present** | Almost nothing on the channel. | Motor off? Cable unplugged? Sensor on a different input (try the other ai)? |
| **range / clipping** | The signal reaches the edge of the input range. | Accel: hits or loose parts? Clamp: check the mV/A setting. |
| **stuck value** | The signal stopped changing. | Cable or connector problem; reconnect. |
| **DC offset** (accel) | An AC-coupled input is off-centre. | Wait a few seconds after starting, or check the sensor cable. |
| **fundamental = drive** | The current's frequency differs from the drive Hz you typed. | A typo in Drive Hz, a speed change still in progress, or the clamp is not on a motor lead. |
| **current level** | The current RMS is outside the plausible band. | "~10× too small/large" = wrong mV/A. Otherwise an unusual load: fine if expected, note it. |
| **line pickup** | 60 Hz dominates while the drive runs at another speed. | Clamp on a mains lead instead of a motor lead, or a ground loop / cable near power wiring. |
| **shaft 1x / slip** | No clear running-speed peak, or the slip is implausible. | Accelerometer loose or not on the motor, wrong drive Hz, or the motor is not at speed yet. |
| **integrity** (files) | Fewer samples than requested. | The run was stopped early. Record it again if it should be a full run. |
| *info* lines | Not pass/fail. | 2×/1× (high: misalignment?), f(1±2s) sidebands (high: broken bar?), strongest component. Hints only, not a diagnosis. |

## 7. Review tab
- Lists every `.h5` file under `data\`, including ones recorded with `acquire.py`. Filter by
  text or flag.
- Opening a run shows its metadata and the check report on the whole file. For each channel you
  get the full-length waveform (drag to zoom into full-rate data; double-click to go back), the
  averaged spectrum with markers, and a spectrogram.
- **compare with** overlays another run's spectrum, e.g. normal against unbalance at the same Hz.
- **keep / bad** flags are stored in `data\run_flags.json`.
- **delete run** removes a run (see *Deleting runs* below).

### Deleting runs

You can drop a recording in three places:

| Where | Button | Use it when |
|---|---|---|
| Record card, **while recording** | **✗ Discard this recording** | You notice a problem mid-run (wrong label, sensor bumped, drive changed). It stops now and deletes the file. |
| Record card, **after saving** | **Delete this run** | The saved-file checks show a ✗, or you know the run is bad. |
| Review tab, any run | **delete run** | Cleaning up later. |

- Every delete needs a **second click within 4 s**; the button turns red and says "click again".
- **Nothing is erased.** The file moves to `data\_trash\<date>\…` and its row is removed from
  `runs_index.csv`. To undo, move the file back in Explorer: it appears in the list again (without
  an index row). Empty `data\_trash` yourself when you are sure.
- If the deleted run was part of the **test matrix**, it goes back on the list as "to do", so you
  can record it again.
- For "keep it, but exclude it from analysis", flag it **bad** instead of deleting it.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Red banner "reserved by another computer" | Press **Take over reservation**. Only do this if nobody else is using the rig from another PC. |
| "in use by another task" | Another program has the module (`scope.py`, `acquire.py`, NI MAX test panel). Close it, then press **Reconnect**. |
| "device is not reachable" | Check the chassis power and Ethernet, and that the PC has a 169.254.x.x address. The console retries every 3 s. |
| Page shows "server offline" | The console was closed. Run `python daq\console\server.py` again. |
| Plots frozen | **Freeze display** is on (button says *Resume*), or the stream is off. |

## 9. Files written
- `data\YYYY-MM-DD\<time>_<fault>_<Hz>Hz[_<load>][_<tag>].h5`: samples in g and A, plus all
  metadata and the configuration used. The format is identical to `acquire.py`.
- `data\runs_index.csv`: one row per run.
- `data\run_flags.json`: keep/bad flags from the Review tab.
- `data\_trash\`: deleted and discarded runs (not shown in the console).
