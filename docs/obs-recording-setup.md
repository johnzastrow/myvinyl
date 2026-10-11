# OBS Recording Setup

How the screen-recording setup for YouTube session videos is built on this laptop: hardware,
OBS configuration, audio plugins, and the post-processing script. Use it to understand, change, or
rebuild the setup. For day-to-day operation, see the one-page
[OBS Recording Cheat Sheet](obs-recording-cheatsheet.pdf).

Configured 2026-10-11.

---

## 1. Overview

| Item | Value |
|---|---|
| What gets recorded | The full laptop screen (Windows Terminal, Chrome, or whatever is in front) plus the headset mic |
| Output | 1920x1080, 30 fps, H.264 MP4, mic-only audio |
| Recordings folder | `C:\Users\br8kw\Videos\OBS` |
| Start / stop | `Alt+End` |
| Pause / resume | `Alt+Home` |
| Mute mic | `Ctrl+Alt+F8` |
| Optional cleanup | `uv run tools/clean_audio.py "<recording>.mp4"` writes `<recording>-clean.mp4` |

Signal path:

```
Screen (1920x1200) --> Display Capture --crop taskbar--> scale to 1920x1080 --+
                                                                               +--> QuickSync H.264 --> Hybrid MP4
Logitech USB mic --> ReaEQ (hum) --> Noise Suppression --> Gain --> Compressor --> Limiter --> AAC 192k --+
```

---

## 2. Hardware

| Component | Detail | Why it matters |
|---|---|---|
| CPU | Intel Core i5-1245U (10 cores / 12 threads, low-power U-series) | Too weak to encode video in software while you work, so encoding goes to the graphics chip |
| GPU | Intel Iris Xe (integrated), driver 32.0.101.7085 | Provides the **QuickSync** hardware encoder. No NVIDIA card, so NVENC isn't available (the "NVENC not supported" line in the OBS log is expected) |
| RAM | 24 GB | Plenty |
| Display | Built-in Lenovo panel (`LEN403D`), 1920x1200 (16:10), 60 Hz, 150% scaling | 16:10 doesn't match YouTube's 16:9; the taskbar is 72 physical pixels tall at 150% |
| Microphone | Logitech USB Headset (`Microphone (Logitech USB Headset)`) | Windows input level is already at 100% (+11 dB hardware maximum), yet speech is still quiet |
| Speakers | Logitech USB Headset | Desktop audio is not recorded |
| Power | Charger connected while recording | Likely source of the 60 Hz hum (see section 6) |

---

## 3. Software and file locations

| Software | Version | Location |
|---|---|---|
| OBS Studio | 32.2.2 | `C:\Program Files\obs-studio\bin\64bit\obs64.exe` |
| ReaPlugs (Cockos VST effects) | 2.36 x64 | `C:\Program Files\VSTPlugins\ReaPlugs\` |
| ShareX | (installed separately) | Owns plain `Home`, `End`, `Ctrl+Home`, `Ctrl+PageUp`, `Ctrl+PageDown` |
| uv + Python | for `tools/clean_audio.py` | Dependencies (PyAV, NumPy) are pulled automatically by `uv run` |

ReaPlugs source: <https://www.reaper.fm/reaplugs/>. The installer `reaplugs236_x64-install.exe` is
signed by Cockos Incorporated, SHA-256
`1654F1F78D746FC87A6F6D078A8F928EA61C7B49997B1F2C07DFC870F8FA6C10`. Install to the default folder
so OBS finds the plugins automatically.

OBS configuration lives in `%APPDATA%\obs-studio\`:

| File | Contents |
|---|---|
| `user.ini` | Global settings; selects the active profile and scene collection |
| `basic\profiles\YouTube\basic.ini` | Video, output, audio and hotkey settings |
| `basic\profiles\YouTube\recordEncoder.json` | QuickSync encoder settings |
| `basic\scenes\YouTube_Recording.json` | Scene, Display Capture source, Mic device and its filters (including the saved ReaEQ state) |
| `logs\` | One log per OBS launch; the first place to look when something is wrong |

Backups:

- `%APPDATA%\obs-studio-backup-2026-10-11\`: the untouched original configuration (default
  "Untitled" profile and scene collection, still present and selectable from the OBS menus).
- `basic\scenes\YouTube_Recording.json.pre-reaeq`: the scene collection before the ReaEQ and Gain
  filters were added.

---

## 4. OBS configuration

Profile **YouTube**, scene collection **YouTube Recording**.

### 4.1 Video

| Setting | Value | Reason |
|---|---|---|
| Base (canvas) resolution | 1920x1080 | Standard 16:9 YouTube frame |
| Output resolution | 1920x1080 | No extra rescale |
| FPS | 30 | Plenty for coding videos; halves encoder load compared with 60 |
| Downscale filter | Lanczos | Sharpest text when scaling |
| Color | NV12, Rec. 709, partial range | YouTube standard |

### 4.2 Scene and capture

One scene, **Screen**, with one locked source, **Display** (Display Capture, DXGI method,
cursor captured). Because only the whole screen is captured, switching apps with `Alt+Tab` needs no
scene switching.

The 1920x1200 screen is fitted into the 16:9 canvas as follows:

1. Crop the bottom 72 px (the taskbar), leaving 1920x1128.
2. Scale to fit the 1920x1080 canvas (bounds type "scale to inner bounds", centered). The image
   becomes about 1838x1080, which leaves thin black bars at the sides.

A true 16:9 crop was rejected because it would have cut the bottom of maximized windows, which is
where the Claude Code prompt sits. The 4% downscale softens text very slightly.

Display Capture records everything on screen, including notification pop-ups and the OBS window
itself. Minimize OBS and turn on Do not disturb before recording.

### 4.3 Recording output (Settings > Output, Advanced mode)

| Setting | Value | Reason |
|---|---|---|
| Format | Hybrid MP4 | Stays playable if OBS or Windows crashes; uploads to YouTube without remuxing |
| Path | `C:\Users\br8kw\Videos\OBS` | |
| File name | `%CCYY-%MM-%DD %hh-%mm-%ss` | Sorts by date |
| Video encoder | QuickSync H.264 (`obs_qsv11_v2`) | Runs on the Iris Xe, leaving the CPU free |
| Rate control | CQP 20 | Constant quality; keeps terminal text crisp and uses little space on mostly static screens |
| Target usage | TU4 (balanced) | Use TU7 (speed) if the "Encoding overloaded" warning appears |
| Profile / keyframes / B-frames | High / 2 s / 3 | |
| Audio | FFmpeg AAC, 192 kbps, track 1 only | |

Encoder gotcha: in OBS 32 the QuickSync CQP value is stored as `qpi`, `qpp` and `qpb` in
`recordEncoder.json`. A plain `cqp` key is ignored and OBS falls back to 23. The file contains all
four keys, set to 20. The `[qsv encoder ...] settings:` block in the log shows the values actually
used when a recording starts.

Measured size: about 0.5-1 MB per 6 seconds of light activity, so expect roughly 0.5-1.5 GB per
hour.

### 4.4 Audio

- Sample rate 48 kHz, stereo.
- **Desktop audio is disabled** (there's no Desktop Audio device in the scene collection), so
  notification sounds and browser audio aren't recorded.
- **Mic** is the only audio source, bound to the Logitech headset by device ID
  `{0.0.1.00000000}.{9AFD47CA-18C5-4A2C-B6E0-9A17FB9A7F2D}`. If the headset is replaced, reselect the
  new device in OBS (Settings > Audio, or right-click Mic > Properties).

Mic filter chain, in order (Audio Mixer > Mic > three-dot menu > Filters):

| # | Filter | Settings | Purpose |
|---|---|---|---|
| 1 | Hum Removal (ReaEQ) | See table below | Removes the 60 Hz mains hum and its harmonics |
| 2 | Noise Suppression | RNNoise | Removes hiss, fans and keyboard noise |
| 3 | Gain | +16 dB | Brings quiet headset speech up to a usable level (the Windows input is already at maximum) |
| 4 | Compressor | Ratio 4:1, threshold -18 dB, attack 6 ms, release 60 ms | Evens out loud and soft speech |
| 5 | Limiter | Threshold -1 dB, release 60 ms | Prevents clipping |

Order matters. The hum is removed before Gain amplifies it, and the Compressor only works properly
once Gain has raised the voice above its -18 dB threshold.

ReaEQ bands (set in ReaEQ's own window: Filters > Hum Removal (ReaEQ) > Open Plug-in Interface):

| Band | Type | Frequency | Bandwidth |
|---|---|---|---|
| 1 | High Pass | 85 Hz | default |
| 2 | High Pass | 85 Hz | default |
| 3 | Notch | 180 Hz | 0.06 oct |
| 4 | Notch | 300 Hz | 0.06 oct |

Two high-pass bands stacked at 85 Hz give a steep cut that removes the 60 Hz fundamental without
thinning the voice. The notches remove the strongest harmonics. ReaEQ stores its state as a binary
block (`chunk_data`) in the scene collection, so it can only be edited in its own window, not by
editing the JSON by hand.

### 4.5 Hotkeys

| Action | Keys | Notes |
|---|---|---|
| Start / stop recording | `Alt+End` | One key bound to both Start and Stop, so it toggles |
| Pause / resume | `Alt+Home` | Toggles. Chrome also uses `Alt+Home` (opens the home page), so pause while the terminal is in front |
| Mute / unmute mic | `Ctrl+Alt+F8` | May need `Fn` held on this keyboard |

History: the hotkeys were first `Ctrl+Alt+F9`/`F10`, then plain `End`/`Home`. The plain keys
clashed with ShareX, which uses them. OBS hotkeys are global (`HotkeyFocusType=NeverDisableHotkeys`)
and OBS doesn't swallow the key, so the active app always receives it as well.

---

## 5. Post-processing: `tools/clean_audio.py`

A second line of defense for recordings that still have hum or low level, and the fix for anything
recorded before the live filters existed.

```
uv run tools/clean_audio.py "C:\Users\br8kw\Videos\OBS\2026-10-11 11-21-33.mp4" [more files...]
```

- Writes `<name>-clean.mp4` next to each input and never modifies the original.
- Copies the video stream bit-for-bit; only the audio is re-encoded (AAC 192 kbps).
- Filters (ffmpeg filters via PyAV's bundled libavfilter, so no ffmpeg install is needed): two
  85 Hz high-passes, narrow notches at 60, 120, ... 420 Hz, then `loudnorm` to -16 LUFS with
  peaks capped at -1.5 dBTP.
- Prints how much hum and level changed. For mains at 50 Hz (outside North America), change `HUM_HZ`.

---

## 6. Audio measurements and the hum problem

Recordings were analyzed with an FFT (frequency spectrum) and an integrated-loudness (LUFS)
meter. "Hum vs voice" compares energy at 60/180/300 Hz with the 100-4000 Hz voice band; lower is
better.

| Recording | Setup | Hum vs voice | Loudness | Peak |
|---|---|---|---|---|
| 11-21-33 | Original filters only | -5.1 dB | -35.0 LUFS | -11.7 dBFS |
| 11-21-33-clean | After `clean_audio.py` | -27.1 dB | -19.3 LUFS | -1.3 dBFS |
| 11-43-09 | ReaEQ + Gain +12 dB live | -19.6 dB | -22.3 LUFS | -2.8 dBFS |

After that test, Gain was raised from +12 to +16 dB to bring live loudness closer to -18 LUFS.
YouTube normalizes playback to about -14 LUFS.

Findings:

- The hum is **60 Hz mains hum with odd harmonics (180, 300 Hz)**. That pattern points to an
  electrical problem, not acoustic room noise, so noise suppression alone can't remove it.
- The laptop has been **on the charger for every recording**. A USB headset on a charging laptop
  is a classic ground-loop hum source. **Not yet tested:** record 15 seconds on battery and compare.
  If the hum disappears, the real fix is hardware (record on battery, try another USB port or a
  grounded outlet, or use a USB ground-loop isolator), and the filters become a safety net.
- The headset mic is quiet even at 100% Windows level, so most of the level comes from the OBS Gain
  filter.

---

## 7. Common changes

Close OBS before editing any file in `%APPDATA%\obs-studio`. OBS rewrites its configuration on
exit and would overwrite your edits. Restarting OBS from the command line must use the `bin\64bit`
folder as the working directory.

| Change | How |
|---|---|
| Voice too quiet or too loud | Mic > Filters > Gain. Aim for meter peaks in the yellow zone (-20 to -9 dB) |
| Hum still audible | Widen the ReaEQ notch bandwidth (for example 0.06 to 0.15 oct), or add a Notch at 60 Hz |
| New headset | Settings > Audio > Mic/Auxiliary Audio, or right-click Mic > Properties |
| External monitor | Display > Properties > choose the display; re-check crop and fit (right-click > Transform) |
| 60 fps (smoother scrolling) | Settings > Video > Common FPS 60; watch for "Encoding overloaded" |
| Hotkeys | Settings > Hotkeys, search "Recording" |
| Preview black | Display > Properties > Capture Method: "Windows 10 (1903 and up)" |

## 8. Rebuilding from scratch

1. Install OBS Studio and ReaPlugs x64 (default folders).
2. Settings > Output: Advanced mode, then the values in 4.3. Settings > Video: values in 4.1.
   Settings > Audio: disable Desktop Audio and set Mic to the headset.
3. Add Display Capture, set Crop Bottom = 72 (Alt-drag or Transform > Edit Transform), then
   Transform > Fit to screen.
4. Add the mic filters in the order and with the values from 4.4, including the ReaEQ bands.
5. Set the hotkeys from 4.5.
6. Make a 20-second test recording and check it with `tools/clean_audio.py` or by ear.

Or restore the files listed in section 3 from a backup of `%APPDATA%\obs-studio`.
