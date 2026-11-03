#!/usr/bin/env python3
"""aero_gazefit.py CALIB.json STREAM.yuyv.json PUPILS.csv -- per-eye gaze mapping from an xr_calib.py session.

Frame k of the recording is taken at t_stream + LATENCY + k / fps (CLOCK_MONOTONIC, like the target schedule).
For each target, the steady middle of its dwell (skip the first 0.8 s for the eye to land, and the last 0.3 s) gives
the median feature per eye, two features compared:
  PCCR  = pupil centre - mean glint position (pixels; tolerant of headset slip on the face)
  pupil = pupil centre alone (pixels)
Model per eye and axis: gaze angle = a*fx + b*fy + c (affine, least squares; 5 distinct targets). Reported: the
leave-one-out error (each target predicted from a fit without it) in degrees -- the honest accuracy figure.
Writes CALIB_fit.json with the per-eye coefficients."""
import sys, json, statistics, math
import numpy as np

LATENCY = 0.25                                               # v4l2-ctl start-up after Popen (+-0.2 s; dwell is 3 s)
cal = json.load(open(sys.argv[1])); st = json.load(open(sys.argv[2]))
rows = [l.rstrip("\n").split(",") for l in open(sys.argv[3])][1:]
t_of = lambda k: st["t_stream_monotonic"] + LATENCY + k / st["fps"]

def features(eye):
    per_target = []
    for tg in cal["targets"]:
        a, b = tg["t"] + 0.8, tg["t"] + tg["dwell"] - 0.3
        pc, pu = [], []
        for r in rows:
            k, e, lit = int(r[0]), int(r[1]), r[2] == "1"
            if e != eye or not lit or not r[3] or not (a <= t_of(k) <= b): continue
            cx, cy = float(r[3]), float(r[4]); pu.append((cx, cy))
            gl = [tuple(map(float, g.split(":"))) for g in r[9].split(";") if g]
            if len(gl) >= 3:
                gx, gy = statistics.fmean(g[0] for g in gl), statistics.fmean(g[1] for g in gl); pc.append((cx - gx, cy - gy))
        med = lambda v: (statistics.median(x for x, _ in v), statistics.median(y for _, y in v)) if len(v) >= 5 else None
        per_target.append({"ax": tg["ax"], "ay": tg["ay"], "pccr": med(pc), "pupil": med(pu), "n": len(pu)})
    return per_target

def fit(pts):
    X = np.array([[p[0], p[1], 1] for p, _ in pts]); Y = np.array([g for _, g in pts])
    return np.linalg.lstsq(X, Y, rcond=None)[0]

out = {}
for eye in (0, 1):
    ft = features(eye)
    print(f"camera {'AB'[eye]}: samples per target {[t['n'] for t in ft]}")
    for kind in ("pccr", "pupil"):
        pts = [((t[kind]), (t["ax"], t["ay"])) for t in ft if t[kind] is not None]
        # merge the two centre visits into one point (same target)
        uniq = {}
        for f, g in pts: uniq.setdefault(g, []).append(f)
        pts = [((statistics.fmean(f[0] for f in fs), statistics.fmean(f[1] for f in fs)), g) for g, fs in uniq.items()]
        if len(pts) < 4: print(f"  {kind}: only {len(pts)} targets with data"); continue
        errs = []
        for i in range(len(pts)):
            c = fit(pts[:i] + pts[i + 1:]); p, g = pts[i]
            pred = np.array([p[0], p[1], 1]) @ c; errs.append(math.hypot(pred[0] - g[0], pred[1] - g[1]))
        C = fit(pts); out[f"{'AB'[eye]}_{kind}"] = C.tolist()
        print(f"  {kind:5s}: leave-one-out error per target (deg) " + " ".join(f"{e:4.1f}" for e in errs)
              + f"  -> mean {statistics.fmean(errs):.1f} deg")
json.dump(out, open(sys.argv[1].rsplit(".", 1)[0] + "_fit.json", "w"), indent=1)
