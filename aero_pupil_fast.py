#!/usr/bin/env python3
"""aero_pupil_fast.py -- fast pupil tracker for live use (import), plus an offline benchmark (run as a script):

  aero_pupil_fast.py STREAM.yuyv PUPILS.csv   time the tracker on a recording and compare with aero_pupils.py

Tracker per eye: search a small window around the last pupil (2.2 x its size, at least 120 px) with ONE threshold set
from that window's darkest pixels (pupil ~36-39 vs iris ~55 in typical images: thr = p2 + 0.45 * (p50 - p2)), close
over the glints, take the roundest pupil-sized blob, fitEllipse. When the pupil is lost (blink, first frame) it falls
back to aero_pupils.py's full threshold sweep over the image. Runs in ~/.local/share/varjo-aero/venv (OpenCV)."""
import math, sys, time
import numpy as np, cv2

W, H = 640, 400
_K9 = np.ones((9, 9), np.uint8); _K5 = np.ones((5, 5), np.uint8)

def _best_blob(mask, amin=600, amax=20000):
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    best = None
    for c in cnts:
        a = cv2.contourArea(c)
        if not (amin < a < amax) or len(c) < 20: continue
        p = cv2.arcLength(c, True); circ = 4 * math.pi * a / (p * p)
        if circ >= 0.6 and (best is None or circ > best[0]): best = (circ, c)
    return best

def full_search(img):
    """aero_pupils.py's threshold sweep (slow, robust) -> ellipse or None"""
    g = cv2.GaussianBlur(img, (7, 7), 0)
    roi = np.zeros_like(g); roi[50:H - 50, 70:W - 70] = 1
    best = None
    for t in range(30, 64, 4):
        m = (((g < t) & (g > 22) & (roi > 0)) * 255).astype(np.uint8)
        m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_CLOSE, _K9), cv2.MORPH_OPEN, _K5)
        b = _best_blob(m)
        if b and (best is None or b[0] > best[0]): best = b
    return cv2.fitEllipse(best[1]) if best else None

class PupilTracker:
    def __init__(self): self.last = None; self.lost = 0
    def update(self, img):
        """img: 400x640 uint8 -> ((cx, cy), (major, minor), angle) or None"""
        if self.last is not None:
            (cx, cy), (ma, mi), _ = self.last
            half = max(60, int(1.1 * max(ma, mi)))
            x0, y0 = max(0, int(cx) - half), max(0, int(cy) - half)
            x1, y1 = min(W, int(cx) + half), min(H, int(cy) + half)
            win = cv2.GaussianBlur(img[y0:y1, x0:x1], (5, 5), 0)
            p2, p50 = np.percentile(win[::2, ::2], (2, 50))
            thr = p2 + 0.45 * (p50 - p2)
            m = ((win < thr) * 255).astype(np.uint8)
            m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_CLOSE, _K9), cv2.MORPH_OPEN, _K5)
            b = _best_blob(m)
            if b:
                (ex, ey), axes, ang = cv2.fitEllipse(b[1])
                self.last = ((ex + x0, ey + y0), axes, ang); self.lost = 0
                return self.last
            self.lost += 1
            if self.lost < 3: return None                     # short dropout (blink start): keep the window
        e = full_search(img); self.last = e
        return e

if __name__ == "__main__":
    data = np.fromfile(sys.argv[1], np.uint8); fs = W * 2 * H; n = len(data) // fs
    frames = data[:n * fs].reshape(n, H, W * 2)
    ref = {}
    for l in list(open(sys.argv[2]))[1:]:
        r = l.split(",")
        if r[3]: ref[(int(r[0]), int(r[1]))] = (float(r[3]), float(r[4]))
    trk = [PupilTracker(), PupilTracker()]; got = [0, 0]; lit = [0, 0]; diffs = [[], []]
    t0 = time.perf_counter()
    for k in range(n):
        for eye in (0, 1):
            img = frames[k][:, eye * W:(eye + 1) * W]
            if img[::7, ::7].mean() <= 40: continue           # LEDs-off phase
            lit[eye] += 1
            e = trk[eye].update(img)
            if e is None: continue
            got[eye] += 1
            if (k, eye) in ref:
                rx, ry = ref[(k, eye)]; diffs[eye].append(math.hypot(e[0][0] - rx, e[0][1] - ry))
    dt = time.perf_counter() - t0
    print(f"{n} frames in {dt:.1f} s = {n / dt:.0f} frames/s (both eyes)")
    for eye in (0, 1):
        d = sorted(diffs[eye])
        print(f"camera {'AB'[eye]}: pupil in {got[eye]}/{lit[eye]} lit frames; vs slow detector: median "
              f"{d[len(d) // 2]:.2f} px, 95 % {d[int(len(d) * 0.95)]:.2f} px, max {d[-1]:.1f} px (n={len(d)})")
