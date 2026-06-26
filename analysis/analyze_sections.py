# Part-für-Part-Spektralanalyse eines GANZEN Songs gegen eine Referenz.
# Motivation: Ein einzelnes gemitteltes Fenster verschleiert part-spezifische Probleme
# (z.B. zu laute Ride/Hats in DÜNNEN Parts, die in dichten Drops maskiert sind).
# Das ist der manuelle Vorläufer von Apples Music-Understanding-"Structure"-Layer.
#
# Aufruf:
#   uv run --with soundfile,numpy,scipy python analyze_sections.py <vollsong.wav> \
#       --ref <referenz.json> --bpm 138 --start-beat 1.05 \
#       --sections "INTRO:0:160,1.DROP:160:288,BREAK:288:352,DROP2:352:480,END:480:640"
# Ohne --sections: 8 gleich lange Parts. Bänder/Methode identisch zu analyze_audio.py
# (Welch-Spektrum der Mono-Summe, dB relativ zur Gesamtenergie) -> direkt vergleichbar.

import sys, json
import numpy as np
import soundfile as sf
from scipy.signal import welch

BANDS = [("sub",20,60),("bass",60,120),("lowmid",120,350),("mid",350,2000),
         ("highmid",2000,6000),("high",6000,12000),("air",12000,20000)]

def arg(flag, default=None):
    return sys.argv[sys.argv.index(flag)+1] if flag in sys.argv else default

def bands_of(sig, sr):
    f, pxx = welch(sig, fs=sr, nperseg=8192); tot = pxx.sum()
    if tot <= 0: return {n: -120.0 for n,_,_ in BANDS}, 0.0
    out = {n: round(10*np.log10(pxx[(f>=lo)&(f<hi)].sum()/tot + 1e-12), 1) for n,lo,hi in BANDS}
    return out, float((f*pxx).sum()/pxx.sum())

def main():
    path = sys.argv[1]
    bpm = float(arg("--bpm", "138"))
    start_beat = float(arg("--start-beat", "0"))   # bei welchem Beat die Aufnahme begann
    ref = json.load(open(arg("--ref"))) ["frequency_balance_db_rel"] if arg("--ref") else None
    y, sr = sf.read(path, always_2d=True); mono = y.astype(np.float64).mean(1)
    dur = len(mono)/sr; spb = 60.0/bpm                      # Sekunden pro Beat
    def t(beat): return max(0.0, (beat - start_beat)*spb)

    sec_arg = arg("--sections")
    if sec_arg:
        sections = []
        for chunk in sec_arg.split(","):
            nm, b0, b1 = chunk.split(":"); sections.append((nm, float(b0), float(b1)))
    else:
        total_beats = dur/spb + start_beat
        edges = np.linspace(start_beat, total_beats, 9)
        sections = [("P%d"%(i+1), edges[i], edges[i+1]) for i in range(8)]

    hdr = "PART                 | " + "  ".join("%-7s"%n for n,_,_ in BANDS) + " | Cent"
    if ref: hdr += " | high air Δref"
    print(hdr); print("-"*len(hdr))
    rows = []
    for nm, b0, b1 in sections:
        s0, s1 = int(t(b0)*sr), min(len(mono), int(t(b1)*sr))
        if s1 - s0 < sr: continue
        bd, cen = bands_of(mono[s0:s1], sr)
        line = "%-20s | "%nm + "  ".join("%7.1f"%bd[n] for n,_,_ in BANDS) + " | %4.0f"%cen
        if ref:
            dh, da = bd["high"]-ref["high"], bd["air"]-ref["air"]
            flag = "  <== HÖHEN HOCH" if (dh>3 or da>4) else ""
            line += " | %+4.1f %+4.1f%s"%(dh, da, flag)
        print(line)
        rows.append({"part":nm,"beats":[b0,b1],"bands":bd,"centroid_hz":round(cen)})
    out = arg("--json")
    if out: json.dump(rows, open(out,"w"), indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()
