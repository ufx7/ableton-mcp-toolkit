# Mix-Diagnose — fuehrt die Mixdown-Checks in LEHRBUCH-REIHENFOLGE aus und gibt einen
# priorisierten "Was-zuerst-fixen"-Befund (Izhakis "What's wrong with it?").
# Orchestriert die vorhandenen Analyzer (analyze_audio.analyze) — erfindet nichts neu.
#
# Reihenfolge nach Izhaki (Mixing Audio) / Senior (Mixing Secrets) / Snoman (Dance Music
# Manual), siehe LEITFADEN + README. Die fruehen Stages GATEN die spaeteren:
#   1 GAIN   Gain-Staging / Headroom / Brickwall   -> blockt die Spektral-Aussagen, wenn ueberlimitiert
#   2 BALANCE Element-Balance vs Referenz-Stems     -> Fader zuerst (Senior "Raw Balance")
#   3 MONO   Mono / Phase (v.a. Bass mono)          -> Snoman / Referenz-Signatur
#   4 TON    Tonbalance vs Referenz (Summe)         -> Part-Detail: analyze_sections.py
#   5 RESO   Resonanz-DIAGNOSE (nur melden)         -> Notch-Falle: nur ueber Ref-Niveau
#
# Aufruf:
#   uv run --with librosa,pyloudnorm,soundfile,numpy,scipy python mix_diagnose.py \
#       <unser_mix.wav> --ref <referenz_summe.json> [--ref-stems <referenz_stems.json>] \
#       [--stems <unser_demucs_ordner>] [--json out.json]
# Referenz-JSONs werden mit analyze_audio.py / analyze_stems.py erzeugt.

import sys, os, json
import numpy as np
from analyze_audio import analyze, BANDS

def arg(flag, default=None):
    return sys.argv[sys.argv.index(flag)+1] if flag in sys.argv else default

# Befund-Saetze: (order, severity, text, action). severity: 0=BLOCK 1=WARN 2=INFO/OK
ICON = {0: "⛔", 1: "⚠️ ", 2: "ℹ️ "}
STAGE = {1: "GAIN", 2: "BALANCE", 3: "MONO", 4: "TON", 5: "RESO"}

def stem_levels(folder):
    """RMS je Stem (drums/bass/other/vocals), wie analyze_stems — relativ zu drums."""
    out = {}
    for f in sorted(os.listdir(folder)):
        if f.endswith(".wav"):
            out[f[:-4]] = analyze(os.path.join(folder, f))["loudness"]["rms_dbfs"]
    if "drums" not in out:  # ohne Drums-Anker keine vergleichbare Basis
        return None
    return {k: round(v - out["drums"], 1) for k, v in out.items()}  # drums = 0 dB

def main():
    path = sys.argv[1]
    ref = json.load(open(arg("--ref"))) if arg("--ref") else {}
    ref_stems = json.load(open(arg("--ref-stems"))) if arg("--ref-stems") else None
    stems_dir = arg("--stems")
    me = analyze(path)
    F = []  # Befunde

    lo = me["loudness"]; rlo = ref.get("loudness", {})
    crest = lo["crest_factor_db"]; ref_crest = rlo.get("crest_factor_db")
    tp = lo.get("true_peak_dbfs")

    # ---------- Stage 1: GAIN / HEADROOM / BRICKWALL ----------
    brickwalled = False
    if tp is not None and tp > 0.0:
        F.append((1, 0, f"True Peak {tp:+.1f} dBTP > 0 -> Clipping/Intersample.", "Pegel runter, True-Peak-Limit -1 dBTP."))
    elif tp is not None and tp > -1.0:
        F.append((1, 1, f"True Peak {tp:+.1f} dBTP > -1 -> Intersample-Risiko.", "Ceiling auf -1 dBTP setzen."))
    if crest < 6.0:
        brickwalled = True
        F.append((1, 0, f"Crest {crest:.1f} dB sehr niedrig -> stark ueberlimitiert.", "Lowend zuruecknehmen; Messungen unten sind unzuverlaessig (man misst den Limiter)."))
    elif ref_crest is not None and crest < ref_crest - 2.0:
        brickwalled = True
        F.append((1, 1, f"Crest {crest:.1f} dB << Referenz {ref_crest:.1f} -> deutlich staerker limitiert als die Referenz.", "Lowend/Master-Limiting lockern; sonst sind Spektral-/std-Aussagen verfaelscht."))
    elif ref_crest is not None and crest > ref_crest + 3.0:
        F.append((1, 1, f"Crest {crest:.1f} dB >> Referenz {ref_crest:.1f} -> weniger dicht als die Referenz.", "Bus-Glue/Saettigung/Limiting pruefen (Referenz: Dichte kommt oft vom Master-Bus, nicht den Einzelspuren)."))
    else:
        F.append((1, 2, f"Crest {crest:.1f} dB (Ref {ref_crest if ref_crest is not None else '?'}) -> Headroom plausibel.", ""))
    if rlo.get("integrated_lufs") is not None:
        dl = lo["integrated_lufs"] - rlo["integrated_lufs"]
        if abs(dl) > 1.5:
            F.append((1, 2, f"LUFS {lo['integrated_lufs']:.1f} vs Ref {rlo['integrated_lufs']:.1f} ({dl:+.1f} LU).", "Fuer fairen A/B-Vergleich loudness-matchen (nicht 'lauter = besser')."))

    # Master-Modus: Fortschritt zur Loudness-Zielmarke + Dichte-Weg + TP-Headroom
    target = arg("--target-lufs")
    if target is not None:
        tl = float(target)
        gap = tl - lo["integrated_lufs"]
        crest_ref = ref_crest if ref_crest is not None else 8.3
        crest_gap = crest - crest_ref            # >0 = noch zu dynamisch (mehr Dichte noetig)
        tp_head = (-1.0 - tp) if tp is not None else None
        sev = 1 if abs(gap) > 1.0 else 2
        tph = f"{tp_head:+.1f} dB bis -1 dBTP" if tp_head is not None else "TP ?"
        F.append((1, sev,
                  f"MASTER-ZIEL: LUFS {lo['integrated_lufs']:.1f} -> {tl:.1f} ({gap:+.1f} LU) | "
                  f"Dichte Crest {crest:.1f} -> {crest_ref:.1f} (noch {crest_gap:+.1f} dB) | {tph}.",
                  "Konservativ: erst Glue+Saettigung fuer Dichte, dann Limiter sanft Richtung Ziel; TP-Ceiling -1 dBTP."))

    # ---------- Stage 2: ELEMENT-BALANCE vs Referenz (Fader zuerst) ----------
    if stems_dir and ref_stems:
        mine = stem_levels(stems_dir)
        rb = ref_stems.get("stem_balance_db_rel_loudest", {})
        # Referenz auf drums=0 umbasieren (loudest ist meist drums)
        rdrums = rb.get("drums", 0.0)
        refb = {k: round(v - rdrums, 1) for k, v in rb.items()}
        if mine:
            label = {"drums": "Drums", "bass": "Bass", "other": "Stabs/Synths", "vocals": "Vocals/Lead"}
            for k in ("bass", "other", "drums", "vocals"):
                if k in mine and k in refb:
                    d = round(mine[k] - refb[k], 1)
                    if k == "vocals" and min(mine[k], refb[k]) < -45:
                        continue  # beide praktisch still
                    if abs(d) >= 3.0:
                        verb = "lauter" if d < 0 else "leiser"
                        F.append((2, 1, f"{label.get(k,k)} {mine[k]:+.1f} dB vs Drums (Ref {refb[k]:+.1f}) -> {d:+.1f} dB daneben.",
                                  f"{label.get(k,k)}-Fader ~{abs(d):.0f} dB {verb} (Balance vor EQ)."))
                    else:
                        F.append((2, 2, f"{label.get(k,k)} {mine[k]:+.1f} dB vs Drums (Ref {refb[k]:+.1f}) -> sitzt ({d:+.1f}).", ""))
    elif stems_dir or ref_stems:
        F.append((2, 2, "Element-Balance uebersprungen: brauche --stems UND --ref-stems.", "Demucs-Stems beider Seiten bereitstellen."))

    # ---------- Stage 3: MONO / PHASE ----------
    st = me.get("stereo") or {}
    if not st:
        F.append((3, 2, "Mono-Datei -> kein Stereo-/Phasen-Check.", ""))
    else:
        cb = st.get("correlation_by_band", {})
        if st.get("verdict_bass_mono") is False:
            F.append((3, 1, f"Bass nicht mono (Seitenanteil <120 Hz = {st.get('bass_side_ratio_below_120hz')}).",
                      "Mono-Maker/Utility-Bass-Mono < 120 Hz (Referenz: Bass meist mono)."))
        if cb.get("low") is not None and cb["low"] < 0.7:
            F.append((3, 1, f"Tiefen-Korrelation {cb['low']} -> Phasenprobleme im Bass.", "Phase/Polaritaet pruefen, Bass mono machen."))
        if st.get("correlation") is not None and st["correlation"] < 0.5:
            F.append((3, 1, f"Gesamt-Korrelation {st['correlation']} niedrig -> Mono-Summe verliert Energie.", "Breitmacher/Phasen pruefen (Mono-Kompatibilitaet)."))
        if not any(f[0] == 3 and f[1] < 2 for f in F):
            F.append((3, 2, f"Mono ok (Korr {st.get('correlation')}, Bass mono={st.get('verdict_bass_mono')}).", ""))

    # ---------- Stage 4: TONBALANCE vs Referenz (Summe) ----------
    rbands = ref.get("frequency_balance_db_rel")
    if rbands:
        if brickwalled:
            F.append((4, 2, "Hinweis: Tonbalance unten ist wegen Brickwall (Stage 1) mit Vorsicht zu lesen.", "Erst Gain-Staging fixen, dann erneut messen."))
        for name, _, _ in BANDS:
            d = round(me["frequency_balance_db_rel"][name] - rbands[name], 1)
            if abs(d) >= 3.0:
                hochrunter = "zu viel" if d > 0 else "zu wenig"
                F.append((4, 1, f"{name}: {d:+.1f} dB vs Referenz ({hochrunter}).",
                          "An der QUELLE/Element angehen, part-weise (analyze_sections.py), nicht pauschal am Master."))
        cen = me.get("spectral_centroid_hz"); rcen = ref.get("spectral_centroid_hz")
        if rcen:
            F.append((4, 2, f"Centroid {cen} Hz vs Ref {rcen} Hz ({'heller' if cen>rcen else 'dunkler'}).",
                      "Part-weise pruefen: duenne Parts kippen die Helligkeit, nicht der Drop."))

    # ---------- Stage 5: RESONANZ-DIAGNOSE (nur melden) ----------
    ref_reso = ref.get("resonances_hz") or []
    ref_max = max([r["excess_db"] for r in ref_reso], default=6.0)
    for r in (me.get("resonances_hz") or [])[:5]:
        if r["excess_db"] > ref_max:
            F.append((5, 1, f"Resonanz {r['freq']} Hz +{r['excess_db']} dB > Ref-Niveau ({ref_max}).",
                      "An der QUELLE (Sample/Velocity). KEIN schmaler Master-Notch (Notch-Falle)."))
        else:
            F.append((5, 2, f"Resonanz {r['freq']} Hz +{r['excess_db']} dB <= Ref-Niveau ({ref_max}) -> eher lassen.", ""))

    # ---------- Ausgabe ----------
    F.sort(key=lambda x: (x[0], x[1]))
    print(f"\nMIX-DIAGNOSE  {me['file']}  vs  {ref.get('file','<keine Referenz>')}")
    print("=" * 72)
    cur = None
    for order, sev, text, action in F:
        if order != cur:
            cur = order
            print(f"\n[{order} {STAGE[order]}]")
        print(f"  {ICON[sev]} {text}")
        if action and sev < 2:
            print(f"      -> {action}")

    todo = [f for f in F if f[1] < 2]
    print("\n" + "=" * 72)
    if todo:
        print("PRIORITAET (in dieser Reihenfolge fixen):")
        for i, (order, sev, text, action) in enumerate(todo, 1):
            print(f"  {i}. [{STAGE[order]}] {text}")
    else:
        print("Kein Handlungsbedarf gefunden — Mix sitzt gegen die Referenz.")
    print()

    out = arg("--json")
    if out:
        rep = {"file": me["file"], "reference": ref.get("file"),
               "findings": [{"stage": STAGE[o], "severity": ["block", "warn", "info"][s],
                             "text": t, "action": a} for o, s, t, a in F]}
        json.dump(rep, open(out, "w"), indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()
