# STFT-Zeitanalyse — was die Mittelwert-Analyse NICHT zeigt:
# wie sich das Spektrum ueber die Zeit entwickelt und wie STABIL jedes Band ist.
# Aufruf: uv run --with librosa,soundfile,numpy,scipy python analyze_stft.py <datei> [--json out]
#
# Kennzahlen je 7-Band:
#   mean_db   - mittlere Energie (wie bei der Summen-Analyse, Quercheck)
#   std_db    - Streuung ueber die Zeit = "Bewegung/Wandern" des Bands
#               (niedrig = konstant/hypnotisch, hoch = lebendig/wandernd)
# Plus: spectral_flux (Tempo der spektralen Aenderung), und eine ASCII-Zeitkarte.

import sys, json
import numpy as np
import soundfile as sf
from scipy.signal import stft

BANDS = [("sub",20,60),("bass",60,120),("lowmid",120,350),
         ("mid",350,2000),("highmid",2000,6000),("high",6000,12000),("air",12000,20000)]

def analyze(path, frame_s=0.093):
    y, srr = sf.read(path, always_2d=True)
    mono = y.mean(axis=1).astype(np.float64)
    sr = srr
    nper = int(sr*frame_s); nper = 2**int(round(np.log2(max(nper,1024))))
    f, t, Z = stft(mono, fs=sr, nperseg=nper, noverlap=nper//2)
    P = np.abs(Z)**2  # power, shape (freq, time)
    total = P.sum(axis=0) + 1e-12  # pro Frame Gesamtenergie

    band_db = {}   # zeitlicher Verlauf je Band (dB rel. Frame-Gesamt)
    for name, lo, hi in BANDS:
        m = (f>=lo)&(f<hi)
        frac = P[m,:].sum(axis=0)/total
        band_db[name] = 10*np.log10(frac+1e-12)

    # Nur Frames mit Signal (nicht Stille)
    rms_frame = np.sqrt(P.sum(axis=0)/P.shape[0])
    active = rms_frame > (rms_frame.max()*0.05)

    stats = {}
    for name,_,_ in BANDS:
        vals = band_db[name][active]
        stats[name] = {"mean_db": round(float(np.mean(vals)),1),
                       "std_db": round(float(np.std(vals)),2)}

    # Spectral Flux: mittlere quadr. Aenderung des (normierten) Magnitudenspektrums
    mag = np.sqrt(P)
    magn = mag/(mag.sum(axis=0)+1e-12)
    flux = np.sqrt(((np.diff(magn,axis=1))**2).sum(axis=0))
    flux_mean = round(float(np.mean(flux[active[1:]])),4)

    return {"file":path.split("/")[-1], "frame_ms":round(nper/sr*1000),
            "bands":stats, "spectral_flux":flux_mean,
            "_heat":{name:band_db[name][active] for name,_,_ in BANDS}}

def heatmap(rep, cols=60):
    print("\nZeitkarte (Energie je Band, 0-9; '.'=schwach):")
    for name,_,_ in BANDS:
        v = rep["_heat"][name]
        # auf cols runterrechnen
        idx = np.linspace(0,len(v)-1,cols).astype(int)
        vv = v[idx]
        lo,hi = vv.min(), vv.max()
        row = "".join("." if (hi-lo)<1e-6 else str(min(9,max(0,int((x-lo)/(hi-lo)*9)))) for x in vv)
        print("  %-8s %s" % (name,row))

def main():
    path=sys.argv[1]; out=None
    if "--json" in sys.argv: out=sys.argv[sys.argv.index("--json")+1]
    rep=analyze(path)
    pub={k:v for k,v in rep.items() if k!="_heat"}
    print(json.dumps(pub,indent=2,ensure_ascii=False))
    heatmap(rep)
    if out:
        open(out,"w").write(json.dumps(pub,indent=2,ensure_ascii=False))

if __name__=="__main__":
    main()
