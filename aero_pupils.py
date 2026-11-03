#!/usr/bin/env python3
"""aero_pupils.py STREAM.yuyv [OUT_PREFIX] -- pupil + glint detection on an aero_eyecam.py recording (offline).

Frames: 640x400 YUYV, each 1280-byte line = camera A (bytes 0-639) | camera B (640-1279), 8-bit IR images.
Per lit frame and eye (dark frames = LEDs-off phase of the glint-LED pattern, skipped):
  pupil  = darkest compact blob: blur, threshold at (darkest 1 % of the eye area + margin), open/close, then the
           contour with the best circularity in a plausible size range; cv2.fitEllipse -> centre, axes, angle;
  glints = small saturated spots (connected components, area 1-40 px) within 1.9 pupil radii (on the iris;
           wider let lid/tear-film reflections in on camera A).
Writes OUT_PREFIX.csv (frame, eye, lit, pupil cx cy major minor angle, n_glints, glints x:y;...) and
OUT_PREFIX_overlay.png (a few frames, pupil ellipse green, glints red) for checking by eye.
Runs in ~/.local/share/varjo-aero/venv (OpenCV)."""
import sys, math
import numpy as np, cv2

W, H = 640, 400
def detect(img):
    """-> (pupil ellipse or None, [glint (x, y)])"""
    g = cv2.GaussianBlur(img, (7, 7), 0)
    # Threshold sweep: the pupil (~36-39 here) is darker than the iris (~55) but not as dark as the lens border and
    # vignetting (~16), so keep pixels between 22 and t and take the roundest pupil-sized blob over all t.
    roi = np.zeros_like(g); roi[50:H - 50, 70:W - 70] = 1                # ignore the lens rim
    best = None
    for t in range(30, 64, 4):
        m = (((g < t) & (g > 22) & (roi > 0)) * 255).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))   # fill glints punched into the pupil
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        for c in cnts:
            a = cv2.contourArea(c)
            if not (600 < a < 20000) or len(c) < 20: continue
            p = cv2.arcLength(c, True); circ = 4 * math.pi * a / (p * p)
            if circ < 0.6: continue
            if best is None or circ > best[0]: best = (circ, c)
    if best is None: return None, []
    (cx, cy), (ma, mi), ang = cv2.fitEllipse(best[1])
    r = max(ma, mi) / 2
    _, bright = cv2.threshold(img, 230, 255, cv2.THRESH_BINARY)
    k, _, stats, cent = cv2.connectedComponentsWithStats(bright)
    gl = [(float(x), float(y)) for i, (x, y) in enumerate(cent[1:], 1)
          if 1 <= stats[i, cv2.CC_STAT_AREA] <= 40 and math.hypot(x - cx, y - cy) < 1.9 * r]   # on the iris
    return ((cx, cy), (ma, mi), ang), gl


def is_lit(img):
    return img[::7, ::7].mean() > 40


def main():
    path = sys.argv[1]; out = sys.argv[2] if len(sys.argv) > 2 else path.rsplit(".", 1)[0]
    data = np.fromfile(path, np.uint8); fs = W * 2 * H; n = len(data) // fs
    frames = data[:n * fs].reshape(n, H, W * 2)

    rows, keep, found = [], [], {0: 0, 1: 0}; lit_n = {0: 0, 1: 0}
    for k in range(n):
        for eye in (0, 1):
            img = frames[k][:, eye * W:(eye + 1) * W]
            lit = is_lit(img)
            if not lit:
                rows.append(f"{k},{eye},0,,,,,,0,"); continue
            lit_n[eye] += 1
            e, gl = detect(img)
            if e is None:
                rows.append(f"{k},{eye},1,,,,,,0,"); continue
            found[eye] += 1
            (cx, cy), (ma, mi), ang = e
            rows.append(f"{k},{eye},1,{cx:.2f},{cy:.2f},{max(ma, mi):.2f},{min(ma, mi):.2f},{ang:.1f},{len(gl)},"
                        + ";".join(f"{x:.1f}:{y:.1f}" for x, y in gl))
            if eye == 1 and len(keep) < 4 and k % 97 == 0: keep.append(k)
    open(out + ".csv", "w").write("frame,eye,lit,cx,cy,major,minor,angle,n_glints,glints\n" + "\n".join(rows) + "\n")
    print(f"{n} frames; lit A {lit_n[0]} B {lit_n[1]}; pupil found A {found[0]} B {found[1]} -> {out}.csv")

    tiles = []
    for k in keep or [n // 2]:
        row = []
        for eye in (0, 1):
            img = frames[k][:, eye * W:(eye + 1) * W]; col = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            e, gl = detect(img)
            if e is not None: cv2.ellipse(col, e, (0, 255, 0), 2)
            for x, y in gl: cv2.circle(col, (int(x), int(y)), 4, (0, 0, 255), 1)
            cv2.putText(col, f"frame {k} cam {'AB'[eye]} glints {len(gl)}", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
            row.append(col)
        tiles.append(np.hstack(row))
    cv2.imwrite(out + "_overlay.png", np.vstack(tiles))
    print(f"overlay -> {out}_overlay.png")


if __name__ == "__main__":
    main()
