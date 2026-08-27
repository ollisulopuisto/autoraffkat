# Shared speech-mixing pipeline — brief for sibling projects

Context for whoever is reading this in **automixer** or in the new
session-based project: `autoraffkat` (FCPXML in, FCPXML out, picture cuts to
whoever is talking) has spent a long session measuring and correcting its
audio chain. Three projects now want the same chain. This document is the
proposal for how to share it, and — more importantly — the findings that must
travel with it.

Everything below is measured on real 77-minute two-microphone podcast
material. The numbers are the argument; please don't re-derive them.

---

## 1. Where the seam already is

`chain.py` **is already a library.** It takes `(audio ndarray, rate, settings,
gain_db, speech_flag, target_lufs, plugin, speech_mask)` and returns an array.
It has never heard of FCPXML, timelines, or files. Same for `debleed.py` and
`worker.py` (the plug-in host child process).

The impure module is `mix.py`. It knows about job dicts carrying
`item.placements`, sibling output paths, freshness stamps, and grid→file time
conversion. That is where the host format leaks in, and that is the only line
that needs drawing.

### The abstraction is "a track with a placement on a programme timeline"

Not "an FCPXML asset". Everything `mix.py` needs from the host is:

```
Track:
    path: str
    speaker: str            # who this microphone belongs to
    mono: bool              # always True for microphones, see §3.6
    bit_depth: int
    spans: [(programme_start, programme_end, file_offset)]
```

An FCPXML asset is that. An automixer session track is that. Whatever your
session format is, it is that. The conversion between programme time and file
time is linear inside each span, and that single formula is all the timeline
knowledge the pipeline needs:

```
file_time = span.file_offset + (programme_time - span.programme_start)
```

### The second seam: compute gain decisions, don't apply them

This one only became visible late and it is the more valuable of the two.

`duck_envelopes(grid, settings, program_start)` returns
`{speaker: [(time, dB), ...]}`. autoraffkat writes those into the FCPXML as
Final Cut `<adjust-volume>` keyframes, so the editor can still change them.
automixer has nothing downstream to write automation into, so it would bake
the same curve into samples. **Same computation, different emission.**

So the library should return gain *decisions* and let the host decide whether
they become samples or automation. The general rule that fell out of it:

> Level decisions that come **after** the chain can be automation.
> Level decisions that come **before** it must be baked in.

Ducking is after → it can be automation. A level rider is before → it cannot.

---

## 2. Packaging: uv workspace monorepo, not a separate repo. Not yet.

The pipeline changed ten times in one day, and every change was driven by a
measurement that invalidated the previous behaviour. With a separate
versioned package, each of those is a release plus three consumer bumps, and
consumers drift in between. **That drift is the problem we are trying to
solve**: automixer is currently behind on the de-clicker fix, on de-bleeding,
on a compressor stage that never fired, and on the programme ceiling. A
separate repo would formalise the drift rather than remove it.

```
koodi/podcast/
  packages/speechmix/     chain, debleed, envelope analysis, programme
                          ceiling, duck/rider envelope computation,
                          and the measurement tests
  apps/autoraffkat/       FCPXML reader/writer, cutting decisions, web UI
  apps/automixer/         session reader, render
  apps/<new>/             its own session reader
```

Each app keeps: its reader, its writer, its UI, and any editing logic.
The library keeps: everything that turns samples into other samples, plus the
tests that prove it.

**The measurement tests are the real asset.** They encode findings that each
cost hours to discover, and they must run against the library in one place,
atomically, on every change. If you take nothing else from this document,
take the tests.

Split into a real PyPI package when the pipeline stops moving, or when a
fourth consumer appears outside these three. Splitting later is cheap;
un-splitting is not.

### One thing that must move with the chain

`FINGERPRINT_FIELDS` and `FINGERPRINT_VERSION`. They describe *what the chain
does*, so they belong to the chain. But *where the stamp file lives* is
per-app. Get this backwards and every app invents its own idea of "up to
date", which is the bug class this project has paid for repeatedly.

---

## 3. Findings that must travel with the code

Each of these was a silent failure: valid output, clean import, no error, and
wrong. None was caught by a crash. All were caught by measuring the output.

### 3.1 The de-clicker's threshold is a rate, not a multiplier

A de-clicker calibrated by "reference × N" corrected **2 % of all samples,
550–640 corrections per second**, altering the signal −10 dB relative to
itself. It passed every test, because the tests asked whether a planted click
was removed and never how many were found.

Calibrate on how often the artefact actually occurs — lip smacks are a few a
minute — and keep a ceiling that raises the threshold until the findings fit,
correcting nothing if they never do.

### 3.2 Bleed is linear: subtract it, do not gate it

The same voice in two microphones a few milliseconds apart is a comb filter.
That is what a summed pair sounds like when it sounds metallic.

Ducking cannot reach it. Measured: the masks fired correctly and closed the
microphone on 64 % of the frames where only the other person spoke, and
**infinite** attenuation still moved the ripple only 6.22 dB → 6.01, because
the gaps fall on turn-taking boundaries where the bleed is loudest — and
overlapping speech needs both microphones open anyway.

`debleed.py` estimates the leakage path as a least-squares FIR (2048 taps,
solved from the Toeplitz structure) over the passages where only the source
speaks, and subtracts it everywhere. Coherence 0.1069 → 0.0098; the target's
own speech preserved at r = 0.9993.

It must run on the **raw** audio, *before* any generative restoration
plug-in: such a plug-in does not preserve the linear relation between tracks,
and after it no filter can remove the bleed.

And it measures its own output: a filter that eats the target's own speech is
refused with a stated reason, because that mistake is only audible after the
export.

### 3.3 Compression comes in small amounts several times — and check every stage fires

Three bounded stages, each capped at 5 dB of gain reduction. The first is
multiband so a plosive cannot pull the sibilance down with it, with one ratio
and one limit across all bands (differing amounts per band move the tone with
the programme).

**One of the three never fired.** Its threshold was written
`leveler_threshold + 4.0` — four decibels *above* the second stage — and it
runs after the second, which has already pulled everything below its own
threshold. Measured on three minutes of real speech, that stage's gain moved
**0.00 dB at every target from −14 to −18 LUFS**. The chain promised three
stages and ran two.

Write a test that runs each stage in sequence and fails on any that leaves
the signal untouched. Note what the test fixture had to learn: thresholds are
absolute and applied *after* normalisation, so a signal whose every burst is
equally loud sits entirely below all of them and the test passes while
measuring nothing. The bursts must vary, because in speech it is the loud
passages that clear the threshold.

### 3.4 The peak attack must be longer than a pitch period

Two milliseconds modulates the waveform of a 110 Hz voice instead of its
level, which is harmonic distortion by definition. Measured on a sine at
110 Hz / −6 dBFS: 2 ms → THD −30.9 dB, 10 ms → −32.9 dB, 40 ms → −36.1 dB.
15 ms is longer than a pitch period for every speaking voice.

De-essing goes **before** the compressors, because a restoration plug-in adds
several dB above 3 kHz (measured +4…+5.7 dB, 3–20 kHz with dxRevive) and one
sibilant otherwise drives the gain of a whole sentence.

### 3.5 The ceiling is the programme's, not the stem's

This is the one most likely to be wrong in your project right now.

Each stem limited to −1.5 dBTP is not enough, because what plays is the
**sum**. Two stems whose peaks are both pressed to the ceiling exceed full
scale whenever those peaks coincide — in theory +4.5 dB, and measured on a
real episode **+4.51 dBFS, 49 971 samples over full scale in 4072 bursts,
200 a minute**, median 0.23 ms. That is audible as intermittent crackle on
loud syllables, and it is what a host application draws in red.

The fix is **not** harder per-stem limiting — then every stem pays six
decibels of crest for what some *other* file happens to do. Compute the
limiter's gain curve from the **summed** stems and multiply that identical
curve into each one. The sum then obeys the ceiling and the balance between
speakers cannot move, because every stem gets the same number. Measured:
+4.51 → −1.51 dBFS at a cost of 0.50 LU.

The pass is idempotent by construction — the curve is `min(1, ceiling/peak)`,
so a sum already at the ceiling gets 1 everywhere — which makes it safe to
run on every processing round.

Summing files sample-by-sample is only correct when the stems line up on the
timeline. Make that a checked fact, not an assumption, and leave mismatched
stems alone rather than summing them at the wrong offset.

Related: the ceiling must be a look-ahead limiter, never a static
attenuation. A static cut scales the whole file by what its single loudest
sample demands; measured, that turned −14.00 LUFS into −25.74, and it makes
the balance between speakers depend on whose loudest transient was loudest,
which is to say random.

Also: `pedalboard.Limiter` applies makeup gain — it lifted −20 LUFS to −15.8
and peaks to zero. Use a static attenuation that never raises, or your own
look-ahead limiter.

### 3.6 The loudness target is the programme's, not one stem's

Two microphones each normalised to −14 LUFS sum above it — measured −12.2,
because the speakers overlap and the microphones hear each other. Measure the
sum of the raw microphones over a bounded window and take the difference off
every file, and put the trim into the **target**, never into the gain: the
chain normalises to the target as its last act, so a trim added to the gain
is removed again exactly (measured, stems landed on −14.1 instead of −15.8
and the reading looked correct).

Applying −14 to a mono speech stem directly leaves about 14 dB of crest and
sounds crushed; the same figure as a programme target leaves 17.5.

**A microphone is always mono out, even from a stereo source.** Two channels
break the arithmetic in three places silently: de-bleeding reads only the
first channel, the programme ceiling sums stems of differing channel counts
by broadcasting them, and panning is a mono-source idea.

### 3.7 The level rider goes first, and it cannot work from the signal alone

A slow level ride before the compressors is the stage every hand-made mix
starts with. It removes the speaker's *own* variation so the compressor only
catches what is left, instead of doing the rider's job badly — fast and
level-dependent instead of slow and even.

Two things went the wrong way before it worked:

**Deciding "speech" from the level is worse than not riding at all.** On a
two-microphone recording, half of what is loud on a track is the other
person. Measured: the level heuristic called 74 % of one track's blocks
speech when 53 % were its owner's, agreeing only 38 % of the time. The rider
dutifully lifted the leakage — noise floor **up 3.5 dB**, level spread
*worse* at 2.88 → 3.37 dB. Take the mask from the speech grid (which is
measured on raw audio), and with no mask, return the audio untouched rather
than guessing.

**The gain must return to unity outside its own speaker's speech, not hold.**
Holding is what a one-microphone rider does and it is right there; here the
pause *is the other person talking*, so a held boost lands straight on their
leakage. Measured, separation between own speech and leakage fell
19.1 → 14.8 dB. Returning to zero keeps it at 18.7.

What it is worth, measured on ten minutes of real speech: own-speech level
spread 6.72 → 6.44 dB and 6.46 → 5.67 dB, separation and noise floor
unchanged. Modest, because real speech variation is mostly sentence-scale
emphasis, which the rider deliberately leaves alone.

**Honest note on a tempting premise:** the compressor does *not* cost
separation either (19.1 → 19.0 dB). So a rider is not the answer to leakage.
De-bleeding is.

### 3.8 Ducking, and what it does and does not do

Independent per-microphone normalisation lifts bleed: two microphones
normalised to the same LUFS get different gains — measured +25.6 dB and
+22.5 dB — and the 3.1 dB difference lands on the quieter microphone's bleed
of the louder speaker.

Ducking must never fail quietly. It depends on the analysis, and pressing the
button before the analysis finished left the masks empty with nothing said:
the setting read −9 dB and the output had none. "The setting is on and no
microphone matched a mask" is an **error**, not a silence.

Measure ducking on the **raw** files. A compressor raises the noise floor
between words and flattens the difference between microphones, which are
exactly the two things the ducking decision depends on. Measure it on
processed audio and the masks fire in the wrong places — and it still looks
fine until someone listens.

Worth knowing what ducking does to demarcation, measured on real material
(gap between own speech and own non-speech):

| track | raw | after the chain | + ducking |
|---|---|---|---|
| clean-ish mic | 17.8 dB | 24.2 dB | 25.5 dB |
| leaky mic | 13.4 dB | 13.3 dB | 15.0 dB |

Note the chain does **not** erode the gap: the compressors are downward-only
with no makeup gain, and normalisation is broadband so ratios survive. The
restoration plug-in actually improves it. What limits the leaky track is
bleed, not compression — its non-speech sits 13 dB down because it contains
the other person's voice, not noise.

### 3.9 The plug-in slot is flavour, not a replacement mechanism

One slot, it runs first, and it never stands in for a stage of the chain. The
reason it exists at all is that a speech-restoration model is the one thing
we have no opinion about and cannot ship; everything after it was measured,
and those numbers are the tool. Letting a second plug-in in would quietly
undo them — someone loads a limiter in front of ours and the ceiling
guarantee stops being true with nothing to say so.

Practical constraints, both measured:

- `plugin.process(..., reset=False)` **shortens** the result by the plug-in's
  latency (4641 samples with dxRevive). Always `reset=True`, and never feed
  one instance a file in chunks.
- pedalboard loads a VST3 on the **main thread only**; it processes from any
  thread. The error text talks about processing and hides that the constraint
  is on loading, so a lazy per-thread load looks reasonable and fails every
  time. Build every instance up front.
- Host the plug-in in a child process. It is 97 % of the run and uses **one**
  core (measured 0.98 cores, 7.25× realtime), so the only way to reach the
  other cores is several instances at once. Measured on a 20-minute file:
  168.4 s → 68.3 s with the file cut into pieces, each its own full
  `reset=True` run with a five-second margin processed and thrown away. It is
  not free — the pieces do not see each other's context — so the difference
  from the whole-file result is 25.7 dB below the signal in speech and
  −84 dBFS in the quiet parts, and the piece count belongs in the
  fingerprint.
- Not everything that changes the result is an automatable parameter.
  dxRevive publishes four, and the **model selector is not one of them** — it
  lives in the plug-in's own state, reachable only through its own interface.
  Save the opaque state blob with the project and put it in the fingerprint.

---

## 4. Hard rules that are cheap to violate

- **Never write over the original.** Analysis is always done on the raw file.
  A compressor raises the noise floor between words and flattens the
  difference between microphones — exactly the two things sensitivity and the
  overlap rule depend on. Cache keyed on modification time makes overwriting
  doubly destructive: the curve is recomputed on already-processed audio.
- **The sample count must not change.** The export references the processed
  file with the same times as the original. Check it in more than one place
  and discard anything that deviates. Measure shift separately by
  cross-correlation, because length alone cannot detect a plug-in that
  reports its latency wrongly — and keep that correlation an FFT.
  `np.correlate(..., "full")` is O(n²) and took 132 s on a 20-minute file,
  longer than the plug-in itself.
- **"Up to date" is a fingerprint, not a modification time.** A processed
  file newer than its source proves nothing: the plug-in, its controls, the
  target level and the ducking depth never touch the source. Comparing times
  alone made the button skip every file and return before the first log line
  — indistinguishable from a broken button. Write the field list out by hand
  so a new setting cannot slip in or out unnoticed, and fail a test if it
  does. An unknown stamp counts as stale.
- **Progress is weighted by file size, and the stage is the resolution.** The
  plug-in processes a file in one piece and cannot be asked how far along it
  is. Log each file and stage: when it is slow or fails, the question is
  always which file and which stage.

---

## 5. The recurring failure, stated plainly

Nearly every bug in this list was **valid, accepted, and silently wrong**:
correct-looking output, a clean import, no exception, and a result nobody
notices until they listen — by which time the edit has been done by hand and
cannot be rebuilt.

So the working rule for this pipeline is: **a feature that produced nothing
must say so.** Setting on and result empty is an error, not a silence. And
every claim about what a stage does should have a number next to it, taken
from real material, in the comment where the constant lives.

---

*Source: autoraffkat, `CLAUDE.md` and `CHANGELOG.md` carry the same findings
with full context. Ask for the relevant section if a number here needs its
surrounding story.*
