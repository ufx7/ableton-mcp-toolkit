# Arrangement-Profiler — erzaehlt die "Geschichte" eines Tracks aus seinen Stems.
# Aufruf: uv run --with librosa,soundfile,numpy,scipy python arrangement_profile.py <stems_ordner> <fullmix.wav> [fenster_s]
#
# Liefert pro Zeitfenster: Praesenz (RMS) jedes Stems, Helligkeit (Centroid),
# Stereo-Seite und Onset-Dichte der Drums -> als kompakte ASCII-Zeitkarte.
# 0-9 = Intensitaet (relativ zum jeweiligen Stem-Maximum), "." = praktisch stumm.

import sys, os, json
import numpy as np
import soundfile as sf

def rms_curve(y, sr, win):
    n = int(sr*win)
    k = len(y)//n
    return np.array([np.sqrt(np.mean(y[i*n:(i+1)*n]**2)) for i in range(k)])

def row(vals, floor_db=-55.0):
    out=[]
    ref = np.max(vals) if np.max(vals) > 0 else 1.0
    for v in vals:
        db = 20*np.log10(v/ref + 1e-12)
        if db < floor_db: out.append(".")
        else: out.append(str(min(9, int((db - floor_db) / (-floor_db) * 10))))
    return "".join(out)

def main():
    stems_dir, mix_path = sys.argv[1], sys.argv[2]
    win = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0

    print("Fenster: %.1f s pro Zeichen" % win)
    curves={}
    for f in sorted(os.listdir(stems_dir)):
        if not f.endswith(".wav"): continue
        y, sr = sf.read(os.path.join(stems_dir, f), always_2d=True)
        mono = y.mean(axis=1)
        curves[f[:-4]] = rms_curve(mono, sr, win)

    y, sr = sf.read(mix_path, always_2d=True)
    mono = y.mean(axis=1)
    mixc = rms_curve(mono, sr, win)
    n = int(sr*win); k = len(mono)//n

    # Helligkeit (Centroid) + Seite je Fenster
    cent=[]; side=[]
    for i in range(k):
        seg = y[i*n:(i+1)*n]
        m = seg.mean(axis=1)
        sp = np.abs(np.fft.rfft(m * np.hanning(len(m))))**2
        fr = np.fft.rfftfreq(len(m), 1/sr)
        cent.append(float((fr*sp).sum()/(sp.sum()+1e-12)))
        if seg.shape[1] >= 2:
            mid=(seg[:,0]+seg[:,1])/2; sd=(seg[:,0]-seg[:,1])/2
            e=(mid**2).sum()+(sd**2).sum()
            side.append(float((sd**2).sum()/e) if e>0 else 0.0)
        else: side.append(0.0)
    cent=np.array(cent); side=np.array(side)

    # Zeitleiste in Minuten
    marks=[]
    for i in range(k):
        t=i*win
        marks.append("|" if abs(t % 60) < win/2 else " ")
    print("Min:      " + "".join(marks) + "   (| = Minutenmarke)")
    for name in ("drums","bass","other","vocals"):
        if name in curves:
            print("%-9s %s" % (name, row(curves[name][:k])))
    print("%-9s %s" % ("MIX", row(mixc)))
    # Helligkeit: 0-9 relativ zwischen 100 und 3000 Hz (log)
    cl = np.clip((np.log10(np.maximum(cent,1)) - 2) / (np.log10(3000)-2), 0, 1)
    print("%-9s %s" % ("hell", "".join(str(int(c*9)) for c in cl)))
    sl = np.clip(side / max(side.max(), 1e-9), 0, 1)
    print("%-9s %s" % ("seite", "".join(str(int(s*9)) for s in sl)))

if __name__ == "__main__":
    main()
