#!/usr/bin/env python3
"""aero_gazefilter.py -- gaze filters for aero_eyetrack.py (kept apart so they can be replayed offline:
`python3 aero_gazefilter.py REC.jsonl` runs a recorded UDP stream through every filter and prints statistics).

Gaze in and out = [x, y] degrees (+x right, +y up, head-locked view angles), one call per camera frame (200 Hz);
None = no pupil this frame. Every filter HOLDS its last output for up to 0.3 s through frames without a pupil -- the
LED-off half of the glint-LED pattern (20 of every 40 frames) and blinks -- instead of reporting 'no gaze' (Monado
would snap the inset back to the centre several times a second).

Filters (AERO_GAZE_FILTER):
  varjo     the default: a predictive foveation gaze filter. Two stages per tracker sample:
            1. Predictor: the last 3 gaze VELOCITIES (tracker output; newest first), projected into the tangent
               plane at the current gaze, combined with one coefficient row and mapped back to the sphere (inverse
               stereographic) -> predicted gaze. Any of the 3 velocities missing -> no prediction (current gaze
               used). Result clamped to a 55 deg cone around straight ahead.
               Coefficients: 5 rows x 3 (VARJO_COEF); rows 0-3 are variants, row 4 (the default, used here) is
               about their mean.
            2. Stabiliser, against the previous OUTPUT: angle a to the new gaze; a >= 2 deg (saccade)
               or a <= 0.025 deg -> jump to it; otherwise step min(a, max(a - 0.5 deg, 0) + 0.025 deg) towards it.
               So fixation jitter under 0.5 deg is reduced to a 0.025 deg/sample creep (5 deg/s at 200 Hz), and
               smooth pursuit trails by at most ~0.5 deg.
            The formula has no time step: the velocity must be per tracker sample (per second would mean
            predicting seconds ahead). Ours = difference to the previous camera frame (5 ms), only between
            consecutive frames with the same eyes seen (the mean of both eyes jumps when one eye drops out). With a
            constant velocity the prediction leads by 1.823 + 1.081 + 0.342 = 3.25 samples = ~16 ms.
            Small-angle form: our gaze already is angles in degrees, and the tangent-plane/stereographic mapping
            differs from plain angle addition by < 0.1 % at the few-degree leads involved.
  twospeed  the earlier filter: steps under 3 deg smoothed (alpha 0.15 per 5 ms frame, ~30 ms), larger
            jumps eased (alpha 0.5, ~97 % in 25 ms) instead of snapping in one frame (clearly better than raw).
  off       raw gaze (still held through LED-off frames).
"""
import json, math, sys

HOLD_S = 0.3
VARJO_COEF = {                                   # predictor coefficient rows (newest velocity first)
    0: (1.802458105338492, 1.094017906830691, 0.35673460975802984),
    1: (1.837117789037851, 1.057580975556444, 0.3294973155355025),
    2: (1.8383000725781342, 1.0912907330415567, 0.34941689390671127),
    3: (1.8160861240032726, 1.0776812808983214, 0.3325231281659672),
    4: (1.822882766948679, 1.0811816716881684, 0.34244043119015966),   # default
}
CONE_DEG = 55.0                                  # cos 55 = 0.5736, sin^2 55 = 0.6710
STAB_JUMP_DEG, STAB_BAND_DEG, STAB_CREEP_DEG = 2.0, 0.5, 0.025   # 0.0349 / 0.00873 / 0.000436 rad
FILTERS = ("varjo", "twospeed", "off")


class GazeFilter:
    def __init__(self, mode="varjo", row=4):
        self.mode = mode if mode in FILTERS else "varjo"
        self.coef = VARJO_COEF[row]
        self.reset()

    def reset(self):
        self.out = None; self.t_out = -1e9           # last output and when a pupil last fed it
        self.prev = None; self.prev_key = None       # previous frame's raw gaze (None = no pupil)
        self.vel = []                                # per-frame velocities, newest first (max 3)

    def set_mode(self, mode):
        if mode in FILTERS and mode != self.mode:
            self.mode = mode
            self.reset()

    def __call__(self, t, g, key=None):
        """t = seconds (monotonic), g = [x, y] deg or None, key = which eyes produced g (e.g. 'AB'); returns [x, y]
        or None"""
        if g is None:
            self.prev = None                         # the velocity chain needs consecutive frames
            self.vel = []
            return self._held(t)
        g = (float(g[0]), float(g[1]))
        if self.mode == "off":
            new = g
        elif self.mode == "twospeed":
            new = self._twospeed(g)
        else:
            new = self._varjo(g, key)
        self.out = new; self.t_out = t
        return [round(new[0], 2), round(new[1], 2)]

    def _held(self, t):
        if self.out is not None and t - self.t_out < HOLD_S:
            return [round(self.out[0], 2), round(self.out[1], 2)]
        self.out = None
        return None

    def _twospeed(self, g):
        if self.out is None:
            return g
        d = math.hypot(g[0] - self.out[0], g[1] - self.out[1])
        a = 0.5 if d > 3.0 else 0.15
        return (self.out[0] + a * (g[0] - self.out[0]), self.out[1] + a * (g[1] - self.out[1]))

    def _varjo(self, g, key):
        # velocity history (tracker-sample units)
        if self.prev is not None and key == self.prev_key:
            self.vel.insert(0, (g[0] - self.prev[0], g[1] - self.prev[1]))
            del self.vel[3:]
        else:
            self.vel = []
        self.prev, self.prev_key = g, key
        # 1. predictor
        p = g
        if len(self.vel) == 3:
            c = self.coef
            p = (g[0] + sum(ci * v[0] for ci, v in zip(c, self.vel)),
                 g[1] + sum(ci * v[1] for ci, v in zip(c, self.vel)))
            r = math.hypot(*p)
            if r > CONE_DEG:
                p = (p[0] * CONE_DEG / r, p[1] * CONE_DEG / r)
        # 2. stabiliser against the previous output
        o = self.out
        if o is None:
            return p
        a = math.hypot(p[0] - o[0], p[1] - o[1])
        if a >= STAB_JUMP_DEG or a <= STAB_CREEP_DEG:
            return p
        step = min(a, max(a - STAB_BAND_DEG, 0.0) + STAB_CREEP_DEG)
        return (o[0] + (p[0] - o[0]) * step / a, o[1] + (p[1] - o[1]) * step / a)


def replay(path):
    """REC.jsonl = the tracker's UDP datagrams, one per line ({"t", "frame", "A", "B", "gaze"})."""
    with open(path) as fh:
        recs = [json.loads(l) for l in fh if l.strip()]
    print(f"{path}: {len(recs)} frames, {sum(r['gaze'] is not None for r in recs)} with gaze, "
          f"{recs[-1]['t'] - recs[0]['t']:.1f} s")
    for mode in FILTERS:
        f = GazeFilter(mode)
        outs = [f(r["t"], r["gaze"], "".join(e for e in "AB" if r[e])) for r in recs]
        # jitter = mean frame-to-frame output movement while the raw gaze moves < 1 deg/frame (fixation-ish);
        # lag proxy = mean distance between output and raw gaze on frames with gaze
        steps, dist = [], []
        for i in range(1, len(recs)):
            r0, r1, o0, o1 = recs[i - 1]["gaze"], recs[i]["gaze"], outs[i - 1], outs[i]
            if r0 and r1 and o0 and o1 and math.dist(r0, r1) < 1.0:
                steps.append(math.dist(o0, o1))
            if r1 and o1:
                dist.append(math.dist(r1, o1))
        print(f"  {mode:9s} fixation jitter {sum(steps) / max(len(steps), 1):.3f} deg/frame   "
              f"mean |out - raw| {sum(dist) / max(len(dist), 1):.2f} deg   outputs {sum(o is not None for o in outs)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    replay(sys.argv[1])
