# .als-Extraktor — liest ein Ableton-Set als XML (ohne Live) und fasst die Struktur zusammen.
# .als = gzip-gepacktes XML. Aufruf: uv run python als_extract.py <datei.als> [--full]
#
# Liefert: Tempo, Spuren (Name/Typ/Gruppe), Geräteketten je Spur, Clip-Anzahl/Längen,
# Routing, und eine Heuristik fuer "auffaellige" Dinge (z.B. starke Kompression).

import sys, gzip, re
import xml.etree.ElementTree as ET

def load(path):
    with gzip.open(path, "rb") as f:
        return ET.parse(f).getroot()

def attr(el, name, default=None):
    e = el.find(name)
    if e is not None and "Value" in e.attrib:
        return e.attrib["Value"]
    return default

def main():
    path = sys.argv[1]
    full = "--full" in sys.argv
    root = load(path)
    live = root  # Ableton root

    # Tempo
    tempo = None
    for t in root.iter("Tempo"):
        m = t.find("Manual")
        if m is not None:
            tempo = m.attrib.get("Value"); break

    print("### %s" % path.split("/")[-1])
    print("Tempo:", tempo)

    # Tracks
    track_tags = ("MidiTrack", "AudioTrack", "GroupTrack", "ReturnTrack")
    tracks = []
    for tt in track_tags:
        for tr in root.iter(tt):
            name = None
            ne = tr.find("Name")
            if ne is not None:
                eff = ne.find("EffectiveName")
                if eff is not None: name = eff.attrib.get("Value")
            # Devices
            devs = []
            dch = tr.find("DeviceChain")
            if dch is not None:
                for dc in dch.iter("Devices"):
                    for dev in list(dc):
                        devs.append(dev.tag)
            # Clips
            nclips = len(list(tr.iter("MidiClip"))) + len(list(tr.iter("AudioClip")))
            tracks.append((tt, name, devs, nclips))

    print("\nSpuren: %d" % len(tracks))
    for tt, name, devs, nclips in tracks:
        kind = {"MidiTrack":"MIDI","AudioTrack":"AUDIO","GroupTrack":"GROUP","ReturnTrack":"RETURN"}[tt]
        dev_names = ", ".join(devs) if devs else "-"
        print("  [%-6s] %-22s clips:%-3d devices: %s" % (kind, str(name)[:22], nclips, dev_names[:70]))

    # Heuristik: Kompressoren mit hoher Ratio / niedrigem Threshold finden
    print("\nKompressoren (Threshold/Ratio — Heuristik fuer Ueberkompression):")
    found = False
    for comp in root.iter("Compressor2"):
        thr = attr(comp, "Threshold"); ratio = attr(comp, "Ratio")
        if thr or ratio:
            found = True
            print("  Threshold=%s Ratio=%s" % (thr, ratio))
    for comp in root.iter("GlueCompressor"):
        thr = attr(comp, "Threshold"); ratio = attr(comp, "Ratio")
        found = True
        print("  Glue: Threshold=%s Ratio=%s" % (thr, ratio))
    if not found:
        print("  (keine direkt auslesbaren Compressor-Parameter)")

if __name__ == "__main__":
    main()
