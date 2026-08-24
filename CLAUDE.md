# autoraffkat

FCPXML in, FCPXML out. The picture cuts to whoever is talking. Nothing is
rendered.

Code, comments and docstrings are in **Finnish** — they are for the
maintainers. Documentation and everything the user sees is in English and
Finnish. Keep it that way.

## Two layers, don't mix them

`audio/envelope.py` is slow (ffmpeg, seconds) and cached to disk. `decide.py`
is fast (numpy, milliseconds) and runs on every adjustment. No file reading may
leak into `decide.py` or into `analysis.build_grid` which it calls — that
breaks the interface response time, which is the single most important
requirement here.

`decide.py` must not loop over individual samples either. Loops walk runs
(`_runs`), of which there are thousands, not samples, of which there are
hundreds of thousands.

## Time is a Fraction

All time read from and written to XML passes through `timeline.py` as a
`Fraction`. Floating point is acceptable only in the analysis layer. The
reason: rounding error accumulates over thousands of frames and leaves gaps on
the timeline.

FCPXML time semantics: a clip's `offset` is in the host's local time base,
whose zero is the host's `start`. A child's absolute position is therefore
`host_absolute + (child_offset - host_start)`. This applies to attached clips
and to sync-clip contents alike, and it is the entire idea behind
`fcpxml/read.py`'s `_walk`.

In a multicam, additionally: the angle's content must be clipped to the
`mc-clip`'s duration (`_walk`'s `bounds`), because an angle spans the whole
multicam and the same multicam can appear on the spine twice.

## A track is not a media file

The unit of roling is `Timeline.tracks`, not `Timeline.media`. In a multicam
the same angle is a different file in each part but one track. Everything that
reads roles, controls or `Segment.angle` speaks in track keys. Without this,
`Roles.wide_key` and `closes` would be lists and every site reading them would
have to handle several keys.

## Roles are inherited between episodes

A new episode with no settings of its own reads the nearest previous
`*.autoraffkat.json` and takes the roles of matching track keys from it. This
is the entire reason a track key is derived from the filename rather than the
angle name or `angleID`: in a series the cameras stay, the angle numbers do
not. Change how the key is derived and inheritance stops working silently.

## Sensitivity and gain are not the same thing

Sensitivity is a threshold above the noise floor, so gain does not move it —
the floor moves by the same amount. Gain only affects how microphones compare
against each other during overlapping speech. Change this and the controls
start interfering with each other.

## Audio: analyse raw, export processed

`audio/mix.py` is the third slow layer. Two things are not negotiable:

Never write over the original. The envelope cache is keyed on modification
time, so overwriting would recompute the curve — and the new computation would
land on processed audio. Analysis is always done on the raw file: a compressor
raises the noise floor between words and flattens the difference between
microphones, destroying exactly the two things sensitivity and the overlap rule
depend on.

The sample count must not change. The export references the processed file with
the same times as the original. The check exists in two places and anything
deviating is discarded. A shift is measured separately by cross-correlation,
because length alone cannot detect a plug-in that reports its latency wrongly.

When an asset's `src` is redirected, the `<bookmark>` must be removed. It is a
macOS file reference that beats `src`, and leaving it would mean Final Cut
opens the unprocessed file without saying anything.

The channel strip is in `audio/chain.py`, on pedalboard. Two places where the
library doesn't do what its name promises, both measured:

* `plugin.process(..., reset=False)` **shortens** the result by the plug-in's
  latency (4641 samples with dxRevive). Always use `reset=True`, and never
  process a file in chunks.
* `pedalboard.Limiter` applies makeup gain: it lifted −20 LUFS to −15.8 and
  peaks to zero. It was replaced by `peak_guard`, a static attenuation that
  never raises.

## Microphone to the angle, room tone to a lane — and why

Microphone audio goes into the export inside the multicam clip (`mc-source`),
so it cannot lose sync no matter how the user edits in Final Cut. Room tone is
a connected clip, because `mc-source` has no level control — and therefore it
**can** drift on a ripple edit. If someone finds a way to make room tone an
angle with a level, that is an improvement.

## Final Cut is stricter than our own reader

The export must be validated against Final Cut's own DTD
(`/Applications/Final Cut Pro.app/.../Interchange.framework/.../FCPXMLv1_*.dtd`,
`xmllint --dtdvalid`). Our reader accepts far more than the importer: once
`tcFormat` was written onto `mc-clip`, which the reader accepted but which
killed the entire import. `clip` and `asset-clip` know that attribute,
`mc-clip` does not.

Derived files do not go inside the `.fcpxmld` bundle but beside it, taking the
bundle's name. The bundle belongs to Final Cut.

An export never lands on an existing file. `project.next_output_path` walks
`-cut`, `-cut v2`, `v3` … until it finds a free name, and `pick`'s
`_OUTPUT_RE` recognises the numbered ones as our own so they are not offered
back as a source. The reason is not tidiness: the previous export is usually
already imported into Final Cut and edited by hand, and that work has no other
source to be rebuilt from.

The name also carries the settings (`project.name_tag`): the rhythm preset
always, deviating controls after it, `audio` when the microphones were
processed. `_OUTPUT_RE` therefore accepts a tag between the suffix and the
number — but only words the tool writes itself, so a foreign
`interview-cut down.fcpxml` is still a valid source. The numbering runs within
one tag: a cut made with different controls is a new file, not a new version.

The whole settings set goes into the exported XML as well. The DTD says
`sequence (note?, spine, metadata?)`, so the `<note>` goes before the spine and
the `<metadata>` after it — the order is part of the rule, not a style choice.
The note is translated (it is a user-visible Final Cut field); the `md` keys
are not, they are machine-readable and prefixed `fi.autoraffkat.`.

## User-visible text is translated, code is not

Everything the user reads goes through translation: server messages via
`i18n.py`'s `t()`, browser strings via `static/i18n.js`'s `T()`. A new error
message means a new key in both languages — a hard-coded string shows up in the
wrong language and nobody notices until a user complains.

Code, comments and docstrings stay in Finnish. They are for the maintainers.

The language is a `ContextVar`, not a global: audio processing runs in a
background thread while the interface is asking for state.

## The pair is a row, not a drawing

A close-up and its microphone are one thing, and the interface has to say so
without being read. It is a patch bay: one **slot** per row — video cell on the
left, audio cell on the right, the speaker's name once in the strip between
them. The pair is adjacency, so the cable is a horizontal line in a fixed-width
strip. It is CSS, not geometry: nothing is measured, nothing is redrawn on
hover or resize, and a crossing cable is not possible to express. The whole
`drawCables` / `chipEls` / `getBoundingClientRect` machinery that the two-list
layout needed is gone, and it should not come back.

The top row is for the tracks that belong to nobody: the wide shot and the room
tone. They are shared by the whole episode the way the other rows belong to one
person. Unassigned tracks live in a tray below the bay, not as rows — a track
with no slot has no pair and therefore no row.

**A slot sets the role.** `assign()` is the only place that writes
`config.role` and `config.speaker`, and it derives both from where the card
landed: video into a speaker slot is `close`, audio is `mic`, video into the
shared slot is `wide`, audio into it is `audio.room_track`, and the tray is
`unused`. There is no role menu any more. Add a new role and it needs a place
to sit, not a new option in a list.

**The name is written once.** It lives on the slot, so a pair cannot break by a
typo on the second track — which is what the old per-track text field made easy
and invisible. Renaming a slot writes to every member track.

**Drag and click are one path.** `picked` holds the lifted track key; both
`dragstart` and a click on a card set it, and every drop target reads it.
`dataTransfer` carries the key too, but only as the native affordance — a
browser will not let `dragover` read it, and the keyboard has no `dataTransfer`
at all. `dropTarget()` wires all four events in one place so the mouse and the
keyboard cannot end up disagreeing about what is allowed.

Below 900 px the two columns cannot sit side by side. Then the slot stacks —
name first, then its video and audio cards — which is the same grouping in a
different direction, and the connector is hidden because adjacency already says
it.

## The interface has a smoke test, and it is not optional

`node --check` validates syntax only, so it does not notice an undefined
variable. One got through: `renderAudio` referenced a `busy` variable that had
been removed, which aborted the whole render — "Reload" span forever and the
console showed nothing but a `ReferenceError`.

`tests/ui_smoke.js` loads `i18n.js` and `app.js` into a stub DOM and runs every
render function in both languages, with audio processing on and off and with
processing in progress. The state comes from the server for real
(`_state_json`), so a field renamed at only one end fails here too.

Three things keep it honest, and none of them are decoration:

* `test_smoke_catches_an_undefined_variable` injects a broken reference and
  asserts the harness notices. A smoke test that passes everything protects
  nothing.
* The harness fires every registered event handler. Rendering alone runs about
  half the file; clicks, selects and text fields are the other half, and that
  is where an undefined variable hides. It fires one generation at a time:
  handlers that redraw the track list create a whole new set of elements, and
  firing the detached ones again on the next pass multiplies them until node
  runs out of heap. The next pass renders the same interface anyway.
* Every top-level function is wrapped in a counter, and the run **fails** if
  any was never called. Add a function without covering it and the test says
  so by name. Anything genuinely unreachable goes in `NEVER_CALLED_OK` with a
  reason.

CI (`.github/workflows/tests.yml`) runs the suite on macOS with ffmpeg and
Node, and fails if the interface smoke test skipped — a silent skip would
leave exactly this class of bug unguarded.

Note when writing the harness: `let state` in `app.js` is a lexical binding,
not a property of the global object, so `context.state = ...` does not reach
it. Assign it from inside the context.

## Static files are versioned

`index.html` is served with `app.js`, `i18n.js` and `style.css` given their
modification time as a query parameter. Without it the browser serves an old
stylesheet with a new script, and the layout breaks in a way nobody connects to
caching. This happened once already.

## Tests

`tests/make_fixture.py` synthesises the material with ffmpeg: sine bursts at
known positions (`SPEECH_A`, `SPEECH_B`). The project fixture starts at second
1 of the source, the sync clip at zero — comparisons must use the
`source_to_timeline` conversion, not raw numbers.

`multicam.fcpxml` is the same material as two parts: the parts' files are
copies, because grouping looks at the filename rather than the content. There a
timeline moment equals a file moment, so `source_to_timeline` is the identity —
unlike in the project fixture.

Settings are written beside the XML, so tests that export or save need the
`scratch_xml` fixture rather than the shared `fixture_dir`.
