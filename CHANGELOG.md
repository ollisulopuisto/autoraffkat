# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to Calendar Versioning (CalVer).

## [v26.08.24.60] - 2026-08-24

### Fixed
- **Final Cut Played the Raw Audio Because `uid` Beats `src`**: the exported video measured −43 LUFS, which is raw-microphone level. Cross-correlated against the sources it was raw: +0.958 to the untouched file, +0.883 to the processed one. Final Cut identifies media by `uid`, not by path. Redirecting an asset's `src` left its `uid` untouched, so the processed file claimed to be the same media as the original — and the raw twin, being a copy, carried that same `uid` *and* a `<bookmark>`. Final Cut collapsed the pair and kept the raw. Every "processed" angle had been playing untouched audio, and nothing said so. A redirected asset now drops its `uid` as well as its `<bookmark>`; the twin keeps both, because it really is the original media.

### Changed
- **The Channel Strip Was Throwing Away 9–12 dB**: `peak_guard` attenuated the whole file by whatever its single loudest sample demanded. After normalisation the peaks sat at +8 to +11 dBFS, so the static cut was enormous: −14.00 LUFS became −25.74 (Nyman) and −22.94 (Wancke). That one line put every file 9–12 dB under target, made the speakers' balance depend on whose loudest transient was loudest, and reduced the program trim to noise. The ceiling is now a look-ahead limiter that touches only the peaks, and the level is re-measured after it so speakers land together. Measured on the same excerpts: **−14.95 and −16.02 LUFS with peaks at exactly −1.00 dBFS**.
- **Less Distortion at the Same Loudness**: the peak compressor attacked in 2 ms, which is inside the pitch period of a 110 Hz voice — that is waveform modulation, not level control. Measured on a 110 Hz sine at −6 dBFS: 2 ms gives −30.9 dB THD, 40 ms gives −36.1 dB. The attack is now 15 ms, longer than any speech pitch period. The two compressors run in **parallel** with the dry signal rather than in series, so quiet passages come up while transients survive untouched, and a de-esser sits ahead of them because the restoration plug-in adds +4 to +5.7 dB across 3–20 kHz and a single sibilant was pulling whole sentences down.

## [v26.08.24.59] - 2026-08-24

### Fixed
- **Per-Speaker Roles Now Survive the Import**: `audioRole` on a multicam angle's clip is the obvious way to give it a role, and Final Cut ignores it — the angle stays on the default subrole `Dialogue-1`, which is where it puts every dialogue clip. The working mechanism is `<audio-channel-source>`, which names the component channel by channel. Established by importing both versions and looking: `audioRole` alone shows "Dialogue-1", `audio-channel-source` shows "Nyman". Both are written now, since that is how it was tested and the attribute costs nothing. The Audio Configuration inspector reads "Nyman, Wancke" instead of "Dialogue-1", which means per-speaker faders and role-based stem exports.

## [v26.08.24.58] - 2026-08-24

### Fixed
- **The Raw Twin Still Played: `srcEnable` Beats `active`**: the previous release made the angle carry the subrole its `mc-source` names, which was a real mismatch — and not the one keeping the twin audible. Final Cut never writes `srcEnable="audio"` together with `active="0"`. In its own multicams an angle with audio on is `srcEnable="audio"` with `active="1"`, and an angle with audio off is `srcEnable="none"` (or `"video"`) with `active="0"`. Our combination is a contradiction, and Final Cut resolves it in favour of `srcEnable`: the angle plays, whatever the role says. The twin's source is now `srcEnable="none"`, which still lists it in Audio Configuration — unticked, ready to switch on when it is wanted.

## [v26.08.24.57] - 2026-08-24

### Fixed
- **The Raw Twin Was Not Muted, and Played Underneath the Processed Track**: the multicam angles are copied from the source, so their audio keeps Final Cut's default subrole `dialogue.dialogue-1`. The `<mc-source>` written beside them named a per-speaker subrole instead — `dialogue.Nyman`, `dialogue.Nyman raw` — which the angle did not carry. Nothing failed: the XML validated against the DTD, the import succeeded, and `active="0"` simply had no role to apply to. So the untouched twin played summed with the processed track, two nearly identical signals combing against each other, and it was audible only by listening. The angle now carries the subrole its `mc-source` names, built by the same construction in both places so they cannot drift apart again. A test asserts the invariant and fails without the fix.

**If you have already imported an earlier export**, you do not need to re-export and redo your edits: in the Audio Configuration inspector, untick the two angles whose names end in `raw`.

## [v26.08.24.56] - 2026-08-24

### Added
- **The Button Says What Has Been Done**: after a run the panel reset to "Process audio", which looks exactly like a panel where nothing has happened. There are three states and they were all rendered the same: nothing processed, everything processed, and processed-but-the-settings-have-changed-since. The button now reads "Audio processed (n files)" when every file matches the current settings, and pressing it asks for confirmation before starting a run that costs minutes. When only some files are stale — because a control moved after the last run — it invites processing again and the note says how many and why. Freshness is recomputed on every settings round, so the button goes stale at the same moment the result does; only the button is swapped, never the whole panel, because redrawing it would pull a slider out from under a drag.
- **Deliberate Re-rendering**: `/api/mix` takes `force`, which processes files that are already up to date. Reachable only through the confirmation, because it is minutes of work that the fingerprint would otherwise correctly skip.

## [v26.08.24.55] - 2026-08-24

### Performance
- **The Plug-In Now Uses More Than One Core**: dxRevive was measured at 0.98 cores and 7.25× realtime — the plug-in is 97 % of a run, and it was using one of eight cores. The file is now cut into as many pieces as there are workers and the pieces run in parallel on their own plug-in instances. Measured on a real 20-minute microphone file: **168.4 s → 68.3 s, 2.46×**. Scaling is not linear (1 → 7.5×, 2 → 9.5×, 4 → 14.8×, 6 → 20.1× realtime) because the plug-in's inference is memory-bandwidth bound; six workers is where adding more stops paying, and two cores are left for the interface. This is not the forbidden chunked feeding: each piece is its own full `reset=True` run with a five-second margin that is processed and discarded, and the result is written into an array of the original length, so the sample count cannot move. It is not free either — the pieces cannot see each other's context, so the plug-in's slow adaptation differs slightly between them: 25.7 dB below the signal in speech, −84 dBFS absolute in the quiet parts. Because that is not zero, the piece count is adjustable in the panel — a share of the machine's cores by default, capped at the core count, and 1 for a single run over the whole file.

### Added
- **The Loudness Target Is the Program's, Not One Stem's**: two microphones each normalised to −14 LUFS do not sum to −14. Measured on real material, they summed to −12.2 — the speakers overlap and the microphones hear each other, so the gap is neither the 3 dB of two identical signals nor the 0 dB of perfect alternation. `mix.program_trim` measures the sum of the raw microphones over a bounded window before processing and takes the difference off every file; on the episode it was built for, −1.79 dB, measured in five seconds. The window is anchored to the longest microphone file rather than the middle of the timeline, because in a multicam the parts are consecutive and the midpoint lands inside one of them.

### Fixed
- **"Process Audio" Did Nothing and Said Nothing**: a processed file was considered up to date whenever it was newer than its source. Nothing else was compared, so changing the plug-in, its controls, the target loudness, the ducking depth or the trim did not invalidate anything: the run skipped every file, returned before the first log line, and left the panel showing exactly the text it showed before the button was pressed. The processed audio on disk stayed as it was rendered days earlier, and the export used it. Freshness is now a fingerprint — the source's path, size and modification time, the plug-in's own modification time, the job's target level, and every setting the result depends on — kept in `~/Library/Caches/autoraffkat/mix/`. A file whose fingerprint is unknown counts as stale, so the first run after this update re-renders everything once. `adopt` uses the same test, so the export never uses a file that processing has just decided to redo.
- **A Run With Nothing to Do Was Indistinguishable From a Broken Button**: processing now logs each skipped file and a summary line to the terminal, and the interface reports the outcome of a run — "processed *n* files" or "every file was already up to date". A no-op run finishes before the first progress poll, so without this nothing on screen changed at all.

## [v26.08.24.54] - 2026-08-24

### Performance
- **The Shift Check Cost More Than the Plug-In**: after processing, the result is cross-correlated against the original to catch a plug-in that reports its latency wrongly. It used `np.correlate(..., "full")`, which computes the correlation directly and is O(n²). On a millisecond grid a 20-minute file is 1.2 million bins, and the check took **132 seconds** — longer than dxRevive spent on the same file — while an hour-long file would have spent a quarter of an hour on the check alone. An FFT gives the identical answer in 0.05 seconds. Processing that file now takes 168 s instead of 300 s.

### Added
- **A Progress Bar for Audio Processing**: the panel now shows a weighted bar, the stage being worked on (plug-in, measuring, dynamics, shift check, writing) and a time estimate, rather than `2/4` alone. Files are weighted by size, so a 20-minute file and a 64-minute one no longer count the same, and the estimate exists from the first stage instead of appearing only after the first file finishes. The bar moves within a single long file: the plug-in cannot be asked how far along it is — it processes a file in one piece because chunking would shorten the result — so stage boundaries are the resolution available.
- **Progress Survives a Reload**: processing runs in a background thread on the server, but only the browser tab that started it was watching. Reloading the page mid-run left a frozen panel. The interface now resumes watching whenever it loads and finds a run in progress.
- **Processing Logs to the Terminal**: one line per file and per stage with its duration, plus the normalisation lift and any error. Processing takes minutes in a background thread where nothing was visible, and when it is slow or fails the question is always the same — which file, and which stage.

## [v26.08.24.53] - 2026-08-24

### Added
- **The Raw Microphone Also Travels With a Flat Cut**: the muted twin now exists in the non-multicam export too. There are no angles there, so it is a connected clip on its own lane with `enabled="0"` and the subrole `dialogue.<Speaker> raw`. Twins sit on the lowest lanes — after the microphones and the room tone — so switching processing on does not move the microphone on lane −1. Only a processed track gets one; without processing there is nothing to fall back from.

## [v26.08.24.52] - 2026-08-24

### Fixed
- **The Export Used Raw Audio Unless the Button Was Pressed in That Session**: processed audio is written once and stays on disk beside the source, but the fact that it existed lived only in the session's memory. Reopening an episode — or opening a new one whose settings were inherited — and exporting straight away referenced the untouched originals, while the file name still said `audio`. Processed files that are up to date beside their source are now adopted on load and again at export, so the export follows what is on disk rather than which buttons were pressed.

### Added
- **The Raw Microphone Travels With the Cut**: every processed microphone angle now has a twin in the multicam carrying the untouched original, muted (`active="0"`) and on its own subrole (`dialogue.<Speaker> raw`). Redirecting an asset to the processed file leaves no reference to the original, and a plug-in's mark is heard only by listening — by which time the cut has usually been edited in Final Cut and a fresh export would not bring that work along. The twin is a copy of the angle, so it inherits its timing and bookmark and is in sync to the sample.

### Changed
- Processing now refuses outright to write to its source path. The target has always been a `[mix]` sibling, but the check now sits at the write itself, because that step is not reversible.

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
