# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to Calendar Versioning (CalVer).

## [v26.08.24.51] - 2026-08-24

### Added
- **Plug-in Controls**: The external VST3/AU plug-in can now be given its parameters instead of running on whatever preset happened to be its factory default. Choosing a plug-in lists its own controls under the field — slider, checkbox or menu according to the parameter's type — in the plug-in's own units (dB, %, on/off), not the 0–1 raw value underneath. Only touched controls are saved; the rest stay at the plug-in's defaults, and **Plug-in defaults** clears them. The settings belong to the plug-in they were read from, so choosing another plug-in clears them: the same name in another plug-in would land on the wrong control. An unknown or out-of-range name is skipped rather than raised, because settings are inherited from the previous episode, whose plug-in may have been a different one.

## [v26.08.24.50] - 2026-08-24

### Added
- **Settings in the Export Name**: The export is now named after the controls it was made with — the rhythm preset always, plus any control that deviates from its default (`episode-cut custom 3s louder stay audio.fcpxml`). In Final Cut's browser the file name is the only thing separating one rough cut from the next. New **Settings in the file name** checkbox in the Project section turns it off; the path shown in the interface follows the controls live.
- **Settings Embedded in the Exported FCPXML**: `<sequence>` now carries a translated one-line `<note>` (version, rhythm, shot lengths, rules, whether the microphones were processed) and a `<metadata>` block with one `<md>` per control plus the complete settings JSON under `fi.autoraffkat.settings`. A cut is reproducible from the file alone, on a machine that never saw the settings file.

### Fixed
- **Rhythm Preset and Hang Never Reached the Server**: `/api/settings` dropped `rhythm` and `hang` from the incoming payload, so the saved settings kept the defaults no matter what was chosen in the interface.
- **Hang (L-cut) Did Nothing**: `decide.py` never read `g.hang`. The slider, the rhythm presets and the documentation all promised an L-cut that was not implemented. The hang is now a floor on the cut point — the outgoing speaker's face stays on screen for that long after their speech ceases, so a fast handover becomes an L-cut while a real pause still gets the J-cut lead. It does not apply during overlapping speech, where the outgoing speaker has not stopped.
- **Brief Overlap Could Cut to a Silent Speaker**: in backchannelling the picture went to the loudest *microphone* rather than the loudest speaker who was actually talking. With three or more people, a hot mic or a large gain on a silent participant took the shot.
- **Reaction Shot Could Cut to an Angle That Does Not Exist**: the co-host chosen for a reaction shot was not checked against the close-up's availability, so in a multicam the cut could land on an angle missing from that part. The candidate must now be available for the whole insert; otherwise the break goes wide as before.
- **Programme Edges Were Always Cut at the Slowest Tempo**: the 1/f tempo window was zero-padded, so the first and last 22 seconds measured as the slowest possible material and stretched the shortest shot by a fifth regardless of content. The window now slides inward at the edges instead of being padded with zeros, so the start and end are measured against the same span as the middle, and the mean-rate epsilon no longer skews sparse material by several percent.

### Performance
- The 1/f tempo window is a summed-area lookup instead of a direct convolution: a two-hour programme decides in 24 ms instead of 90 ms, of which the tempo is now 9 ms instead of 75 ms. The decision layer runs on every slider movement, so this is the interface's response time.

### Changed
- Export version numbering now runs within one set of settings: a cut made with different controls is a new file, not the next version of the same one.

## [v26.08.22.49] - 2026-08-22

### Documentation
- Updated `README.md` and `README.fi.md` with headless remote execution guide, cross-platform plugin directories, rhythm presets, L/J cuts, and reaction shots.
- Updated `DESIGN.md` and `DESIGN.fi.md` architecture notes for 1/f tempo waves and breath-snapped long-take punctuation.

## [v26.08.22.48] - 2026-08-22

### Added
- **Cross-Platform VST3 Paths**: Added native Linux (`/usr/lib/vst3`, `~/.vst3`, etc.) and Windows (`CommonProgramFiles/VST3`) plugin directory discovery to `audio/chain.py` for headless remote servers.
- **1/f Dynamic Tempo Modulation**: Local conversation density / turn-taking rate dynamically modulates pacing and dwell times over rolling 45s windows in `decide.py`.
- **Optional Reaction Shots**: Added `reaction` rule to long-take breaking options (`LONGTAKE_REACTION`), allowing cuts to a silent co-host's close-up during monologues while keeping Wide as the safe default.
- **Rhythm & Pacing Engine**: Added macro editing presets (`broadcast`, `mellow`, `hectic`, `custom`) for rhythm control.
- **L-Cut & J-Cut Support**: Added asymmetric lead (J-cut anticipation) and hang (L-cut reaction) controls to `decide.py` and UI.
- **Pause-Snapped Monologue Punctuation**: Long continuous monologues now snap transitions at natural speech pauses or acoustic breath/energy dips.
- **Rhythm UI Controls**: Added profile selection radio group and hang slider in both Finnish and English.
