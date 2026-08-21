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

## User-visible text is translated, code is not

Everything the user reads goes through translation: server messages via
`i18n.py`'s `t()`, browser strings via `static/i18n.js`'s `T()`. A new error
message means a new key in both languages — a hard-coded string shows up in the
wrong language and nobody notices until a user complains.

Code, comments and docstrings stay in Finnish. They are for the maintainers.

The language is a `ContextVar`, not a global: audio processing runs in a
background thread while the interface is asking for state.

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
  is where an undefined variable hides.
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
