# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to Calendar Versioning (CalVer).

## [v26.08.26.86] - 2026-08-26

### Added
- **Reaction Shots Appear in the Cut List**: interleaved by timecode with the cuts, but **unnumbered and indented**. They are not cuts — they are overlays on their own lane, and the numbering is the running order of cuts. Numbering them would claim they are part of the cut underneath, which is exactly what the separate lane exists to deny. The summary line counts them separately.

## [v26.08.26.85] - 2026-08-26

### Added
- **Reaction Shots Appear in the Preview Bar**: a low fourth row under the cut row, coloured by speaker, so their placement against the speech is visible before exporting. Where they fall relative to who is talking *is* the question, and a list of timecodes cannot answer it.
  - The row is squeezed into the **same columns** as the speech rows. The bar is read across, so the rows' relation to each other is the whole point; a different division would put a reaction shot at the wrong place against the speech with nothing to say so. A test asserts the column counts match, and fails if they drift.
  - A column is marked as soon as any reaction touches it, exactly as the speaker rows work. Reaction shots are around a second and the bar is 1400 columns wide, so an averaging squeeze would lose them precisely where they matter.
  - The row and its legend entry appear only when there are shots; an empty strip would promise a feature that has not been measured.
  - The lane is drawn even when the setting is off, because that is the only way to judge what turning it on would do before exporting.

## [v26.08.26.84] - 2026-08-26

### Fixed
- **"4 lähikuvaa mitattu" Was the Least Informative Number Available**: it counted *files* — two cameras in two parts — and read as though four pictures had been found. The row now says how many keyframes came out of how many files, in what share a face was found, and **how many moments pass the gate**, which is the only number that answers "will this do anything".
  - The candidate count is recomputed on every state request, so it moves as the gate slider moves. It is numpy over the cached tables with no file reading, so it belongs in the settings loop.
  - Like the measurement, it ignores the `reactions` setting on purpose: it reports what is in the material, and the setting only decides whether that gets used. Reading it would have shown zero while hundreds of candidates existed — the same lie as the button.

## [v26.08.26.83] - 2026-08-26

### Fixed
- **"Mittaa lähikuvat" Was a Silent No-op While Reaction Shots Were Off**: the progress bar ran through in a second, zero files were measured, and nothing was said. `analyse.tables()` returned empty as soon as it saw the setting off — but pressing the button is an explicit request, and measuring is gathering data; the setting only decides whether the data gets used. The measurement no longer looks at it.
- **Nothing to measure now says so.** If no close-up qualifies — none roled, or nobody ever falls silent — that is a valid situation but it is not "done", and the button must not look like it succeeded.

## [v26.08.26.82] - 2026-08-26

### Changed
- **Close-ups Are Measured Four at a Time**: decoding one stream does not spread across cores, so the parallelism has to be across files. On the real path the job went **990 s → 476 s**.
  - Four is measured, not chosen: 22× realtime for one file, 38× for two, 73× for four — and then it stops, 72× at six and 71× at eight. The ceiling is neither the disk nor the CPU. During a decode `dd` pulled **759 MB/s** off the same drive while the decode held its 254 MB/s, and **66 % of the CPU was idle even at eight**. It is the number of hardware h264 decoders, and threads cannot add to those.
  - The temp JPEG round-trip, which looked like an obvious suspect, costs about **1 %** of the time (23.1 s → 23.3 s over a 300 s segment). Scaling to 960 px costs 19 %. Everything else is the decode itself.
  - A test asserts the files actually overlap. Serial would not fail, only take three times as long, which is the kind of slowdown nobody notices without measuring.

## [v26.08.26.81] - 2026-08-26

### Added
- **Reaction Shots Reach the Export and the Interface**: the pipeline built earlier is now wired end to end. A row in the cut panel turns them on, measures the close-ups, and shows how far along it is; the export puts what passes the gate on its own lane.
  - **Measuring is a button, not something the load does.** Decoding is minutes and most episodes do not want reaction shots at all. The result is cached on disk, so a second run costs seconds — which is what makes it affordable to press.
  - It runs in a **thread**, not a child process. The child was pedalboard's requirement, which needs the main thread to load a VST3; Vision has no such constraint and ffmpeg is already its own process.
  - **Both empty cases are reported.** Reaction shots on with nothing measured, and measured with nothing passing the gate, are different situations and each says so in the export warnings. Setting on and nothing in the result is this project's recurring failure, and silence is how it gets missed.
  - The gate is the only control exposed, and it carries its measurement: the classes do not overlap, so 0.080 sits in the gap between the worst acceptable frame and the best unacceptable one.

### Fixed
- The interface smoke test's coverage guard caught `watchVideo` never being called — the harness did not serve `/api/video`, so the handler failed before reaching it. The route is now stubbed, which is the honest fix: the harness should answer the endpoints the interface actually calls.

## [v26.08.26.80] - 2026-08-26

### Changed
- **The Gate Moved to 0.080, and It Is Now Measured Rather Than Chosen**: 23 hand-marked frames out of 381 candidates, and the two classes do not overlap at all — the worst frame marked good is 0.0721, the best marked bad is 0.0943. The threshold belongs in that gap. At 0.080 it keeps **all twelve marked good, admits none of the eleven marked bad**, and passes 60 % of candidates, about nine seconds a minute. The previous 0.057 was set from six marks and falsely rejected three good frames.
  - It sits on the tight half of the gap on purpose: a reaction shot that never happens costs nothing, one that is disqualifying costs the take. A test asserts the default stays inside the gap and on that side, so moving it says which error was chosen.
  - Caveat kept in the source: all eleven frames marked bad come from one speaker, so that half of the evidence is thin.

## [v26.08.26.79] - 2026-08-26

### Fixed
- **Vision's `yaw` Is a Bin, Not an Angle**: measured across 9995 frames of real footage it takes exactly five values — multiples of 45° — and `roll` takes three. The components computed from the landmarks here (`smile`, `eyes`, `size`) take about nine thousand each over the same frames. So the one component that separated good reaction frames from bad was effectively binary, and the continuous ones did not separate at all. It looked like an angle and nothing said otherwise.
  - `turn` and `tilt` now come from the nose relative to the midpoint of the eyes, divided by the eye span so face size and distance stay out of the measure. `yaw` stays: as a bin, "turned away" is exactly what it detects well. Detector version 2, so the cache invalidates itself.

### Changed
- **The Reaction Score Is a Gate, Not a Ranking**: the bar for a reaction shot is not "outstanding" but "not disqualifying" — in a finished edit most are unremarkable and only have to avoid embarrassment. Measured on 381 candidates against hand marks, a head-pose deviation of **0.057 keeps all six frames marked good, admits none of the fifteen marked bad, and halves the pool**. The same job on the quantised `yaw` let 95 % through and admitted three bad ones.
  - `eyes` and `size` now default to zero weight. Neither separated good from bad, and `eyes` was actively harmful: a hard laugh closes the eyes, so rewarding open eyes buried the frames worth cutting to — three of the six marked good sat at ranks 66, 67 and 69 of 72 because they were neutral, attentive faces rather than grinning ones.

## [v26.08.26.78] - 2026-08-26

### Added
- **Video Analysis Layer (`video/`) and Reaction Spans (`reactions.py`)**: the scaffolding for reaction shots, built so the detection can be replaced without touching anything else. Nothing is wired into the export path or the interface yet — this is the stable half, deliberately built first.
  - **The seam is the detector.** `video/detect.py` is a registry; a detector looks at one frame and returns numbers, knowing nothing about the timeline, the speakers or the scoring. Its `name` and `version` are part of the cache key, so swapping it invalidates the cache by itself. Without that a new detector reads the old one's traces, and the result is valid, accepted and wrong.
  - **Measurements are cached, not scores.** Tuning the weights costs nothing, which matters because the weights are the part expected to change. `reactions.py` reads the finished table in numpy with no file access — same rule as `decide.py`, since it runs in the settings loop.
  - **Only keyframes are decoded**, measured at 70× realtime against 16× for a full decode — one frame a second at a camera's usual keyframe interval. And only close-ups of speakers who are silent at some point: decoding is the entire cost, so the narrowing happens before it.
  - **Reactions are written to a positive lane as video-only connected clips**, never as angle switches inside the `mc-clip`. Validated against Final Cut's own DTD.
  - The gaze baseline is measured, not assumed: a camera is not square-on, so "facing the speaker" is that camera's median yaw, not zero. Below the threshold nothing is proposed — a reaction shot of someone looking at their phone is worse than none.

### Fixed
- Two traps found while building, both silent: `-vsync 0` no longer exists in current ffmpeg (`-fps_mode passthrough`), and without it keyframes are stretched back to full rate — the same picture dozens of times, timestamps out of step with frames. And `ffprobe`'s `csv=p=0` still emits a trailing comma, so every timestamp failed to parse.

## [v26.08.25.77] - 2026-08-25

### Fixed
- **The Row Hover Lied About What It Would Do**: a row carries two different actions — switch it on, or look inside — and the hover highlighted the whole row, including the checkbox. It promised one target where there were two. The checkbox sitting at the far left made it worse: a checkbox before a label reads as *that label's* checkbox, so clicking the name looked like it would toggle.
  - Opening is now a **button** containing the name, the value and the chevron, and only that button highlights. The switch comes after it, next to the chevron: two controls side by side at the right edge, with the label clearly outside both.
  - The button handles the keyboard itself. The hand-rolled `keydown` was doubling the space bar and breaking Enter.
  - A test asserts the switch is not inside the button — nesting it would mean one click doing both things, and a control inside a `<button>` is invalid anyway. Verified by breaking it.

## [v26.08.25.76] - 2026-08-25

### Changed
- **A Preset's Sliders Appear Only Once "Custom" Is Chosen**: the rhythm preset's four numbers were always visible, and moving one switched the preset to Custom in passing. That made the choice change as a *side effect* rather than as a choice. A preset **is** the decision; those four numbers are its definition, not something adjusted on top of it. Pick Custom and they appear, carrying the values the preset had.
- **Long Turn and Overlapping Speech Are Rows Too**: each is one rule and a couple of timings, and the chosen rule now reads off the collapsed row instead of having to be found among the radio buttons. The settings rail went from one long scroll to eight collapsed rows.
- **Three Columns on a Wide Screen**: two columns left a metre of empty space beside the patch bay and crammed everything else into one rail, so the audio section fell below the fold and could not be found — which is exactly what happened. The rail now splits into two columns above 1500 px, giving bay / cut / audio. Below that it is unchanged, and it stacks as before on narrow screens. The third column comes from splitting the rail rather than adding a column to `main`, so the medium case is untouched.

### Fixed
- The smoke test now asserts that a preset hides its sliders and Custom shows them. Verified by breaking it: the guard reports `esiasetuksessa näkyi 4 säädintä, pitäisi olla 0`.

## [v26.08.25.75] - 2026-08-25

### Fixed
- **The New Rows Read Like the Old Checkboxes**: they inherited the checkbox labels, which were whole sentences — "Vaimenna toinen mikki puheen ulkopuolella" as a row name beside its own description and its own value. A checkbox needs a sentence because nothing else is next to it; a row does not. The rows are now named: Palautusliitännäinen, Vuodon poisto, Vaimennus, Naksunpoisto.
- `unit.db` already begins with a space, so the row value read `-9  dB`.

## [v26.08.25.74] - 2026-08-25

### Changed
- **The Audio Panel Collapses to Seven Rows**: it showed 26 sliders, and eight of them belonged to one feature. Almost every one has a measured default, and the measurement is written down in the code — but the user saw only the slider. The rule for the first screen is now: **if a default's measurement can be written down, the slider does not belong there.** That separates a number we measured from the few where taste genuinely varies — ducking depth, the plug-in's Mix, the platform's loudness.
  - Nothing was removed. All 26 controls are still present at the same values; they were **ranked**, not deleted, and each now carries the measurement that set it. `duck_min_closed` says it is 0.6 s because shorter made 20 ms holes that click; `declick_sensitivity` says the threshold was calibrated on findings per second, 316–666 at 3.5× against about one at 25×.
  - **A closed row shows when something inside it has been changed**, and names which control. Disclosure that hides a setting you already moved is worse than no disclosure — the knob disappears and cannot be found. Same principle as `project.name_tag`, which writes deviating controls into the export filename: the deviation is always visible one level up.
  - Bleed removal is a row with no controls at all, and that is not a gap. It estimates the leakage path and measures its own result; a knob would only be a way to break it.
  - Each row can restore its own measured defaults. `audio_defaults` comes from `AudioSettings()` over `/api/state`, not a copy in JavaScript — a copy would drift silently and the marker would then be wrong or absent.
  - Opening a row does not redraw the panel, for the same reason `swapMixButton` exists: it would swap a slider out from under the cursor mid-drag.

### Fixed
- The interface smoke test's synthetic event had no `stopPropagation`, so a handler that used it threw only in the test. A real browser event has it; the stub now does too. The smoke test also asserts the rows and the deviation marker structurally — a marker that quietly stopped appearing would otherwise still pass, since nothing throws.

## [v26.08.25.73] - 2026-08-25

### Fixed
- **The Plug-in's Window Opened Behind Everything**: the button reported the window was open and nothing appeared. The window was there all along — measured at 536×392 in the top-left corner, on screen, thirteenth from the front — but a plain Python process is not a GUI application to macOS, so it has no Dock icon and never comes forward. To the user that is indistinguishable from a button that does nothing, which is this project's recurring failure: it happened, it did not show, nothing said so. The child now sets `NSApplicationActivationPolicyRegular` and activates itself, once before opening and once a second later, when the plug-in has actually drawn something. pyobjc arrives with pywebview and is not required here: without it the window still opens, it just has to be found.
- The window's title is pedalboard's, not the plug-in's, so the panel now says which title to look for.

## [v26.08.25.72] - 2026-08-25

### Added
- **The Plug-in's Own Window (`audio/editor.py`)**: a button in the audio panel opens the plug-in's real interface, and whatever state you leave it in is saved with the episode.
  - This is not a convenience. **Not everything that changes the result is a parameter.** dxRevive publishes four automatable parameters — bypass, input gain, output gain, Mix — and the *model selector is not one of them*. Studio 2 and its siblings live in the plug-in's own state, reachable only through its own interface. Without this we always ran whatever model the plug-in happens to default to, and could not even report which one.
  - It runs in a **child process**. `show_editor` carries the same rule as loading — main thread only — and it *blocks* until the window is closed. The server's main thread is the event loop and cannot be held for as long as someone looks at a plug-in. Same reason, same shape as `audio/worker.py`.
  - The state is applied **before** parameters, so a saved Mix cannot override the slider in the panel. A state from a different plug-in is opaque and useless, so it is ignored rather than made into an error, and changing `plugin_path` drops it along with the parameters.
  - `plugin_state` is in `FINGERPRINT_FIELDS` and `FINGERPRINT_VERSION` is 4: a different model is a different result, and the button must not call those files fresh.

## [v26.08.25.71] - 2026-08-25

### Added
- **Bleed Removal (`audio/debleed.py`)**: two microphones in one room hear both speakers, and in the export both tracks play — so the other person's voice arrives twice, a few milliseconds apart. That is a comb filter, and it sounds like a metallic reverb. Measured on a real episode: Nyman's voice sits 7.7 dB below the direct sound in Wancke's track at a 5 ms delay.
  - **Ducking cannot fix this, and a deeper duck does not help.** Measured: the masks fire correctly and close Wancke's microphone on 64 % of the frames where only Nyman speaks — yet *infinite* attenuation moved the sum's ripple from 6.22 dB to 6.01 dB. The gaps are at the turn-taking boundaries, which is where the bleed is loudest. A gate can also do nothing about overlapping speech, where both microphones must stay open.
  - The bleed is **linear** — one source, one room, a fixed delay and early reflections — so it is an FIR filter from one microphone to the other, and it can be estimated and subtracted. The filter is solved by least squares over the passages where **only the source speaks**, and subtracted everywhere, overlapping speech included.
  - Measured coherence, 200–6000 Hz, where only the source speaks: raw 0.1734 → 0.0095; after the full chain 0.1069 → 0.0098. The target's own speech survived at r = 0.9993.
  - It runs on the **raw** audio before the plug-in. The plug-in is generative and does not preserve the linear relation between tracks; after it, no filter can remove the bleed.
  - **The result is checked, not assumed.** A wrong estimate eats the target's own speech, and that is only audible after the export. `remove` measures its own output and refuses a filter that reduces the target's own speech below `MIN_SPEECH_KEPT`, that had less than `MIN_SOLO_SECONDS` to learn from, or that achieves nothing. Every refusal names its reason in the log and in the result.
- **`FINGERPRINT_VERSION` 3**, and a `debleed` toggle in the audio panel.

## [v26.08.25.70] - 2026-08-25

### Fixed
- **The De-clicker Was a Distortion Generator**: it corrected **1.8–2.2 % of every sample — 550–640 corrections per second** — and altered the signal by −10 to −15 dB relative to itself. A lip smack happens a few times a minute. The cause was half a fix: when the reference was corrected from a local maximum to a local mean, the multiplier stayed the maximum's (3.5), and against a mean it fires on ordinary speech. Measured on real podcast material, the threshold that finds clicks at the rate clicks actually occur is around 25× the local mean. Default sensitivity now makes 0.2–0.6 corrections per second and touches 0.03 % of samples.
  - A **ceiling** backs the threshold up: more findings than `DECLICK_MAX_PER_SECOND` and the threshold doubles until they fit; if they never fit, nothing is corrected. A detector that finds a click every other millisecond has found the signal, not clicks.
  - Overshoots within 2 ms are **one** event. Without that, a single 2 ms click counts as thirty separate findings — its half-cycles — so the ceiling tripped on one click and the interpolation repaired only the peaks of the wave and left the rest.
  - The plosive guard compares against a **local** mean. A whole-file mean made the guard a function of file length: in an hour-long recording full of pauses the mean sinks and the guard stops guarding exactly where the detector fires most.
  - Two tests now cover both directions. The old suite only asked whether a planted click was removed, never *how many* were found, so a detector that corrected everything passed it.
- **`FINGERPRINT_VERSION` 2**: files made with the old detector are not up to date under any setting, and without this the button would have said the opposite.

## [v26.08.25.69] - 2026-08-25

### Fixed
- **The Export's Name Did Not Reach Final Cut**: the file name carries the settings tag and a version number — `…-cut broadcast audio v8.fcpxml` — but Final Cut does not show file names. It shows `<project name>`, which was the project name setting and therefore identical for every export. Successive imports were indistinguishable in the browser, with nothing to say which was newer or which file it came from, which is the same problem the file numbering exists to solve. The shown name now carries the distinguishing part: `Rough cut · broadcast audio v8`.

## [v26.08.25.68] - 2026-08-25

### Fixed
- **The Envelope Cache Had Never Worked**: `np.save` appends `.npy` to a path that does not already end in it, so writing the temporary file `<key>.npy.tmp` actually produced `<key>.npy.tmp.npy`. The rename that followed then looked for a file that did not exist, raised `FileNotFoundError` — which is an `OSError` — and the `except OSError` swallowed it. Nothing failed, nothing was logged, and every load re-decoded every audio file with ffmpeg. There were 1212 orphaned files in the cache directory going back to 21 August, and they have been removed. Writing through an open handle instead: analysing this project's ten files falls from **56.5 s to 0.0 s** on the second pass.
- **The Test That Should Have Caught It Measured the Wrong Thing**: it asserted the second analysis pass took under 0.4 seconds — a proxy for "the cache was used" that the tiny test fixture satisfied whether or not the cache worked, and that fails on a busy machine whether or not it is broken. It now forbids decoding outright, so a miss is an error rather than a slowdown.

## [v26.08.25.67] - 2026-08-25

### Changed
- **The Channel Strip Bites in Small Amounts, Several Times**: one compressor pulling twelve decibels sounds like a compressor; three pulling four sound like nothing. Every stage now has a hard ceiling on its gain reduction (`MAX_GR_DB`), and there are three of them — a multiband stage first, so a plosive at 100 Hz cannot drag the sibilance down with it, then two gentle broadband stages. Owsinski puts a single box at "less (usually way less) than 3dB" and calls six decibels "extreme processing" worth splitting; five is the hard ceiling here, and typical reduction sits well below it. All bands share one ratio and one limit, which is his explicit precaution — differing amounts per band alter the tone with the programme and read as unnatural.
- **The Ceiling Is True Peak, With Headroom**: limiting sample peaks to −1.0 dBFS measured −0.42 dBTP, because the peaks that clip a converter fall *between* samples. Detection is now 4× oversampled and the ceiling is −1.5 dBTP, which survives AAC encoding — the handbook wants true peaks under −1, and −2 for Spotify.
- **An Over-Compression Alarm**: `peak_to_short_term` reports peak-to-short-term loudness. Owsinski's one numeric rule for this: below about 6 LU means more compression than was needed. The current chain measures 12 LU on a stem and 15 on the programme.

### Fixed
- **Compressor Thresholds Follow the Target**: they were absolute, so raising the delivery target from −20 to −14 LUFS drove the signal 6 dB deeper into them and removed 4.5 dB more crest. The target now changes the level, not the amount of compression.

## [v26.08.25.66] - 2026-08-25

### Fixed
- **The Program Trim Was Being Undone**: it was added to the gain, and the chain normalises to the target *after* that, so the normalisation removed it exactly. The stems measured −14.1 LUFS where they should have measured −15.8, and nothing said otherwise — the number looked right, just for the wrong reason. The trim now goes into the target, where normalisation preserves it because it is the thing normalisation aims at. A test asserts the trim actually moves the level.
- **Doubled `[ääni]` in the log**: the child's own lines were passed through the parent's logger, which added its prefix a second time.

## [v26.08.25.65] - 2026-08-25

### Fixed
- **The Plug-In Pool Loaded on the Wrong Thread**: moving processing into a child process was necessary but not sufficient. The pool then loaded each instance *lazily, inside the worker thread that would use it*, which is exactly what pedalboard forbids — and the error says `reset=False`, which points at processing and hides that the rule is about **loading**. Processing from a worker thread is fine; loading is not. Every instance is now created when the pool is built, on the thread that builds it, and each piece is handed one. Verified end to end on the real project: plug-in 59 s for a 20-minute file across six pieces, with ducking active.

## [v26.08.25.64] - 2026-08-25

### Fixed
- **"Process Audio" Raised `name 'json' is not defined`**: the child-process change added a `json.dump` to the server without the import, and no test reached that path — the whole of `run_mix` was untested, so 235 green tests said nothing about the button. Two tests now drive it end to end with a stand-in child: one that reports progress, a non-JSON log line and a result, and one that dies with a non-zero code. Both were confirmed to fail without the fix.

## [v26.08.25.63] - 2026-08-25

### Fixed
- **Audio Processing Runs in Its Own Process**: loading a VST3 through pedalboard requires the **main thread** — anywhere else it refuses with "must be reloaded on the main thread". The server's main thread runs the event loop and cannot be occupied for minutes, so hosting the plug-in inside the server was never sound; it had simply been getting away with it. Processing now happens in a child process whose main thread is free to do the work, talking back over line-delimited JSON. Two things come free: a plug-in that crashes or hangs no longer takes the server with it, and the child computes the envelopes itself, so ducking can no longer be skipped for not having them.

## [v26.08.25.62] - 2026-08-25

### Fixed
- **Ducking Was Silently Skipped, and the Bleed Comb-Filtered**: playing two processed microphones together produced a flanging sound. It was not a sync problem — the files measure 0 samples of offset against their sources, everywhere, and the parallel pieces differ by at most 2 samples. It was the crosstalk. Each microphone is normalised to the same target independently, so the quieter one gets more gain, and that gain lifts the *other* speaker's bleed inside it: separation between direct sound and bleed fell from 19.2 dB to 15.2 dB, which is where a few milliseconds of acoustic path delay stops being inaudible and starts being a comb filter.

  Ducking exists to prevent exactly this, and it had not run. The envelopes are computed in a background thread on load; pressing the button before that finished left `analysis` unset, the grid unbuilt and the masks empty — with no log line, no warning and no error. Measured in the output: the ducked track was **1.7 dB louder** during the other speaker's turn rather than 9 dB quieter. Processing now waits for the envelopes instead of skipping, says so while it waits, and reports it in the panel if they never arrive. It also logs how many microphones got a mask and how much material will be ducked, and treats "the setting is on and nothing matched" as an error rather than a silence.

## [v26.08.24.61] - 2026-08-24

### Added
- **Loudness Targets Have Names**: −14 LUFS is where YouTube normalises, −16 where Spotify and Apple Podcasts do, −23 is EBU R128. These are specifications, not preferences: export louder and the platform turns it down, quieter and it sits under everything else. The panel now names them and the default is YouTube's −14; the slider stays free, because not all delivery is one of the three.
- **The Level Now Lands Where It Was Asked To**: the limiter eats loudness in proportion to what it clips, and correcting for that pushes the peaks back into the limiter, so a single correction pass always fell short — measured 1–2 dB under target. It now iterates up to three times and stops within 0.3 dB. On real material: asked −14, got −14.52 and −14.64 for the two speakers; asked −16, got −16.27 and −16.53. The two microphones land **0.12 dB apart**, which is the balance that matters.

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
