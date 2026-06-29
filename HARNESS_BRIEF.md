# Project brief: a GUI harness for the Ableton MCP Toolkit

Hey — thanks for taking a look. This doc is meant to get you (and your coding agent) oriented
fast: what exists, why it's built this way, and what we're trying to build next.

## TL;DR

This repo lets a program talk to a running Ableton Live session (read/write tracks, clips,
devices, automation) over a local TCP socket, plus a set of **offline, deterministic** audio
analysis scripts that do reference-matched mix diagnosis. Everything here was built and used
through an LLM coding agent driving it conversationally. The ask: **wrap the deterministic parts
in a real GUI app** so a producer can use it without an agent or a terminal.

## What exists today

**1. The bridge** — `remote_script/__init__.py`
An Ableton Remote Script (a `ControlSurface`) that opens a TCP server on `localhost:9877` inside
a running Live instance. It's based on the open-source
[AbletonMCP](https://github.com/ahujasid/ableton-mcp) (MIT), extended with a **generic Live
Object Model layer**:
- `get_property` / `set_property` / `call_method` / `list_children` / `describe_object`
- These four primitives expose basically the *entire* Live API by path
  (e.g. `song.tracks[3].devices[0].parameters[35].value`) instead of hand-wrapping every command.
- Plus some purpose-built commands: arrangement clip read/write, clip envelopes (automation),
  device parameter listing, note reading, track mute, deletes.
- Read commands run directly; anything that mutates state is marshalled onto Live's main thread
  (the Live API is not thread-safe).

**2. The MCP servers** — `automation_server.py`
A small [FastMCP](https://github.com/jlowin/fastmcp) server that connects to the same port 9877
as a TCP *client* and exposes the same primitives as MCP tools, so an LLM client (e.g. Claude
Code) can call them. There's a second, separate MCP server (`uvx ableton-mcp`, the upstream
project) that talks to the same socket for clip/track/browser/playback commands.

**3. The analysis engine** — `analysis/*.py`
This is the important part for the GUI: **these scripts have zero LLM dependency.** Pure Python
(numpy/scipy/librosa/pyloudnorm), run as `uv run --with <deps> python analysis/<tool>.py …`.

| Script | What it does |
|---|---|
| `analyze_audio.py` | Signature of one file: LUFS, true peak, crest, 7-band spectral balance, centroid, stereo correlation, resonances |
| `analyze_sections.py` | Same, but **part-by-part** across a whole song vs. a reference track |
| `mix_diagnose.py` | **The orchestrator.** Runs the above in textbook mixing order (gain-staging → element balance → mono → tonal balance → resonance) and emits a prioritized punch-list. Optional `--target-lufs` flag for mastering-stage readout (loudness gap, density/crest gap, true-peak headroom) |
| `analyze_stems.py` | Per-stem analysis of a Demucs separation + balance between stems |
| `analyze_stft.py` | Time-resolved band stability + spectral flux (catches things averages hide) |
| `eq_match.py` | Reference EQ matcher — spectral delta → corrective EQ curve |
| `als_extract.py` | Reads an `.als` (gzipped XML) project structure without opening Live |

All of this was designed against actual mixing literature (Izhaki, Senior, Snoman) — the staging
order in `mix_diagnose.py` isn't arbitrary, it encodes "you can't EQ your way out of a bad
balance, and a brickwalled master lies about your spectrum."

## The vision: a GUI harness

Right now, using this requires an LLM agent in the loop translating intent into tool calls. The
goal is a **standalone desktop app** that wraps the same deterministic pipeline so a producer can
just click through it:

```
Desktop app
  │
  ├─ talks directly to port 9877 (the remote script's raw socket protocol — no MCP/LLM needed
  │    for this part at all)
  │     - capture wizard: pre-flight routing/safety checks → arm → play → fire clip → wait →
  │       stop → trim silence
  │     - reads track/device state to drive the UI
  │
  ├─ runs analysis/*.py as subprocesses (or a future native port — they're small and mostly
  │    numpy/scipy, portable)
  │     - mix_diagnose, analyze_sections, eq_match
  │
  └─ optional: an LLM chat panel (bring-your-own API key) for "explain this finding" /
       "what should I try" — kept OPTIONAL so the core tool has no inference cost and no
       LLM dependency at all
```

**Why this split matters:** the deterministic core (capture → analyze → diagnose) is a completely
normal app-engineering problem — no agent needed. The LLM layer becomes a nice-to-have add-on,
not the product's backbone. That's a much easier thing to ship and support.

### The user-facing flow we want

1. **Setup** — install/symlink the remote script into Live's User Library, verify the socket
   connection.
2. **Reference library** — user imports *their own* reference tracks (we don't ship or bundle
   any copyrighted audio — that's a hard constraint, see below).
3. **Capture wizard** — automates the pre-recording safety checks (this is the part with the most
   "domain trap" potential, see Gotchas below), records, trims silence, verifies signal isn't
   silent (RMS check, not file-size check).
4. **Diagnosis view** — `mix_diagnose`'s staged punch-list as actual UI: charts per frequency
   band, part-by-part visualization instead of an ASCII table, color-coded severity.
5. **Iteration history** — capture → diagnose → tweak → recapture, with deltas tracked across
   rounds, so a user can see "did that last EQ move actually help."

## Repo / protocol details you'll need

- The remote script's socket protocol is JSON-over-TCP on `localhost:9877`. Commands have a
  `command_type` field; see `_process_command` in `remote_script/__init__.py` for the dispatch
  table.
- Object references in command args use a `{"__path__": "song.tracks[0]..."}` convention,
  resolved by `_coerce_arg` / `_resolve_path`.
- **Type coercion gotcha (already fixed once, worth knowing about):** values arriving as strings
  get coerced based on the *current* type of the target property — so a `bool` target like
  `track.arm` gets `'true'/'false'` parsed as bool, but a `str` property (e.g. `.name`) is left
  alone even if it looks numeric (e.g. a track literally named `"123"`). See `_set_property`.
  If you build a new client on top of this protocol, don't re-introduce a naive
  "stringify everything" bridge — type it properly on the way in.

## Known Ableton API limits (apply to any GUI built on this)

- Arrangement-track automation isn't scriptable — only session-clip envelopes (workaround:
  write into a session clip, then duplicate to the arrangement).
- Live's "Humanize" is UI-only — must be reimplemented by reading notes, jittering, writing back.
- **Device reordering isn't exposed by the API** — if your UI wants a specific effect-chain
  order (e.g. "limiter must be last"), you can't enforce it programmatically; you can only detect
  and warn.
- Deleting an arrangement clip shifts indices — always re-query after a delete, never cache
  positions across a mutation.

## Capture safety — the trap worth designing around up front

The single most painful recurring bug in real usage was **signal routing causing doubled audio**
(a capture/monitor track accidentally feeding back into the master bus). The fix that actually
stuck: capture tracks must have **output = "Sends Only" + monitoring = Off** — that makes
feedback structurally impossible regardless of arm/monitor/clip state, instead of relying on
"remember to mute it" conventions that regress. If the GUI exposes any kind of recording/capture
feature, **build this as an enforced precondition, not a checklist item** — verify routing state
before allowing a capture to start, auto-correct or hard-block otherwise.

## Constraints / non-negotiables

- **No copyrighted audio ships with this.** Reference tracks, songs, `.als` project files —
  all stay user-supplied, never bundled, never in version control. This repo's history has never
  contained any binary/audio asset; keep it that way in anything downstream too.
- MIT license, attribution to the upstream AbletonMCP project (Siddharth Ahuja) preserved —
  see `LICENSE`.

## Suggested phased plan

1. **Phase 1 (smallest useful slice):** capture wizard + diagnosis view, single platform
   (macOS first, since that's the dev environment), no LLM panel at all. Tauri is a reasonable
   default — small binary, a Rust (or even just Python) backend owns the socket connection, web
   frontend for the charts.
2. **Phase 2:** iteration history / versioning across capture rounds.
3. **Phase 3 (optional):** BYO-API-key chat panel for natural-language Q&A on the diagnosis.
4. **Later:** cross-platform (Windows path/symlink handling differs), packaging/notarization.

## Open questions to resolve early

- GUI stack: Tauri vs. plain Electron vs. a lighter native option — depends on your and the
  team's comfort zone more than anything technical.
- How much of `analysis/*.py` stays as Python subprocesses vs. gets ported — Python is fine to
  ship via `uv`/PyInstaller-style bundling short-term; revisit if packaging size/startup time
  becomes annoying.
- Where reference-track "profiles" get cached (so a user doesn't re-analyze the same reference
  every session).

Happy to jump on a call or pair on this — just ping back. Looking forward to seeing what you and
your agent come up with.
