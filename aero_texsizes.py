#!/usr/bin/env python3
"""aero_texsizes.py VIEWSET QUALITY -- render sizes for the Aero at a quality level: prints "CONTEXT_WxH INSET_PX"
(inset 0 for stereo).

View sets: dynamic_foveation (eye-tracked), quad (fixed foveation), stereo (plain two-view apps: most games).
Qualities: VeryLow, Low, Fair, Medium, High, VeryHigh, ExtremelyHigh, UltraHigh, Highest -- the levels Aero owners
know from Varjo Base. Eye-tracked sizes come from FOVEATED_SIZES; quad and stereo from data/varjo-aero-texture-sizes.json,
stereo scaled by STEREO_SCALE.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aero_data

QUALITIES = ["VeryLow", "Low", "Fair", "Medium", "High", "VeryHigh", "ExtremelyHigh", "UltraHigh", "Highest"]

# Eye-tracked (dynamic_foveation) sizes per quality: pixels per degree, inset (square) size, context size.
# Highest = UltraHigh.
FOVEATED_SIZES = {
    "VeryLow": (27, 832, "828x710"), "Low": (29, 876, "950x816"), "Fair": (30, 922, "1092x936"),
    "Medium": (32, 970, "1256x1076"), "High": (33, 1020, "1444x1236"), "VeryHigh": (35, 1076, "1660x1420"),
    "ExtremelyHigh": (37, 1140, "1908x1632"), "UltraHigh": (39, 1200, "2192x1880"),
}
FOVEATED_SIZES["Highest"] = FOVEATED_SIZES["UltraHigh"]
STEREO_SCALE = 0.964                                         # stereo context = table size x this, rounded to even


def even(x):
    return round(x / 2) * 2


def lookup(viewset, quality):
    if viewset == "dynamic_foveation" and quality in FOVEATED_SIZES:
        ppd, inset, context = FOVEATED_SIZES[quality]
        print(context, inset)
        return
    aero = json.load(open(aero_data.data_path("varjo-aero-texture-sizes.json")))["gen3_hedy"]
    if quality not in aero[viewset]:
        sys.exit(f"aero_texsizes: quality {quality!r} not in {', '.join(aero[viewset])}")
    views = [v.replace("*", "x") for v in aero[viewset][quality]]
    inset = views[2].split("x")[0] if len(views) > 2 else "0"
    if viewset == "stereo":
        w, h = (int(n) for n in views[0].split("x"))
        views[0] = f"{even(w * STEREO_SCALE)}x{even(h * STEREO_SCALE)}"
    print(views[0], inset)


if __name__ == "__main__":
    if len(sys.argv) == 3:
        lookup(sys.argv[1], sys.argv[2])
    else:
        sys.exit(__doc__)
