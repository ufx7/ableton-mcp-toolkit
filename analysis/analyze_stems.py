# Stem-Analyse — alle Stems eines Demucs-Outputs durch den Analyzer ziehen
# Aufruf: uv run --with librosa,pyloudnorm,soundfile,numpy,scipy python analyze_stems.py <stems_ordner> [--json <out.json>]
#
# Erwartet einen Ordner mit drums.wav / bass.wav / other.wav / vocals.wav
# (Demucs-Layout: stems/htdemucs/<trackname>/*.wav).
# Liefert pro Stem die volle Signatur + die Pegel-Verhaeltnisse zwischen den Stems.

import sys, os, json
from analyze_audio import analyze

def main():
    folder = sys.argv[1]
    out = None
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]

    stems = {}
    for f in sorted(os.listdir(folder)):
        if f.endswith(".wav"):
            name = f[:-4]
            stems[name] = analyze(os.path.join(folder, f))

    # Pegel-Verhaeltnisse (RMS) relativ zum lautesten Stem
    rms = {k: v["loudness"]["rms_dbfs"] for k, v in stems.items()}
    loudest = max(rms, key=rms.get)
    balance = {k: round(v - rms[loudest], 1) for k, v in rms.items()}

    report = {
        "stems_folder": folder,
        "stem_balance_db_rel_loudest": balance,
        "loudest_stem": loudest,
        "stems": stems,
    }
    txt = json.dumps(report, indent=2, ensure_ascii=False)
    print(txt)
    if out:
        with open(out, "w") as fh:
            fh.write(txt)

if __name__ == "__main__":
    main()
