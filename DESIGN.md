# Design notes

Why the code is split the way it is. `README.md` covers how to use the
application, `CLAUDE.md` what must not be broken.

*[Suomeksi](DESIGN.fi.md)*

## The requirement that determines everything

The user's loop is:

1. sync in Final Cut, export the XML
2. name the tracks, move the sliders
3. export the XML, import into Final Cut, watch
4. if it isn't right, back to step 2

Between the adjustment in step 2 and the export in step 3 there is room for
about one second. Two hours of material is 360 000 analysis steps, and
decoding the audio with ffmpeg takes minutes. Neither can happen while
adjusting. Every other structural decision follows from that.

## Layers

```
   FCPXML  ──►  fcpxml/read.py  ──►  Timeline (MediaItem + Placement)
                                          │
                    ┌─────────────────────┴──────────────────────┐
                    │                                            │
              audio/envelope.py                            the user's roles
              ffmpeg + RMS 20 ms                           and controls
              disk cache                                        │
              SECONDS                                           │
                    │                                            │
                    └──────────►  analysis.py  ◄─────────────────┘
                                  align onto the grid
                                  MILLISECONDS
                                          │
                                          ▼
                                     decide.py
                                     thresholds, durations, overlap
                                     MILLISECONDS
                                          │
                          ┌───────────────┴───────────────┐
                          ▼                               ▼
                    preview.py                     fcpxml/write.py
                    bar for the browser            new project
```

The boundary runs between `envelope.py` and `analysis.py`. Everything below it
is re-run every time a slider moves.

Measured on a two-hour input: `decide.py` 11 ms (rule *wide*), 38 ms (rule
*louder wins*, which adds a sort across speakers). A full server round trip on
the fixture is 4.5 ms.

## The envelope

`envelope.py` decodes the audio with ffmpeg to mono at 8 kHz and computes RMS
in decibels every 20 ms. 8 kHz is plenty for speech energy and quarters the
decode time compared with 48 kHz; frequency response is irrelevant because the
decision only looks at level.

Decoding is streamed in 82-second chunks, so a two-hour file never allocates
230 MB. The result is 360 000 float32 values, or 1.4 MB.

The curve is indexed **from the start of the file**, not the timeline. The same
cache then stays valid when a clip moves on the timeline or the same file
appears in several projects. The shift onto the timeline happens later, in
`analysis.align`.

The cache key is the path, file size, modification time and the analysis
parameters. A replaced file therefore never hits a stale curve.

## Alignment

`analysis.align` maps the curve onto the timeline grid. A media file can appear
on the timeline in several pieces (`MediaItem.placements`), so alignment is
done piece by piece: within each piece the mapping is linear, so it is one
`np.arange` and one indexing operation.

The same pass produces a `valid` mask marking where the grid has any media at
all. Without it, a missing region would look like silence, which is not the
same thing.

## Sensitivity and gain

Sensitivity is a threshold **above the noise floor**, not an absolute decibel
value:

```
on = db > floor + sensitivity
```

Gain is added to the decibels, but the noise floor moves by the same amount, so
gain cancels out of the condition above. It therefore only affects which
microphone counts as louder:

```
level = db + gain          # only for the overlap comparison
```

This is deliberate. If gain also moved the threshold, two controls would do
partly the same thing and tuning would become guesswork.

The noise floor is the 20th percentile of the material. It is computed once
during alignment and cached, because it doesn't depend on the controls.

## The decision

`decide.py` never loops over samples. First numpy produces a `want` array — the
desired shot at each moment, with no duration constraints — and then a loop
walks its **runs** (`_runs`). Two hours of material has thousands of runs and
hundreds of thousands of samples.

The order:

1. **Confirm time.** Speech runs shorter than the confirm time are dropped
   (`_open_runs`). Pauses shorter than the confirm time are filled
   (`_close_gaps`) so word gaps don't fragment a run.
2. **1/f Dynamic Tempo Modulation.** `_compute_tempo` computes turn-taking rate
   across a rolling 45s window, scaling effective minimum dwell times $\tau$
   during fast banter vs slow passages.
3. **One person talking** → their close-up.
4. **Several talking.** If the overlap is shorter than `min_overlap` it is
   backchannelling rather than overlapping speech: pick the louder one and
   don't trigger the rule. Otherwise apply the chosen rule.
5. **A speaker with no close-up** → wide. **A close-up that isn't on the
   timeline here** → hold current.
6. **Duration constraints & Split Edits (J-cut / L-cut)** in the run loop:
   `lead` (J-cut) moves the camera cut before speech onset to capture breaths and
   reactions; `hang` (L-cut) holds the outgoing speaker across pauses.
   Effective shortest shot stops cuts landing too close to the previous one.
   If the two together push the cut past the run, no cut is made.
7. **Long turn & Breath-Snapped Punctuation** (`_force_wide` + `_find_breath_point`).

#### Which of lead and hang wins

They are two edges of the same cut point. `lead` pulls the cut earlier, ahead
of the incoming voice; `hang` is a floor that keeps the outgoing speaker's face
on screen after their own speech has ceased. The cut lands at the later of the
two, so the pause length decides: after a long pause the lead wins and the cut
anticipates the new speaker (J-cut), in a fast handover the hang wins and the
old face stays over the new voice (L-cut). With the broadcast defaults the
crossover is a pause of about 0.9 s, which is longer than a normal conversational
gap — so most handovers are L-cuts and only real pauses get the anticipation.

The floor applies only when the outgoing speaker has actually stopped. During
overlapping speech they are still talking, the cut is not caused by them
finishing, and there is no tail to give: the overlap rule cuts on time. The same
goes for the first cut of the programme, where the outgoing shot is the wide and
there is no face to linger on.

A reply shorter than the hang gets no cut at all — the floor pushes the cut past
the end of that speaker's run, so the picture stays where it is. That is the
same mechanism as the confirm time, from the other end.

### Long turn and Reaction Shots

Steps 1–6 produce the right shot but not a rhythm: a monologue gives one
close-up for as long as the speech lasts, and to a viewer that is a minute of
the same face. Once the same speaker has held the floor for `wide_every`
seconds, the picture cuts at the nearest natural breath or pause.

There are three ways to continue, because they are editorially different things
and neither is always right:
* **Return to speaker** lets the wide last `wide_hold` and returns to the same
  shot; the rhythm stays with the speaker, which suits a conversation where
  monologue is the exception.
* **Stay wide** holds the wide until the next turn; a long monologue reads as a
  situation rather than a face, and there are markedly fewer cuts.
* **Reaction shot** cuts to a silent co-host's close-up for `wide_hold`, then returns
  to the active speaker. (Falls back to wide if no second close-up exists).

The choice is taste, so it is a control rather than a constant.

This runs on the finished cut list, not on the `want` array. It is a rhythm
rule rather than an observation about who is talking, and it must not get
mixed into the thresholds: `want` still says whose turn it is, and
`_force_wide` decides separately whether to show it.

The wide duration is always raised to at least the shortest shot. Otherwise the
control would produce flashes that no other rule would allow — and the minimum
duration is the strictest promise the decision makes.

## Time

All time read from and written to XML is a `Fraction`. The reason shows up in
`test_quantize_is_exact_over_many_frames`: at 29.97 fps a frame is 1001/30000
seconds, and as a float the error accumulated over 216 000 frames is enough to
move a cut to the wrong frame. The timeline would be left with gaps, and Final
Cut shows gaps as black.

Floating point is acceptable only in the analysis layer, where the 20 ms grid
is coarser than a frame anyway.

### FCPXML time semantics

A clip's `offset` is **in the host's local time base**, whose zero is the
host's `start`. A child's absolute position on the timeline is therefore:

```
child_absolute = host_absolute + (child_offset - host_start)
```

This applies to attached clips and to the contents of a `sync-clip` alike, and
it is the entire idea behind `read.py`'s `_walk`. The same rule in reverse
explains why `write.py` gives the microphones' connected clips an offset equal
to the first spine clip's `start` rather than zero.

### Multicam

`<mc-clip>` is the host, the content lives in the angles of
`<media><multicam>`, and the zero of the angles' time base is the multicam's
`tcStart`. The same rule applies, with one addition: **the angle's content must
be clipped to the `mc-clip`'s duration**. An angle spans the whole multicam, so
without clipping two parts of the same multicam would produce overlapping
placements, the envelope would align to the wrong place, and coverage would
claim picture where there is none. That is `_walk`'s `bounds` parameter, and it
applies to `ref-clip` too.

### A track, not a media file

The unit of roling is a **track** (`Timeline.tracks`), not a media file. On an
ordinary timeline the difference is invisible — each media file is its own
track and the key is the filename as before — but in a multicam the same angle
is a different file in each part, and they belong to the same role, the same
control and the same speaker.

Without this, `Roles.wide_key` and `Roles.closes` would all have to become
lists, and every site reading them would have to handle several keys. A track
handles it in one place: the decision layer still sees one key per shot, and
coverage is the union of the track's parts.

Grouping is by angle name (`"1"`, `"host a Track2"`), because that is the
editor's own marking that this is the same camera. The key, however, is derived
from the filenames, because names and `angleID`s change from one export to the
next. Two angles of the same multicam are never merged, even if their names
normalise to the same string.

## Writing

One spine, one clip per shot. The cameras' own audio is disabled with
`srcEnable="video"`. Microphones are connected clips on the first spine clip,
on lanes −1, −2, … with roles `dialogue.<speaker>`.

### The settings travel with the cut

The name of the export carries the rhythm preset and every control that
deviates from its default (`episode-cut custom 3s louder stay audio.fcpxml`).
It is not decoration: in Final Cut's browser the file name is the only thing
that separates one rough cut from another, and the loop produces several per
episode. `pick` knows the words it writes itself and only those, so a foreign
name that happens to end in `-cut` is still offered as a source.

The complete settings go inside the file. The DTD says
`sequence (note?, spine, metadata?)`, so both have a place and the order is
part of the rule: the `<note>` is a translated one-line summary — the version,
the rhythm, the shot lengths, the rules, whether the microphones were
processed — and the `<metadata>` block holds one `<md>` per control plus the
whole settings JSON under `fi.autoraffkat.settings`. The reverse-DNS prefix is
Apple's convention and keeps the keys clear of Final Cut's own. The XML travels
from machine to machine; the settings file does not necessarily travel with it.

### Writing multicam

From a multicam source the output is one `<mc-clip>` per shot: the picture
angle `srcEnable="video"`, the microphone angles `srcEnable="audio"` with their
own `dialogue.<speaker>` roles, and the camera's own audio `active="0"`. The
result is a native multicam edit, so the angle can still be changed by hand in
Final Cut afterwards — which a flat edit no longer allows.

Resources are **copied from the source XML verbatim** rather than rebuilt. The
multicam's angle structure, the `angleID`s and the assets' mutual sync are
exactly the part that must not change, and copying is the only way to guarantee
that.

A shot must not continue from one part into the next: the next part is a
different `<mc-clip>` with different `angleID`s. So the quantised spans are
split again at part boundaries (`_split_spans`), and each piece gets its own
`start` in its own part's time base.

Quantisation (`_quantize`) is more careful than it looks. It walks the segments
forward keeping a cursor, which guarantees each shot gets at least one frame
and that the next start is always greater than the previous. Each segment's end
is the next one's start, so gaps cannot appear. If there were more cuts than
frames — which the decision layer does not produce, but which must not be
written broken either — the rest are dropped and the previous shot continues
over them.

## Audio

The third slow layer: `audio/chain.py` does the signal processing,
`audio/mix.py` decides what gets processed and guards the sync.

The chain originally ran in a sibling project's environment (automixer) via
`uv run --project`, because it required Python 3.13 and MLX. That dependency
was removed: the part actually needed was small, and pedalboard does it
directly in the same process. The version requirement and the process boundary
went with it.

Two places in that library where the name doesn't match the behaviour turned
up, and both would have shipped unnoticed without the length check:

* `plugin.process(..., reset=False)` drops the plug-in's latency worth of tail
  — 4641 samples with dxRevive. The result sounds correct but is too short.
  Hence `reset=True`, and no chunked processing.
* `pedalboard.Limiter` applies makeup gain. It lifted an already-normalised
  track from −20 LUFS to −15.8 and pushed peaks to zero. It was replaced with
  `peak_guard`: a static attenuation that only ever reduces, and only when the
  ceiling is exceeded.

A third turned up in the ported `declick`: the original compared HF energy
against a local **maximum**, although the comment said mean. A click is by
definition the maximum of its own neighbourhood, so the condition could never
be true and the whole operation was a no-op. With a mean it works.

### Plug-in parameters are stored in the plug-in's own units

The chain has no noise reduction of its own — that is the external plug-in's
job, and a plug-in without its parameters is whatever preset happened to be
its factory default. `AudioSettings.plugin_params` is a `{name: value}` map,
and the value is in the plug-in's own units (`plugin.input_gain = 3.0` means
three decibels), not the 0–1 raw value the format underneath actually uses.
Two reasons: the raw-to-displayed mapping is not always linear and pedalboard
already knows it, and a decibel figure is readable in the settings file and in
the exported XML's metadata, where a `0.5625` would tell nobody anything.

Only touched controls are stored; the rest stay at the plug-in's defaults, so
the settings file does not fill up with values that were never chosen.

Two rules keep a wrong value from reaching the plug-in silently. A name is
checked against `plugin.parameters` before it is written, because pedalboard's
plug-in object accepts *any* attribute — an unknown name would look like it
took effect and change nothing. And an unknown or out-of-range name is skipped
rather than raised: settings are inherited from the previous episode, whose
plug-in may have been a different one, and the right behaviour there is to run
the plug-in on its own defaults, not to fail the whole run.

Listing the controls loads the plug-in, which takes seconds, so it is a request
of its own (`/api/plugin-params`) and not part of the plug-in list — that one
has hundreds of entries. The result is cached by path: a plug-in does not
change while the program runs.

### Why analysis runs on raw audio

A compressor does two things, both of which degrade the decision. It raises the
noise floor between words, and sensitivity is a threshold **above the floor**.
It flattens the difference between microphones, and the *louder wins* rule
compares microphones against each other. Processed audio is therefore better to
listen to and worse to measure, so the layers are kept apart: analysis reads
the original, the export points at the processed copy.

### Normalisation before compression

Compressor thresholds are absolute decibels. An unprocessed podcast microphone
is easily −40 LUFS, at which a −12 dB threshold is never crossed and the whole
chain is a no-op. So every microphone is measured and lifted to the same
loudness first.

The target is a **stem** target rather than a programme one: −20 LUFS, not −16.
One person speaks at a time, so the sum lands close to the same figure, and the
final level is set in Final Cut. Applying a programme target to every track
separately would produce a sum that is far too hot.

The level is measured again after compression. LUFS gates quiet passages
relative to the whole, so when compression lifts quiet material a different set
of blocks passes the gate and the reading rises — measured at 2.2 dB above
target. The correction is therefore applied afterwards, and the peak ceiling
after that.

### The sample count is the whole sync promise

The export references the processed file with **the same times** as the
original. A single sample added or dropped separates picture from sound, and
the error isn't noticeable until the result is finished. So the length is
checked in the chain and again from the written file with ffprobe, and anything
that deviates is discarded unused.

The same reasoning determines what was left out of the original chain: the **ad
break** shifts the track, and **summing** would remove the separation between
speakers and with it the `dialogue.<speaker>` roles. Neither belongs here.

A shift is measured separately by cross-correlation, because the length check
cannot see it: a plug-in can report its latency incorrectly and return a
correct-length but entirely displaced track. The correlation is computed on
envelopes rather than waveforms — a plug-in changes the content but not the
rhythm of the speech. dxRevive measured 0 samples.

The correlation must be an FFT. `np.correlate(..., "full")` computes it
directly, which is O(n²): on a millisecond grid a 20-minute file is 1.2 million
bins and the check took **132 seconds** — longer than dxRevive spent on the
same file — while an hour-long file would have been a quarter of an hour of
checking alone. The FFT gives the identical answer in 0.05 seconds. There is a
test that fails if the order of growth comes back.

### Progress is weighted, and the stage is the resolution

Processing runs for minutes in a background thread. `2/4` says nothing when one
file is 20 minutes and the next is 64, so files are weighted by size and the
estimate is computed from the weighted fraction — which means it exists from
the first stage rather than appearing only after the first file finishes.

Within a file the plug-in cannot be asked how far along it is: it processes the
file in one piece, because chunking would shorten the result. So the stages are
the resolution available, with measured shares — the plug-in is around 95 % of
a file's work when there is one, and the picture is entirely different when
there is not. The numbers are not exact and cannot be; they exist so the bar
moves during an hour-long file instead of standing still for ten minutes.

Processing also logs each file and stage to the terminal. When it is slow or it
fails, the question is always which file and which stage.

### Redirection happens at the resource level

In a multicam export `<resources>` is copied from the source, so the processed
audio is put in place by changing the asset's `media-rep src`. Angles and
`mc-source`s reference the asset, so the cut list never needs touching.

The `<bookmark>` must be **removed** at the same time. It is a macOS file
reference that beats `src`: leaving it would mean Final Cut opens the original
unprocessed file without saying anything.

### The raw microphone travels with the cut, muted

Redirection leaves no reference to the original, and that is a one-way door:
the plug-in's mark is heard only by listening, and by then the cut has usually
been imported into Final Cut and edited by hand. A fresh export would not bring
that work along.

So every processed microphone angle gets a twin in the multicam that carries
the untouched file, `active="0"` and on its own subrole `dialogue.<Speaker>
raw`. Enabling it in the Audio inspector is the way back.

The twin is a **copy** of the angle rather than a new one built from scratch:
it inherits the angle's times and its `<bookmark>`, so it points at the
original file and is in sync to the sample. Only `angleID`, the visible name
and the asset references change. The copy is taken before the redirect, which
is why the original `src` does not have to be reconstructed afterwards.

A separate subrole is deliberate: if the twin is switched on it must be
adjustable on its own, otherwise it would sum with the processed track under
one fader.

In a flat export there are no angles, so the twin is a connected clip on its
own lane with `enabled="0"`. Twins go **below** everything else — after the
microphones and the room tone — so that turning processing on does not move
the microphone the editor is looking at on lane −1. Interleaving each twin
under its own microphone would do exactly that.

Its asset is otherwise identical to the processed one — same media, same
format — because it is the same file. Unlike the room tone it is not stripped
to audio: there the source is a camera and the result a WAV, here both are the
same file and the asset has to say the same thing about it.

Only a processed track gets a twin. Without processing there is nothing to
fall back from, and an extra disabled lane under every microphone would be
noise.

### Ducking uses the picture's speech detection

A microphone gate is classically hard: the detection flickers across word gaps
and reacts to a cough. Here the detection already exists, tuned with the
sensitivity sliders and inspected in the preview bar — the same
`SpeakerLanes.on` that decides the picture. The gate gets its control for free.

Two things still had to be added.

**The loudest wins.** A threshold alone doesn't separate speakers: two
microphones in one room hear both, and in the measured material both crossed
the threshold 41 % of the time simultaneously. The bleed is clearly quieter
though — a median difference of 12.8 dB — so only the loudest microphone is
left open, plus any within `duck_dominance_db` of it. At six decibels the
overlap fell from 41 % to 6 %.

**Ducking only under a masking voice.** The first version ducked whenever a
speaker was quiet, and it sounded terrible: 20-millisecond dips, 13–33 ducks
per minute, and fades in the middle of silence. A gate is always audible when
nothing masks it.

Ducking can now only exist while **some other microphone is open**. The fade
down is timed to the other person's speech starting, without lookahead — the
fade must not begin before the masking sound has arrived — and the hold plus
the release length are trimmed off the end of the masking run so the fade up
also happens under the masking voice. Measured, ducking during silence fell
from 5.4 % to 1.6 % and from 12.7 % to 5.2 %, and what remains is
within-sentence pauses where the other speaker is clearly still mid-sentence.

The fades are in decibels rather than amplitude, because hearing is
logarithmic: a linear ramp is nearly all the way down at its midpoint and reads
as a step. They are also asymmetric and slow — 0.25 s down, 0.4 s up — because
a hidden fade gains nothing from being fast.

Depth is a control but almost irrelevant: the bleed is already ~13 dB below the
speech, so −9 dB and −15 dB differ by less than 0.1 dB in the sum and the
difference signal averages 34 dB below the mix. This also explains why the
first version sounded bad although the level barely moved: what was audible was
artefacts, not attenuation. The default is therefore a shallow −9 dB, which
does least damage when the detection is wrong.

**Own timings.** The picture waits for a confirm time before cutting; the gate
must open immediately. `open_windows` drops runs that are too short
(`min_open`, a cough), opens ahead of time (`lookahead`) and holds afterwards
(`hold`). The lookahead is only possible because processing is offline —
its absence is exactly what makes a real-time gate eat words.

`_close_gaps` was dead code in `decide.py` until then. Filling word gaps now
happens implicitly: lookahead and hold expand runs in both directions, and
adjacent ones merge.

Ducking is applied at sample level per run rather than as a gain curve spanning
the whole file: an hour-long microphone is 184 million samples, and a float
array on top of that would be three quarters of a gigabyte. There are thousands
of runs.

### Room tone is a connected clip, not an angle

The picture angle changes at every cut; room tone has to continue across them.
So it is not an `mc-source` but an `<asset-clip>` on lane −1 with its own role,
attached to the first clip — the same structure as the microphones in a flat
edit. The camera's audio is extracted with ffmpeg into the cache first, because
the audio reader cannot open mp4.

## The interface

FastAPI and plain JavaScript with no build step. The browser holds state only
for the controls; the decision always runs on the server, because it is numpy.

Moving a slider doesn't send a request immediately but after 45 ms, and the
previous request is cancelled with an `AbortController`. Dragging therefore
never builds a queue.

The preview bar is summarised on the server (`preview.py`) into about 1400
columns. On a speaker row a column is "talking" if the speaker is talking
anywhere within it — otherwise short lines would vanish in the summary. On the
chosen-shot row the middle of the column is taken, because there the prevailing
value is what matters.

Static files are served with their modification time as a query parameter.
Without it the browser serves an old stylesheet with a new script, and the
layout breaks in a way nobody connects to caching. This happened once already.

### Why not SwiftUI

AVFoundation would have provided playback and waveforms out of the box. Against
that, the analysis would have had to be rewritten in Swift or run as a Python
subprocess — two languages and IPC from the first version onwards. In a tool
this size that costs more than it brings.

### How playback would be added later

The decision layer needs no changes. `preview.py` already returns timeline
seconds and `decide.py` knows nothing about the interface. What's needed:

1. proxy file generation with ffmpeg (into the same cache directory)
2. a route serving the proxy with `Range` support
3. a `<video>` element and a playhead over the bar
4. switching source at cut points, since one `<video>` cannot show two cameras
   — in practice two stacked elements with one preloading
