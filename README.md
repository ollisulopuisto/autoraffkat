# autoraffkat

Automatic multicam editing for interview and podcast video on macOS. Export an
FCPXML from Final Cut Pro and get a new FCPXML back, where the picture cuts to
whoever is talking. The cut is decided from the microphone tracks. Nothing is
ever rendered, and the result opens in Final Cut Pro as an ordinary multicam
timeline you can keep editing by hand.

*[Suomenkielinen README](README.fi.md) · [Design notes](DESIGN.md)*

![The track list drawn as a patch bay: three cameras on the left, two microphones on the right, each speaker's name once in the strip between them, with the preview bar and the cut list below.](screenshot.png)

*One row per speaker: their camera, their name, their microphone. The bar below
is the whole episode at a glance; the list under it is every cut the export
will make. Pictured: editing episode 53 of
[Peter & Peter](https://www.youtube.com/@peterpeterhistoria), a Finnish show
about history.*

![The resulting multicam timeline in Final Cut Pro with cuts switched between camera angles and dialogue tracks attached below.](screenshot-fcpx.png)

*The imported result in Final Cut Pro: an ordinary multicam timeline with all
angles, cuts and dialogue roles in place.*

* **The angles switch on the audio**, not on a rhythm: each speaker's
  microphone decides when their camera is on screen.
* **Long turns and overlapping speech are rules you set once** — cut to the
  wide, hold the current shot, or let the louder microphone win.
* **Milliseconds per adjustment.** Moving a slider re-decides the whole
  episode and redraws the preview without writing a file.
* **Final Cut Pro all the way out.** A multicam source produces a multicam
  timeline; angles, roles and sync survive the round trip.

## Usage

```
uv run autoraffkat
```

With no argument the source is found in the working directory: a single export
opens straight away, several give you a numbered choice (Enter picks the
newest), and an empty directory opens a Finder dialog. You can still pass a
path, and a `.fcpxmld` bundle works as-is:

```
uv run autoraffkat "episode 12.fcpxmld"
uv run autoraffkat --pick            # go straight to the file dialog
```

Existing `-cut` exports are never offered as a source: the loop always
returns to the original.

The browser opens at `http://127.0.0.1:8731/`.

The loop:

1. Sync picture and sound in Final Cut, export the XML.
2. Open the XML here, name the tracks, move the sliders.
3. Export the XML (`⌘E`), import into Final Cut, watch.
4. If it isn't right, back to step 2.

Steps 2 and 3 are milliseconds apart: moving a slider runs only the decision
layer, and the preview bar shows the result without a round trip through XML.

Export writes a new file `episode-cut broadcast.fcpxml`; the source XML is
never touched. Settings are saved next to it as `episode.autoraffkat.json`.

The name says what was cut: the rhythm preset always, plus any control that
deviates from the default (`episode-cut custom 3s louder stay audio.fcpxml`).
In Final Cut's browser the name is all that separates one rough cut from the
next, and `-cut` and `-cut v2` do not say which one was the fast one. Turn it
off with **Settings in the file name** in the Project section; the path shown
in the interface follows the controls, so the effect is visible before you
export.

An earlier export is never overwritten either. If `episode-cut broadcast.fcpxml`
exists, the next one becomes `episode-cut broadcast v2.fcpxml`, then `v3`, and
so on. The number runs within one set of settings: a cut made with different
controls is a different file, not the next version of the same one.

Every control also travels inside the exported XML. The sequence carries a
one-line `<note>` — the version, the rhythm, the shot lengths, the rules,
whether the microphones were processed — and a `<metadata>` block with one
`<md>` entry per setting plus the complete settings JSON under
`fi.autoraffkat.settings`. That block is what makes a cut reproducible from
the file alone, on a machine that never saw the settings file.

When the source is a `.fcpxmld` bundle, neither file goes inside it. Both land
beside it and take the bundle's name: `episode 12.fcpxmld` produces
`episode 12-cut broadcast.fcpxml` and `episode 12.autoraffkat.json`. The
bundle belongs to Final Cut.

A new episode inherits its roles from the previous one. Track keys are derived
from filenames, so `CAM 1` is the same camera next week too. Inheritance
looks in the XML's directory, the one above it, and any `.fcpxmld` bundles
there; the source is shown under the title as "Roles inherited from". An
episode's own settings always win.

### Download

[Releases](../../releases) carries a disk image for Apple Silicon, built and
published by CI from a `v*` tag. It is not notarised — that needs a paid Apple
Developer ID — so the first launch takes a right-click on the app and "Open",
or `xattr -d com.apple.quarantine autoraffkat.app`.

Everything in the package is arm64, ffmpeg included, so there is no Rosetta
step. Intel Macs need a package of their own: GitHub no longer offers free
Intel runners, so build it on an Intel machine with `scripts/build_app.py
--dmg`, which fetches the matching Intel binaries by itself.

### Install from source

```
brew install ffmpeg
uv sync            # or: pip install -e .
```

Requires Python 3.11+, ffmpeg, and macOS for the file dialog and thumbnails.
Everything else is cross-platform.

### Desktop app

```
uv run python scripts/build_app.py          # dist/autoraffkat.app
uv run python scripts/build_app.py --dmg    # …and dist/autoraffkat.dmg
```

Pushing a `v*` tag runs the same two commands on CI and publishes the disk
image to Releases; see `.github/workflows/build.yml`.

The build bundles static ffmpeg and ffprobe binaries, downloading them into
`bin/` on the first run from [ffmpeg-static](https://github.com/eugeneware/ffmpeg-static)
at a pinned version with SHA-256 checks. It fetches the architecture it is
running on, and replaces a binary of the wrong one — a mismatch works on the
build machine and fails on the user's. `scripts/make_dmg.py` packs an already-built app on
its own; it copies with `ditto` rather than a plain file copy, because the
signature covers extended attributes and symlinks and a plain copy breaks it
in a way that only shows up on somebody else's machine.

## Input

Three kinds of source are supported:

* **synchronised clip** (`sync-clip`), cameras and microphones on lanes
* **project timeline** (`project` > `sequence` > `spine`), laid out by hand
* **multicam clip** (`mc-clip`), cameras and microphones as angles

Sync is read from the XML, never recalculated. Frame rate comes from the
sequence or the video asset's format.

### Multicam and parts

A long recording is usually several multicam clips on the spine — part A,
part B — and in each part the same camera is a separate file. Three cameras in
two parts is six files but three **tracks**: roles, controls and the edit all
work per track, and a track is assembled by angle name.

The track key is derived from the common part of the filenames
(`CAM 1 01` + `CAM 1 02` → `CAM 1`), because angle names
and `angleID`s change from one export to the next while the files do not.
Saved roles therefore survive a re-export.

### Remote Host Execution

You can run autoraffkat entirely on a faster remote host (e.g. a dedicated Mac or workstation) to offload envelope calculation and heavy VST audio processing:

```
uv run autoraffkat --host 0.0.0.0 --port 8731 --no-gui --no-browser "episode 12.fcpxmld"
```

Open `http://<remote-ip>:8731/` in your local browser. Media files referenced by the XML must be accessible on the remote host (e.g. on a mounted 10GbE network share or synced folder).

### Plug-in Locations

External VST3 and AU plug-ins are discovered automatically from standard locations:
* **macOS:** `/Library/Audio/Plug-Ins/VST3`, `~/Library/Audio/Plug-Ins/VST3`, `/Library/Audio/Plug-Ins/Components`, `~/Library/Audio/Plug-Ins/Components`
* **Linux:** `/usr/lib/vst3`, `/usr/local/lib/vst3`, `~/.vst3`, `~/.local/lib/vst3`
* **Windows:** `%CommonProgramFiles%\VST3`, `%LOCALAPPDATA%\Programs\Common\VST3`

## Controls

**Rhythm & Profile**: Choose a macro pacing profile or tune individually:
* **Broadcast & Podcast** (default): 2.5s minimum shot, 300ms J-cut lead, 600ms L-cut hang, 14s monologue break. Balanced, natural rhythm.
* **Mellow**: 4.5s minimum shot, 150ms lead, 1000ms hang, 22s monologue break. Leisurely documentary pacing.
* **Hectic**: 1.4s minimum shot, 400ms lead, 250ms hang, 8s monologue break. Snappy, fast-paced debate and comedy.
* **Custom**: Individually adjusted parameters.

**Per track** (microphones): sensitivity, i.e. how many decibels above the
noise floor counts as speech, and a gain trim. Sensitivity is a threshold
*relative to the floor*, so gain does not move it; gain only affects how
microphones compare against each other during overlapping speech.

**Global & Split Edits**:
* **Shortest shot**: minimum duration a camera stays on screen.
* **Lead (J-cut)**: cuts to the incoming speaker before their voice crosses the threshold, capturing pre-speech inhales and reactions.
* **Hang (L-cut)**: holds the outgoing speaker on screen after speech ceases, giving conversational pauses space to breathe.
* **Confirm time**: speech must continue this long before triggering a cut.
* **1/f Dynamic Tempo Modulation**: the decision engine tracks conversation density over rolling 45s windows, naturally compressing dwell times during fast banter and expanding them during slower storytelling.

**Long turn**: one close-up doesn't carry forever. Once the same speaker has
held the floor for the set time (default 14 s), the picture cuts at the nearest natural breath or pause. Three ways to continue:

* **Return to speaker** — the wide lasts "wide duration", then back to the
  same shot. A monologue breathes, the rhythm stays with the speaker.
* **Stay wide** — the wide continues until somebody else speaks. Fewer cuts,
  and a long monologue reads as a situation rather than a face.
* **Reaction shot** — cuts to a silent co-host's close-up for "wide duration", then returns to the active speaker. (Falls back to wide if no second close-up exists).

Zero disables the rule. The wide never falls below the shortest shot, even if
"wide duration" is set lower.

**Overlapping speech**, three rules:

* *Wide* — both talking, cut to the wide
* *Hold current* — don't cut at all
* *Louder wins* — the louder one gets the shot once the gap exceeds
  `dominance`

All three respect a minimum overlap duration: a fleeting "mm-hm" doesn't
trigger the rule.

## Tuning

| Symptom | Fix |
|---|---|
| Shots change too often | Raise **shortest shot**. If that isn't enough, raise **confirm time**: short noises stop counting as speech. |
| The cut arrives late | Raise **lead**. Half a second is usually too much; 0.1–0.3 s is enough. |
| Wrong camera during quiet moments | The microphone is hearing the other speaker as bleed. Raise that microphone's **sensitivity**. |
| One speaker always wins overlaps | The microphones sit at different levels. Raise the quieter one's **gain**. It only affects the comparison between microphones, not the threshold. |
| Cuts to the wide too eagerly | Raise **shortest overlap** so backchannelling doesn't trigger the rule. |

## Layout

```
src/autoraffkat/
  timeline.py        rational time from FCPXML (Fraction)
  model.py           media, roles, settings, segments
  fcpxml/read.py     sync-clip, spine and multicam in
  fcpxml/write.py    new project out, flat or multicam
  audio/envelope.py  ffmpeg + RMS, disk cache                SLOW
  audio/chain.py     channel strip: pedalboard + plug-ins
  audio/mix.py       which files, where, and the sync guard  SLOW
  analysis.py        envelopes onto the timeline grid
  decide.py          thresholds, durations, overlap rules    FAST
  preview.py         bar summarised for the browser
  project.py         settings as JSON beside the XML
  i18n.py            server messages in two languages
  probe.py           file facts via ffprobe
  thumbs.py          a frame from the middle of a camera file
  pick.py            finding and choosing the source at startup
  server/app.py      HTTP interface
  server/static/     the interface (i18n.js = browser strings)
```

Full rationale: [`DESIGN.md`](DESIGN.md).

In short, analysis is split in two layers. The envelope (ffmpeg, seconds per
minute of audio) runs once per file and is cached in
`~/Library/Caches/autoraffkat/`. The decision (numpy, 11–38 ms on two hours of
material) runs again on every adjustment. Without that split the interface
would be unusable.

The interface is Python and a local web page rather than SwiftUI: the analysis
code is already Python, and adding video playback later needs no change to the
decision layer.

## HTTP interface

The interface uses these; they work for scripting too.

| | |
|---|---|
| `GET /api/state` | media, roles, controls, envelope progress |
| `POST /api/settings` | controls in, cut list and preview out |
| `POST /api/export` | writes the cut XML, returns the path |
| `POST /api/reload` | re-reads the source XML from disk |
| `POST /api/open` | switches to another XML by path |
| `POST /api/pick` | opens the Finder dialog and returns the chosen path |
| `POST /api/mix` | starts audio processing in the background |
| `GET /api/thumb?track=` | a frame from that track's camera file |
| `GET /api/defaults` | factory settings, for the reset buttons |
| `POST /api/language` | switches the interface language |

```
curl -s -X POST localhost:8731/api/export \
     -H 'Content-Type: application/json' -d @settings.json
```

`POST /api/settings` returns `ok: false` and a readable `problems` list while
the roles are incomplete. That's a normal intermediate state, not an error.

## Language

The interface is available in English and Finnish; the switch is on the title
bar. The default comes from the system (`AUTORAFFKAT_LANG`, `LANG`), and the
choice is saved with the settings and inherited from episode to episode.

Server messages are translated too: an English interface never shows a Finnish
error. Browser strings live in `server/static/i18n.js`, server strings in
`i18n.py`.

Code, comments and docstrings are in Finnish. They are for the maintainers,
not for users.

## Track list

The track list is a patch bay. Each row is one speaker: their camera on the
left, their microphone on the right, their name typed once in between. A short
cable is drawn across when both ends are in place, and a gap where one is
missing.

Roling is done by moving cards, not by choosing from menus. Drag a card into a
slot — or click the card and then the slot, which does the same thing and works
from the keyboard. Where the card lands is what it becomes: a camera in a
speaker's slot is that speaker's close-up, a sound file is their microphone.
The top row is for the tracks that belong to everyone, the wide shot and the
room tone. Anything you haven't placed waits in **Unused** below the bay, and
dragging a card back there takes it out of the edit.

The name is typed once per speaker, on the row, so a close-up and a microphone
cannot drift apart over a typo. Drop a card on **+ new speaker** and a new row
appears, named and ready to rename.

Picture cards show a frame from the middle of the file. In a multicam the
angles are called `1`, `2` and `3`, and the filename doesn't say which speaker
a camera is pointed at either, so without a picture the roling is guesswork.
The frame is extracted on request and cached.

Each card states what the file actually is: dimensions, frame rate, codec and
bit rate for picture; channels, sample rate and bit depth for sound; plus the
combined duration and size of every part. A microphone card carries its own
sensitivity and gain.

## Controls and defaults

Every value is adjustable and every one has a default that works without
adjustment. The defaults were chosen by measuring real material rather than by
guessing — the reasoning is in [`DESIGN.md`](DESIGN.md).

The only hard-coded values are the channel strip's internal dynamics: the
compressor ratios and times, and the peak ceiling. Their thresholds are
adjustable, and the threshold is the part that changes with the material.

"Reset to defaults" in either group restores the factory values. It exists
because settings are inherited by the next episode: without a way back, one
bad value would travel forever. Roles, speakers and the project name are kept
— those are work, not tuning.

## Output

One cut track on the spine. The cameras' own audio is disabled
(`srcEnable="video"`), microphones are attached as continuous connected clips
on lanes −1, −2, … with their own `dialogue.<speaker>` roles. Cut points are
quantised to frames so the timeline has no gaps and no overlaps. All time is
carried as `Fraction`, because floating-point rounding error accumulates over
thousands of frames.

From a multicam source the output is a multicam edit: one `<mc-clip>` per
shot, so the angle can still be changed by hand in Final Cut afterwards.

## Audio

An unprocessed microphone is typically −40 LUFS, which isn't worth exporting
as-is. "Process audio" runs the microphones through a channel strip:

1. **External plug-in** (VST3 or AU) first — this is where noise reduction and
   restoration happen
2. **High-pass** removes rumble
3. **Click removal** cleans up lip smacks
4. **Normalisation** to the target loudness, measured on the cleaned signal
5. **Compression** in two stages, fast and slow
6. **Level correction and peak ceiling**

Normalisation is deliberately fourth: compressor thresholds are absolute
decibels, and a −12 dB threshold is never crossed on a −40 LUFS track. The
level is measured again after compression, because LUFS gates quiet passages
relative to the whole and compression moves the reading.

**There is no noise reduction in the chain.** The high-pass removes rumble and
the de-clicker removes lip smacks, but broadband noise is untouched. That's the
plug-in's job — a good speech restoration tool (dxRevive, for example) does it
better than anything worth writing here.

Note that normalisation raises the noise floor too. A typical lift is
+20…+26 dB, and the interface shows the figure actually applied.

Two rules keep picture and sound together:

* **The original is never touched.** Processed audio is written beside it as
  `mic [mix].wav`, and the export points at that.
* **The sample count never changes and the audio never shifts.** Length is
  checked twice and any shift is measured by cross-correlation — a plug-in can
  report its latency incorrectly and return a correct-length but misplaced
  track. Anything that deviates is discarded.

Analysis always runs on the raw audio, because compression raises the noise
floor and flattens the difference between microphones — precisely the two
things sensitivity and the overlap rule depend on.

**Room tone**: one camera track can be extracted as its own audio track and
attached with the role `effects.Tilaääni` at a set level below speech. It is a
connected clip rather than an angle, so it continues across cuts. It is never
compressed: compressed room tone pumps.

### Ducking the other microphone

Optional. While one person talks, the other's microphone is attenuated —
9 dB by default, not muted.

The default is deliberately shallow. The bleed measures about 13 dB below the
speech, so a deeper duck changes the sum by less than 0.1 dB: −9 dB and −15 dB
differ by an average of 34 dB below the mix. The benefit comes from the timing,
not the depth, and a shallow duck does less damage when detection is wrong. Go
deeper only if the microphones are close together or the room is lively.

The control comes from **the same speech detection that drives the picture** —
what you see in the preview bar, already tuned with the sensitivity sliders.
That is the whole idea: the hard part of a gate is the detection, and it is
already done and already on screen.

A threshold alone is not enough. Two microphones in one room hear both
speakers, so both cross the threshold nearly always — measured at 41 % of the
time simultaneously. The bleed is clearly quieter though, so only the
**loudest** microphone stays open, plus any within the "gap to the loudest"
setting. At six decibels both stay open only 6 % of the time, and that is real
overlapping speech.

**Ducking only ever happens underneath the other person's speech.** If nobody
is talking, every microphone stays open. A gate closing into silence is always
audible because nothing masks it; under the onset of another voice it
disappears. The fade down is therefore timed to the other person's speech
starting, **without lookahead**, and the fade back up completes before the
masking voice stops. The fades are slow — 0.25 s down, 0.4 s up — precisely
because they are hidden and don't need to be fast.

The time controls are what make the gate usable:

* **Shortest opening** drops runs that are too short: a cough doesn't open the
  microphone.
* **Lookahead** opens the gate before speech starts. This is only possible
  because processing is offline — a real-time gate cannot open before the
  sound has arrived, which is why words go missing.
* **Hold** keeps the gate open after speech, so the tail of a sentence and the
  breath stay in.
* **Shortest duck** prevents dips under half a second: those read as clicks,
  not ducking.

### Order: process, export, edit

In that order, and for these reasons:

**Microphone audio cannot lose sync in Final Cut.** It goes into the export
inside the multicam clip (`mc-source srcEnable="audio"`), so picture and sound
move together no matter how you edit.

**Room tone can.** It is a connected clip on lane −1, because `mc-source` has
no level control. If you remove a range from the timeline and close the gap,
the storyline shortens but the connected clip does not — the room tone shifts
by the deleted amount. Blade it at the same point, or leave it off if you plan
to edit heavily.

**Exporting mid-processing is intact but unprocessed.** Files are written
through a temporary, so a half-written file is never visible — but the export
only references finished ones, so anything still running stays raw audio. You
get a warning after the export button.

**A new export does not carry your Final Cut edits.** It is a new project. So
run processing to completion before you export and start editing.

If you have already imported and want the processed audio, **relink** the
microphone assets in Final Cut to the `[mix]` files. Same length, same start
times, so relinking is safe and your edits survive.

## Limitations

Video playback and waveform drawing are out of scope for this version.

A multicam source produces a multicam edit and an ordinary source a flat edit.
The form follows the source and cannot be switched mid-edit.

## When something doesn't work

| Message or symptom | Cause |
|---|---|
| `ffmpeg is missing from the path` | `brew install ffmpeg` |
| `File not found on disk` in the track list | The XML points at a path that doesn't exist: the material moved after the export, or the export points at proxies. Relink in Final Cut and export again. |
| `No project or synchronised clip found in the XML` | What was exported was an event, for example. Select a synced clip or a project before exporting. |
| `The wide shot and the microphones share no common time` | The roles point at media that don't overlap on the timeline. |
| The bar looks right but Final Cut doesn't | Check the sequence frame rate. It is read from the XML, so a wrong value is in the source. |
| Envelopes are recomputed every time | The cache key includes the modification time. A network volume that rewrites timestamps will never hit the cache. |

## Tests

```
uv run pytest
```

The export is validated against Final Cut's own DTD when Final Cut is
installed — our reader accepts far more than the importer does.

The interface has a smoke test: every render function and every event handler
runs in a stub DOM, in both languages, driven by the state the server actually
produces. It fails if any top-level function is never called, so new code
cannot arrive untested. Node is optional locally — the test skips without it —
but CI requires it.

Test material is synthesised with ffmpeg: sine bursts at known positions, so
the decision can be checked without real footage (`tests/make_fixture.py`).

## Licence

MIT — see [`LICENSE`](LICENSE).
