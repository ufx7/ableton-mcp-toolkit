# EQ-Matcher — vergleicht das Spektrum eines Tracks mit einer Referenz und
# schlaegt eine Korrektur-EQ-Kurve vor (EQ-Eight-Baender).
#
# Aufruf: uv run --with soundfile,numpy,scipy python eq_match.py <track.wav> <referenz.wav> [--json out] [--bands 6]
#
# Prinzip (wie ADPTR Metric AB / TDR-Match, nur offline):
#   1. Beide Spektren level-normalisieren (es geht um BALANCE, nicht Lautheit)
#   2. Differenz (Referenz - Track) in dB ueber Frequenz
#   3. 1/3-Oktav-Glaettung, dann auf N musikalische Bell-Baender reduzieren
#   4. Gain pro Band auf +/-6 dB begrenzen (sanft, gegen Ueberkorrektur)

import sys, json
import numpy as np
import soundfile as sf
from scipy.signal import welch
from scipy.ndimage import uniform_filter1d

# Sinnvolle EQ-Eight-Zielfrequenzen (Bell), grob 1 Oktave Abstand
DEFAULT_BANDS = [60, 120, 300, 800, 2500, 6000, 12000]

def avg_spectrum(path):
    y, sr = sf.read(path, always_2d=True)
    mono = y.mean(axis=1)
    f, pxx = welch(mono, fs=sr, nperseg=16384)
    db = 10*np.log10(pxx + 1e-12)
    return f, db

def main():
    track, ref = sys.argv[1], sys.argv[2]
    out = sys.argv[sys.argv.index("--json")+1] if "--json" in sys.argv else None
    nb = int(sys.argv[sys.argv.index("--bands")+1]) if "--bands" in sys.argv else 7
    bands = DEFAULT_BANDS[:nb] if nb <= len(DEFAULT_BANDS) else DEFAULT_BANDS

    ft, dt = avg_spectrum(track)
    fr, dr = avg_spectrum(ref)
    # auf gemeinsames Frequenzraster (Track-Raster) interpolieren
    dr_i = np.interp(ft, fr, dr)

    # Level-Normalisierung: beide im Band 200-2000 Hz auf 0 dB Mittel
    norm = (ft >= 200) & (ft <= 2000)
    dt_n = dt - dt[norm].mean()
    dr_n = dr_i - dr_i[norm].mean()

    diff = dr_n - dt_n  # positiv = Referenz hat hier MEHR -> Track anheben

    # 1/3-Oktav-Glaettung (log-frequenz)
    diff_s = uniform_filter1d(diff, size=15)

    # Pro Ziel-Band den mittleren Korrektur-Gain (Fenster +/- 1/3 Oktave)
    curve = []
    for fc in bands:
        lo, hi = fc/2**(1/3.), fc*2**(1/3.)
        m = (ft >= lo) & (ft < hi)
        if not m.any():
            continue
        g = float(np.clip(np.median(diff_s[m]), -6.0, 6.0))
        curve.append({"freq": fc, "gain_db": round(g, 1)})

    report = {
        "track": track.split("/")[-1],
        "reference": ref.split("/")[-1],
        "eq_curve": curve,
        "hinweis": "Positiver Gain = Referenz hat dort mehr Energie -> Track anheben. +/-6 dB begrenzt.",
    }
    txt = json.dumps(report, indent=2, ensure_ascii=False)
    print(txt)
    # ASCII-Visualisierung
    print("\nKorrektur-Kurve:")
    for b in curve:
        n = int(round(b["gain_db"]*2))
        bar = ("+"*n if n > 0 else "-"*(-n)) or "0"
        print("  %6d Hz  %+4.1f dB  %s" % (b["freq"], b["gain_db"], bar))
    if out:
        open(out, "w").write(txt)

if __name__ == "__main__":
    main()
