# Ableton MCP Toolkit

Remote-control [Ableton Live](https://www.ableton.com/) from an LLM (or any MCP client),
plus a set of offline audio-analysis tools for reference-matched mixing.

Built on top of [AbletonMCP](https://github.com/ahujasid/ableton-mcp) by Siddharth Ahuja:
the Remote Script is the original, substantially extended with a generic Live-Object-Model
layer. Two additional pieces are added — a second MCP server and a suite of analysis scripts.

> Note: code comments are in German (the author's working language); this README is in English.

## Components

| File | What it is |
|---|---|
| `remote_script/__init__.py` | Ableton Remote Script (a `ControlSurface`) that opens a TCP server on **port 9877**. Extends AbletonMCP with a **generic LOM layer** (`get_property` / `set_property` / `call_method` / `list_children` / `describe_object`) plus commands for arrangement clips, clip envelopes, device parameters, note reading, deletes and track mute. |
| `automation_server.py` | A small [FastMCP](https://github.com/jlowin/fastmcp) server exposing the generic LOM layer + envelope automation, device parameters, clip-note reading, clip deletes and track mute. Connects as a TCP **client** to the same port 9877. |
| `analysis/*.py` | Offline audio / `.als` analysis — **no running Live required**. Pure Python. |
| `capture.py` | Deterministic capture wizard: connects to port 9877 directly (no MCP/LLM), adaptively discovers the capture track by its routing (not by index), verifies safety preconditions, records, and trims silence. See [Capture wizard](#capture-wizard) below. |

## Architecture

Two MCP servers drive the same running Ableton through **one** Remote Script:

```
AbletonMCP (uvx ableton-mcp) ──────────┐
                                        ├── TCP localhost:9877 ──> remote_script/__init__.py
AbletonAutomation (automation_server) ──┘                          (runs inside Live as a ControlSurface)
```

Read commands (`get_*`) run directly; anything that mutates Live state is marshalled onto
Live's main thread (the Live API is not thread-safe).

## Setup

The Remote Script and automation server must live where Live and your MCP config expect them.
Symlink them from this repo into your User Library:

```bash
ln -sf "$PWD/remote_script/__init__.py" "$HOME/Music/Ableton/User Library/Remote Scripts/AbletonMCP/__init__.py"
ln -sf "$PWD/automation_server.py"      "$HOME/Music/Ableton/User Library/AbletonMCP_Server/automation_server.py"
```

Enable **AbletonMCP** as a Control Surface in Live (Preferences → Link/Tempo/MIDI), then register
the MCP servers with your client. For Claude Code:

```bash
claude mcp add AbletonMCP -- uvx ableton-mcp
claude mcp add AbletonAutomation -- uv run --with mcp python "$HOME/Music/Ableton/User Library/AbletonMCP_Server/automation_server.py"
```

After **any** change to `remote_script/__init__.py`, fully restart Live (it caches Python modules).

## Capture wizard

`capture.py` records the current arrangement into a WAV without any LLM/MCP round-trip — a plain
TCP client against port 9877. It's built in two phases so it can cope with arbitrary project
layouts instead of hardcoding track indices:

1. **Discover** (adaptive, read-only): scans every track, finds the capture track by its
   **routing** (input = Resampling/bus, output = Sends Only, or name containing "CAPTURE") rather
   than an assumed index, reads song length, and flags anything unsafe — soloed tracks, an
   unmuted reference track, or a capture track whose output isn't `Sends Only` (the routing state
   that makes audio-doubling structurally impossible, regardless of arm/monitor state).
2. **Run** (deterministic): once confirmed, applies the safety fixes, arms, starts playback, jumps
   to bar 1 and fires the clip slot in the same call (avoids drift from round-trip latency),
   verifies the signal isn't silent (RMS, not file size — silence and full signal are the same
   file size on uncompressed formats), waits out the song length, stops, and trims leading/trailing
   silence.

```bash
uv run --with soundfile,numpy python capture.py --discover        # inspect only, no recording
uv run --with soundfile,numpy python capture.py --go --out cap.wav
```

## Analysis tools

Run with `uv run --with <deps> python analysis/<tool>.py …`; each script's header documents its exact invocation.

| Tool | What |
|---|---|
| `analyze_audio.py` | Sound signature of a file: LUFS, sample/true peak, crest, 7-band balance, centroid, stereo correlation, resonances |
| `analyze_sections.py` | Part-by-part spectral balance of a whole song vs a reference |
| `mix_diagnose.py` | Orchestrates the analyzers into a prioritized, staged mix punch-list (gain-staging → element balance → mono → tonal → resonance); optional `--target-lufs` master readout |
| `analyze_stems.py` | Per-stem analysis of a Demucs output + stem balance |
| `analyze_stft.py` | Time-resolved band stability (std over time) + spectral flux |
| `arrangement_profile.py` | An ASCII "story" map of a track from its stems |
| `eq_match.py` | Reference EQ-matcher: spectrum delta → corrective EQ curve |
| `als_extract.py` | Read an `.als` (gzipped XML) structure without opening Live |

## Known Ableton API limits

- **Arrangement-track automation is not scriptable** — only session-clip envelopes. Workaround: write the envelope into a session clip, then duplicate it to the arrangement.
- Live's **"Humanize"** transform is UI-only — humanize by reading notes, jittering them, writing back.
- **Device reordering** is not exposed by the API.
- After deleting an arrangement clip, **indices shift** — re-query after each delete.

## Credits & license

The Remote Script is based on **[AbletonMCP](https://github.com/ahujasid/ableton-mcp)** by
Siddharth Ahuja (MIT). This toolkit — the LOM extensions, the automation server and the analysis
tools — is MIT licensed. See [LICENSE](LICENSE).
