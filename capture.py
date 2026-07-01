#!/usr/bin/env python3
# Capture Wizard (CW) — deterministischer Master-Capture aus einem laufenden Ableton
# ueber das Remote Script (TCP localhost:9877). Reiner Socket-Client, KEIN LLM, KEIN MCP.
#
# Drei Schichten (gegen die Projekt-Variabilitaet):
#   1) discover()  ADAPTIV, ohne Annahmen: findet die Capture-Spur am ROUTING (nicht am Index),
#                  liest Songlaenge, scannt ALLE Spuren auf Solo/Mute + REFERENCES. -> Plan + Issues.
#   2) Confirm     Der Aufrufer (CLI/GUI) zeigt den Plan; der User bestaetigt/korrigiert EINMAL.
#   3) run()       KNALLHART DETERMINISTISCH: Safety-Fixes (Sends-Only/Monitor-Off/Solos/REFERENCES)
#                  -> arm -> start+jump0+fire (im Skript ms-schnell -> ab ~Beat 0) -> RMS-Signalcheck
#                  -> Auto-Stop am Songende -> Trim. Jedes Mal identisch.
#
# discover() gibt einen JSON-faehigen Plan zurueck = der Contract, gegen den die GUI baut.
#
# CLI:
#   uv run --with soundfile,numpy python capture.py --discover
#   uv run --with soundfile,numpy python capture.py --go --out ~/AbletonDB/references/cap.wav [--track N]

import sys, os, json, socket, time

HOST, PORT = "localhost", 9877
_sock = None

def _connect():
    global _sock
    if _sock is None:
        _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _sock.connect((HOST, PORT))
    return _sock

def send(cmd, params=None):
    global _sock
    try:
        s = _connect()
        s.sendall(json.dumps({"type": cmd, "params": params or {}}).encode())
        s.settimeout(15.0)
        chunks = []
        while True:
            ch = s.recv(8192)
            if not ch:
                break
            chunks.append(ch)
            try:
                data = b"".join(chunks)
                json.loads(data.decode())
                resp = json.loads(data.decode())
                if resp.get("status") == "error":
                    raise RuntimeError(resp.get("message", "Ableton error"))
                return resp.get("result", {})
            except json.JSONDecodeError:
                continue
    except Exception:
        try: _sock.close()
        except Exception: pass
        _sock = None
        raise

# --- LOM-Helfer (rohes Property/Methoden-Protokoll) ---
def gp(path):
    r = send("get_property", {"path": path})
    return r.get("value") if isinstance(r, dict) else r
def gp_safe(path, default=None):
    # Robust gegen Spur-Typen, die eine Property nicht haben (MIDI/Gruppen/Return)
    try: return gp(path)
    except Exception: return default
def sp(path, value):
    return send("set_property", {"path": path, "value": value})
def call(path, method, args=None):
    return send("call_method", {"path": path, "method": method, "args": args or []})

# ---------------------------------------------------------------- Schicht 1: DISCOVER
def discover():
    info = send("get_session_info")
    n = int(info["track_count"]); tempo = float(info["tempo"])
    beats = float(gp("song.last_event_time"))
    spb = 60.0 / tempo
    plan = {"tempo": tempo, "song_length_beats": beats, "song_length_s": round(beats * spb, 1),
            "track_count": n, "capture_candidates": [], "soloed_tracks": [],
            "references_unmuted": [], "issues": [], "chosen": None, "empty_slot": None}

    for i in range(n):
        name = gp_safe(f"song.tracks[{i}].name", "") or ""
        inp = gp_safe(f"song.tracks[{i}].current_input_routing")
        out = gp_safe(f"song.tracks[{i}].current_output_routing")
        solo = gp_safe(f"song.tracks[{i}].solo", False)
        up = name.upper()
        if solo:
            plan["soloed_tracks"].append({"i": i, "name": name})
        if "REFERENCE" in up and gp_safe(f"song.tracks[{i}].mute", False) is False:
            plan["references_unmuted"].append({"i": i, "name": name})
        # Capture-Kandidat per ROUTING/Name erkennen (keine Index-Annahme)
        if inp == "Resampling" or "CAPTURE" in up or out == "Sends Only":
            plan["capture_candidates"].append({
                "i": i, "name": name, "input": inp, "output": out,
                "monitoring": gp_safe(f"song.tracks[{i}].current_monitoring_state"),
                "mute": gp_safe(f"song.tracks[{i}].mute", False),
                "has_audio_input": gp_safe(f"song.tracks[{i}].has_audio_input", False)})

    # Auswahl: nur Audio-Input-Kandidaten; bevorzugt "MASTER" im Namen, dann Resampling
    cands = [c for c in plan["capture_candidates"] if c.get("has_audio_input")]
    def score(c):
        return (("MASTER" in c["name"].upper()), c["input"] == "Resampling")
    cands.sort(key=score, reverse=True)
    if len(cands) == 1:
        plan["chosen"] = cands[0]
    elif len(cands) > 1:
        plan["chosen"] = cands[0]
        plan["issues"].append({"level": "info",
            "msg": f"{len(cands)} Capture-Kandidaten — gewaehlt: '{cands[0]['name']}' (Spur {cands[0]['i']}). Mit --track N ueberschreiben."})
    else:
        plan["issues"].append({"level": "block",
            "msg": "Keine Capture-Spur gefunden (Input=Resampling/Bus, Output=Sends-Only oder Name 'CAPTURE'). Erst anlegen."})

    ch = plan["chosen"]
    if ch:
        # leeren Slot finden (nur ein explizites False zaehlt; None/Fehler ueberspringen)
        for s in range(14):
            if gp_safe(f"song.tracks[{ch['i']}].clip_slots[{s}].has_clip") is False:
                plan["empty_slot"] = s; break
        if plan["empty_slot"] is None:
            plan["issues"].append({"level": "block", "msg": "Kein leerer Clip-Slot auf der Capture-Spur."})
        # Safety-Befunde -> Auto-Fixes
        if ch["output"] != "Sends Only":
            plan["issues"].append({"level": "fix", "msg": f"Capture-Output '{ch['output']}' -> wird auf 'Sends Only' gesetzt (Anti-Doppel)."})
        if ch["monitoring"] != 2:
            plan["issues"].append({"level": "fix", "msg": "Monitoring -> Off."})
        if ch["mute"]:
            plan["issues"].append({"level": "fix", "msg": "Capture-Spur ist gemutet -> wird entmutet (sonst stumme Aufnahme)."})
    if plan["soloed_tracks"]:
        names = ", ".join(t["name"] for t in plan["soloed_tracks"])
        plan["issues"].append({"level": "fix", "msg": f"Haengende Solos ({names}) -> werden geloest (Gremlin-Falle)."})
    if plan["references_unmuted"]:
        names = ", ".join(t["name"] for t in plan["references_unmuted"])
        plan["issues"].append({"level": "fix", "msg": f"REFERENCES unmuted ({names}) -> wird gemutet (kein Bleed)."})

    plan["ready"] = plan["chosen"] is not None and plan["empty_slot"] is not None \
        and not any(x["level"] == "block" for x in plan["issues"])
    return plan

def print_plan(plan):
    print(f"\nCAPTURE-WIZARD — Discovery")
    print("=" * 60)
    print(f"  Tempo {plan['tempo']:.0f} BPM | Song {plan['song_length_beats']:.0f} Beats "
          f"(~{plan['song_length_s']:.0f}s = {plan['song_length_s']/60:.1f} min)")
    ch = plan["chosen"]
    if ch:
        print(f"  Capture-Spur: '{ch['name']}' (Spur {ch['i']}) | Input {ch['input']} | "
              f"Output {ch['output']} | Slot {plan['empty_slot']}")
    print("  Befunde:")
    icon = {"block": "⛔", "fix": "🔧", "info": "ℹ️ "}
    if not plan["issues"]:
        print("    ✓ alles sauber, nichts zu fixen")
    for x in plan["issues"]:
        print(f"    {icon.get(x['level'],'')} {x['msg']}")
    print("=" * 60)
    print(f"  -> {'BEREIT (--go startet)' if plan['ready'] else 'NICHT bereit (Block oben loesen)'}\n")

# ---------------------------------------------------------------- Schicht 3: RUN
def _trim(src, dst):
    import numpy as np, soundfile as sf
    y, sr = sf.read(src, always_2d=True); mono = y.mean(1)
    nz = np.where(np.abs(mono) > 10 ** (-50 / 20))[0]
    a, b = (nz[0], nz[-1] + 1) if len(nz) else (0, len(mono)); yt = y[a:b]
    sf.write(dst, yt, sr)
    pk = 20 * np.log10(np.max(np.abs(yt)) + 1e-12)
    rms = 20 * np.log10(np.sqrt(np.mean((yt.mean(1)) ** 2)) + 1e-12)
    return len(yt) / sr, round(float(pk), 1), round(float(rms), 1)

def run(plan, out_path):
    ch = plan["chosen"]; i = ch["i"]; slot = plan["empty_slot"]
    T = f"song.tracks[{i}]"
    # --- Safety-Fixes (deterministisch) ---
    if ch["output"] != "Sends Only": sp(f"{T}.current_output_routing", "Sends Only")
    if ch["monitoring"] != 2:        sp(f"{T}.current_monitoring_state", 2)
    if ch["mute"]:                   sp(f"{T}.mute", False)
    for t in plan["soloed_tracks"]:        sp(f"song.tracks[{t['i']}].solo", False)
    for t in plan["references_unmuted"]:   sp(f"song.tracks[{t['i']}].mute", True)
    # Verifizieren, dass der Pfad jetzt frei ist
    assert gp(f"{T}.current_output_routing") == "Sends Only", "Output-Fix fehlgeschlagen"
    assert gp(f"{T}.muted_via_solo") is False, "Pfad noch solo-gemutet"
    # --- Aufnahme: arm -> start -> jump0 -> fire (im Skript ms-schnell -> ab ~Beat 0) ---
    sp(f"{T}.arm", True)
    send("start_playback")
    sp("song.current_song_time", 0)
    call(f"{T}.clip_slots[{slot}]", "fire")
    time.sleep(0.4)
    rec = gp(f"{T}.clip_slots[{slot}].is_recording")
    meter = gp(f"{T}.output_meter_level")
    if not rec or (meter or 0) < 0.001:
        send("stop_playback"); sp(f"{T}.arm", False)
        raise RuntimeError(f"STUMM/keine Aufnahme (is_recording={rec}, meter={meter}) — Abbruch.")
    dur = plan["song_length_s"] + 8.0
    print(f"  ● Aufnahme laeuft (~{dur/60:.1f} min, Meter {meter:.2f}) …")
    time.sleep(dur)
    send("stop_playback")
    sp(f"{T}.arm", False)
    sp(f"{T}.back_to_arranger", True)
    src = gp(f"{T}.clip_slots[{slot}].clip.file_path")
    tdur, pk, rms = _trim(src, os.path.expanduser(out_path))
    ok = rms > -45
    print(f"  ✓ {os.path.basename(out_path)} | {tdur:.0f}s | peak {pk:+.1f} | RMS {rms:+.1f} dBFS | "
          f"{'OK' if ok else '!!! STUMM !!!'}")
    return {"wav": os.path.expanduser(out_path), "duration_s": round(tdur, 1),
            "peak_dbfs": pk, "rms_dbfs": rms, "silent": not ok, "source_aif": src}

def main():
    a = sys.argv
    track_override = int(a[a.index("--track") + 1]) if "--track" in a else None
    out = a[a.index("--out") + 1] if "--out" in a else None
    plan = discover()
    if track_override is not None:
        m = [c for c in plan["capture_candidates"] if c["i"] == track_override]
        plan["chosen"] = m[0] if m else plan["chosen"]
    if "--json" in a:
        print(json.dumps(plan, ensure_ascii=False, indent=2)); return
    print_plan(plan)
    if "--go" in a:
        if not plan["ready"]:
            print("Abbruch: nicht bereit."); return
        if not out:
            print("Brauche --out <wav>."); return
        run(plan, out)

if __name__ == "__main__":
    main()
