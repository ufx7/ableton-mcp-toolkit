# Audio-Analyzer v1 — Sound-Signatur einer Referenz erfassen
# Aufruf:  uv run --with librosa,pyloudnorm,soundfile,numpy,scipy python analyze_audio.py <datei> [--json <out.json>]
#
# Liefert die Kenngroessen, die fuer Mixdown-Nachbau zaehlen:
#   Pegel/Loudness:  Integrated LUFS, Sample-Peak dBFS, RMS, Crest-Faktor (Kompressionsgrad)
#   Frequenzbalance: Energie in 7 Baendern (Sub/Bass/LowMid/Mid/HighMid/High/Air), Spektral-Schwerpunkt
#   Stereobild:      Korrelation, Seiten-Anteil gesamt, Mono-Kompatibilitaet des Basses (<120 Hz)
#   Resonanzen:      schmale Peaks, die >6 dB aus dem geglaetteten Spektrum ragen

import sys, json
import numpy as np
import soundfile as sf
import pyloudnorm as pyln
from scipy.signal import welch
from scipy.ndimage import uniform_filter1d

BANDS = [
    ("sub",     20,    60),
    ("bass",    60,    120),
    ("lowmid",  120,   350),
    ("mid",     350,   2000),
    ("highmid", 2000,  6000),
    ("high",    6000,  12000),
    ("air",     12000, 20000),
]

def db(x, floor=-120.0):
    return float(max(floor, 20*np.log10(x))) if x > 0 else floor

def analyze(path):
    y, sr = sf.read(path, always_2d=True)
    y = y.astype(np.float64)
    n_ch = y.shape[1]
    mono = y.mean(axis=1)
    dur = len(mono)/sr

    # --- Pegel & Dynamik ---
    meter = pyln.Meter(sr)
    lufs = meter.integrated_loudness(y if n_ch > 1 else mono)
    peak = float(np.max(np.abs(y)))
    rms = float(np.sqrt(np.mean(mono**2)))
    crest = db(peak) - db(rms)

    # True Peak (Intersample): 4x-Oversampling (Snoman: Sample-Peak unterschaetzt;
    # Stereo-Widener taeuschen sogar True-Peak-Limiter)
    try:
        from scipy.signal import resample_poly
        tp = 0.0
        for ch in range(n_ch):
            up = resample_poly(y[:, ch], 4, 1)
            tp = max(tp, float(np.max(np.abs(up))))
        true_peak_db = db(tp)
    except Exception:
        true_peak_db = None

    # LRA (Loudness Range, EBU-R128-Naeherung): Streuung der Short-Term-Loudness
    lra = None
    try:
        step = int(sr * 1.0); wlen = int(sr * 3.0)
        sl = []
        sig = y if n_ch > 1 else mono.reshape(-1, 1)
        for i in range(0, len(sig) - wlen, step):
            v = meter.integrated_loudness(sig[i:i+wlen])
            if v > -70: sl.append(v)
        if len(sl) >= 10:
            sl = np.array(sl)
            lra = float(np.percentile(sl, 95) - np.percentile(sl, 10))
    except Exception:
        pass

    # --- Frequenzbalance (Welch-Spektrum der Mono-Summe) ---
    f, pxx = welch(mono, fs=sr, nperseg=8192)
    total = pxx.sum()
    if total <= 0 or rms <= 1e-9:
        # Stille: sinnvolle Platzhalter statt NaN-Crash
        return {
            "file": path.split("/")[-1], "duration_s": round(dur, 1),
            "sample_rate": sr, "channels": n_ch, "silent": True,
            "loudness": {"integrated_lufs": -120.0, "sample_peak_dbfs": db(peak),
                          "rms_dbfs": db(rms), "crest_factor_db": 0.0},
            "frequency_balance_db_rel": {name: -120.0 for name, _, _ in BANDS},
            "spectral_centroid_hz": 0, "stereo": {}, "resonances_hz": [],
        }
    bands = {}
    for name, lo, hi in BANDS:
        m = (f >= lo) & (f < hi)
        bands[name] = round(10*np.log10(pxx[m].sum()/total + 1e-12), 1)
    centroid = float((f*pxx).sum()/pxx.sum())

    # --- Stereobild ---
    stereo = {}
    if n_ch >= 2:
        L, R = y[:, 0], y[:, 1]
        denom = np.sqrt((L**2).sum()*(R**2).sum())
        corr = float((L*R).sum()/denom) if denom > 0 else 1.0
        mid = (L+R)/2; side = (L-R)/2
        e_mid = (mid**2).sum(); e_side = (side**2).sum()
        side_ratio = float(e_side/(e_mid+e_side)) if (e_mid+e_side) > 0 else 0.0
        # Mono-Bass-Check: Seitenanteil unterhalb 120 Hz
        fs_, ps_ = welch(side, fs=sr, nperseg=8192)
        fm_, pm_ = welch(mid,  fs=sr, nperseg=8192)
        lowm = (fs_ < 120)
        low_side = ps_[lowm].sum(); low_mid = pm_[lowm].sum()
        bass_side_ratio = float(low_side/(low_mid+low_side)) if (low_mid+low_side) > 0 else 0.0
        # Korrelation pro Band (Izhaki: Phase-Meter; Bass-Phase ist kritisch)
        corr_bands = {}
        try:
            from scipy.signal import butter, sosfilt
            for bname, blo, bhi in (("low", 20, 120), ("mid", 120, 2000), ("high", 2000, 16000)):
                sos = butter(4, [blo, min(bhi, sr//2 - 100)], btype="band", fs=sr, output="sos")
                Lf, Rf = sosfilt(sos, L), sosfilt(sos, R)
                dn = np.sqrt((Lf**2).sum() * (Rf**2).sum())
                corr_bands[bname] = round(float((Lf*Rf).sum()/dn), 3) if dn > 0 else 1.0
        except Exception:
            pass
        stereo = {
            "correlation": round(corr, 3),
            "correlation_by_band": corr_bands,
            "side_energy_ratio": round(side_ratio, 3),
            "bass_side_ratio_below_120hz": round(bass_side_ratio, 3),
            "verdict_bass_mono": bass_side_ratio < 0.10,
        }

    # --- Resonanzen: schmale Peaks >6 dB ueber geglaettetem Spektrum ---
    px_db = 10*np.log10(pxx + 1e-18)
    smooth = uniform_filter1d(px_db, size=31)
    excess = px_db - smooth
    peaks = []
    region = (f >= 80) & (f <= 12000)
    idx = np.where(region & (excess > 6.0))[0]
    # zu Gruppen benachbarter Bins zusammenfassen, je Gruppe das Maximum
    if len(idx):
        group = [idx[0]]
        for i in idx[1:]:
            if i - group[-1] <= 2:
                group.append(i)
            else:
                k = group[int(np.argmax(excess[group]))]
                peaks.append((float(f[k]), round(float(excess[k]), 1)))
                group = [i]
        k = group[int(np.argmax(excess[group]))]
        peaks.append((float(f[k]), round(float(excess[k]), 1)))
    peaks = sorted(peaks, key=lambda p: -p[1])[:8]

    return {
        "file": path.split("/")[-1],
        "duration_s": round(dur, 1),
        "sample_rate": sr,
        "channels": n_ch,
        "loudness": {
            "integrated_lufs": round(float(lufs), 1),
            "sample_peak_dbfs": round(db(peak), 1),
            "true_peak_dbfs": round(true_peak_db, 1) if true_peak_db is not None else None,
            "loudness_range_lu": round(lra, 1) if lra is not None else None,
            "rms_dbfs": round(db(rms), 1),
            "crest_factor_db": round(crest, 1),
        },
        "frequency_balance_db_rel": bands,
        "spectral_centroid_hz": round(centroid),
        "stereo": stereo,
        "resonances_hz": [{"freq": round(p[0]), "excess_db": p[1]} for p in peaks],
    }

def main():
    path = sys.argv[1]
    out = None
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json")+1]
    rep = analyze(path)
    txt = json.dumps(rep, indent=2, ensure_ascii=False)
    print(txt)
    if out:
        with open(out, "w") as fh:
            fh.write(txt)

if __name__ == "__main__":
    main()
