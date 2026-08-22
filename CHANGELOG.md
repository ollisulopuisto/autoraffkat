# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to Calendar Versioning (CalVer).

## [v26.08.22.48] - 2026-08-22

### Added
- **Cross-Platform VST3 Paths**: Added native Linux (`/usr/lib/vst3`, `~/.vst3`, etc.) and Windows (`CommonProgramFiles/VST3`) plugin directory discovery to `audio/chain.py` for headless remote servers.
- **1/f Dynamic Tempo Modulation**: Local conversation density / turn-taking rate dynamically modulates pacing and dwell times over rolling 45s windows in `decide.py`.
- **Optional Reaction Shots**: Added `reaction` rule to long-take breaking options (`LONGTAKE_REACTION`), allowing cuts to a silent co-host's close-up during monologues while keeping Wide as the safe default.
- **Rhythm & Pacing Engine**: Added macro editing presets (`broadcast`, `mellow`, `hectic`, `custom`) for rhythm control.
- **L-Cut & J-Cut Support**: Added asymmetric lead (J-cut anticipation) and hang (L-cut reaction) controls to `decide.py` and UI.
- **Pause-Snapped Monologue Punctuation**: Long continuous monologues now snap transitions at natural speech pauses or acoustic breath/energy dips.
- **Rhythm UI Controls**: Added profile selection radio group and hang slider in both Finnish and English.
