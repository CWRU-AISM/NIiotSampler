# Induction motor (IM) fault simulator: data acquisition equipment

> Compiled 2026-10-12, updated 2026-10-15 after the on-site connection test.
> Sources: on-site photos and nameplates; accelerometer specs from the Kistler website.
> Items marked "to confirm" still need checking on site. Setup instructions are in [GUIDEBOOK.md](GUIDEBOOK.md).

---

## 1. System overview

The rig is a SpectraQuest machinery fault simulator with an acrylic guard, asset tag UNIV. OF CT
W47539. A Lenze variable-frequency drive (VFD) runs a 3-phase induction motor. The motor drives a
shaft and a perforated rotor disk through a flexible coupling. Data is acquired with **one NI
cDAQ-9191**, which measures vibration and motor current.

```
VFD (Lenze SMVector) ──► 3-phase IM (Marathon 1/2 HP) ──► coupling ──► shaft + rotor disk
                               │             │              │
                        accelerometer   thermocouple   current clamp (on one motor supply lead)
                               │       (connection to       │
                               │        confirm, §5.3)      │
                               └──────► cDAQ-9191 ◄─────────┘
                                   (BNC/IEPE module)
                                          │
                              Ethernet / Wi-Fi ──► switch / fibre converter ──► PC
```

---

## 2. Device under test: induction motor

**SpectraQuest label:** AC Motor with Balanced Rotor, SpectraQuest, Inc., Richmond, VA.

**Marathon Electric nameplate:**

| Item | 60 Hz | 50 Hz |
|---|---|---|
| Catalogue no. (CAT NO.) | D391 | |
| Model | 56T34F5301J P | |
| Frame (FR) | 56C | |
| Phases (PH) | 3 | |
| Power | 1/2 HP (0.37 kW) | 1/3 HP (0.25 kW) |
| Rated speed | 3450 rpm | 2850 rpm |
| Voltage | 208-230 / 460 V | 190 / 380 V |
| Full-load current (FLA) | 2-2.2 / 1.1 A | 1.85 / 0.92 A |
| Power factor cos φ | 0.697 | 0.66 |
| Service factor (SF) | 1.15 | 1.15 |

**Other nameplate information:**
- Protection: IP43; enclosure TEFC (totally enclosed, fan cooled)
- Insulation class F; design B; code P; type TS
- Ambient 40 °C; continuous duty
- Bearings: permanently lubricated ball bearings
- Wiring: 9 leads (T1–T9), low-voltage (parallel) or high-voltage (series) connection

**Derived parameters:**
- 3450 rpm at 60 Hz means a **2-pole motor**: synchronous speed 3600 rpm, rated slip about 4.2 %.
- The VFD showed 41 (Hz). That gives a synchronous speed of about 2460 rpm, an actual speed of
  about 2400 rpm, and a shaft frequency of about 40 Hz.

---

## 3. Variable-frequency drive

| Item | Details |
|---|---|
| Make / model | Lenze AC Tech **SMVector** (with on-board EPM parameter memory module) |
| Display when photographed | 41 (output frequency, Hz) |
| Keypad | ▲/▼ set frequency, RUN start, STOP stop, R/F reverse, M menu |
| Safety | Capacitors stay charged for about 180 s after power-off; wait before servicing |

> Note: the VFD output is PWM. The current signal contains the carrier frequency and its
> harmonics, which matters for MCSA (motor current signature analysis).

---

## 4. Data acquisition hardware: NI cDAQ-9191 (one unit)

The cDAQ-9191 is a single-slot NI CompactDAQ chassis that connects by Ethernet or 802.11 Wi-Fi.
It has an RJ45 port (latching), an SMA antenna connector and a power connector. The back carries a
hazardous-location rating (Class I Div 2), and the operating temperature is 0–55 °C.

| Item | Value |
|---|---|
| P/N | 150227E-21L |
| S/N | **1D92246** |
| Ethernet MAC | 00:80:2F:22:86:48 |
| Hostname | cDAQ9191-1D92246 |
| Ethernet IP (2026-10-15) | 169.254.27.243 (link-local, no DHCP on the switch) |
| Stored Wi-Fi IP | 172.19.14.90 (did not answer from eduroam on 2026-10-15) |
| Module in the slot | BNC module, carrying the accelerometer and current clamp. Probably an NI 9234 IEPE vibration module (**to confirm**) |
| Driver | Needs NI-DAQmx on **Windows**. On Linux the chassis is found but cannot be reserved (error -201427). |

> To do: read the module model from the label on its side, or from NI MAX once the chassis is reserved.

---

## 5. Sensors

### 5.1 Accelerometer: Kistler 8702B25

| Item | Specification |
|---|---|
| Serial no. | SN 5460544 |
| Type | Single-axis K-Shear quartz shear, IEPE voltage output |
| Range | ±25 g |
| Nominal sensitivity | **200 mV/g** (use the value on this sensor's calibration certificate) |
| Frequency response (±5 %) | 1 Hz – 8 kHz |
| Connector | 10-32, side exit |
| Mounting | Stud, adhesive or magnet |
| Current location | Motor housing near the drive end, radial |

DAQ settings: IEPE mode with excitation current on, AC coupling.

### 5.2 Current clamp

- Grey body with red jaws, clamped on one motor supply lead. Its output cable goes to a BNC input
  on the cDAQ-9191.
- Model and range **to confirm**. Common ratings are 10 mV/A or 100 mV/A; check the label or range
  switch on the clamp.

DAQ settings: plain voltage channel, **IEPE off**, then scale to amps by the clamp ratio.

### 5.3 Thermocouple

- Taped to the motor housing surface to measure housing temperature.
- Type **to confirm**. K is common; check the wire colours or plug marking.
- A thermocouple needs a thermocouple module (e.g. NI 9211). The cDAQ-9191's only slot holds the
  BNC module, so where the thermocouple is connected is **to confirm**.

DAQ settings: correct thermocouple type, cold-junction compensation (CJC) on, 1–10 Hz sampling is enough.

---

## 6. Network and power (equipment on the floor)

- TRENDnet GREENnet Ethernet switch
- TRENDnet fibre media converter (optical-electrical), with an orange multimode fibre patch cord
- Several power adapters: for the cDAQ-9191, the switch and the media converter
- Confirmed 2026-10-15: the cDAQ-9191 connects to the switch by Ethernet, and a PC plugged into the
  same switch reaches it (link-local addresses). The role of the fibre converter (link to the lab
  network?) is **to confirm**.

---

## 7. Acquisition plan

**Software:** install the NI-DAQmx driver (includes NI MAX) on a **Windows** PC, then use one of:
- LabVIEW: ask whether the lab has existing VIs
- NI FlexLogger: no programming
- Python `nidaqmx` package: free, convenient for later analysis (this repository)
- MATLAB Data Acquisition Toolbox

**Connection steps:**
1. Connect the PC to the same switch or subnet.
2. In NI MAX, use *Find Network NI-DAQmx Devices* to add the cDAQ-9191 by serial number or IP, then reserve it.
3. Check each channel's waveform with a Test Panel.

**Recommended sample rates:**

| Signal | Rate | Notes |
|---|---|---|
| Vibration | 25.6 kS/s | Accelerometer goes up to 8 kHz; bearing-fault signatures are in the high band |
| Current | 10–25.6 kS/s | Same module as vibration, so same rate |
| Temperature | 1–10 S/s | Changes slowly |

**Record with every run:** VFD frequency, load condition, fault type (normal, unbalance,
misalignment, bearing fault, ...), duration, and sensor location and orientation.

---

## 8. Open items

- [ ] Model of the module in the cDAQ-9191
- [ ] Current clamp model and mV/A range
- [ ] Thermocouple type, and where it is connected
- [ ] Actual accelerometer sensitivity from the calibration certificate
- [x] IP address and network topology: link-local 169.254.27.243 via the TRENDnet switch (2026-10-15)
- [ ] Does the lab have existing LabVIEW programs or an acquisition protocol?
