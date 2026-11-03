"""aero_ipdmeasure.py -- IPD from the eye cameras: centre-dot pupils at two lens positions + the headset's factory chart images.

Factory data (headset entry 0x8041, saved by aero_storage.py setup as ~/.config/monado/varjo-aero-eyecam-factory.zip):
a flat ChArUco chart 15 mm in front of the eyepieces, photographed by both eye cameras at several lens positions;
hotspot_2d = the lens axis in each camera image (the cameras move with the lenses). A pupil pixel maps onto the chart
(mm) with that image's homography; its x offset from the hotspot is the eye's offset from the lens axis.
Our pupils sit behind the chart plane, so the offsets are scaled by k, measured per user from a lens move between the
two centre-dot looks (example: 12.2 px/mm at the pupil vs 15 on the chart, k = 1.23):
    lens0 + k * d0 = lens1 + k * d1 = IPD,   d = offset_left - offset_right
Plus CORRECTION: the camera sees the pupil centre, comfort follows the line of sight -- a measured 60.4 mm
was preferred at 62.0 in a blind A/B. AERO_IPD_CORRECTION overrides (calibrated on one person so far).
Camera A (bytes 0-639 of each line) = left eye; our 640x400 frames are the factory 1280x800 frames binned 2x."""
import json, os, statistics, zipfile

import cv2
import numpy as np

import aero_pupils

FACTORY = os.path.expanduser("~/.config/monado/varjo-aero-eyecam-factory.zip")
CORRECTION = float(os.environ.get("AERO_IPD_CORRECTION", "1.6"))
LATENCY = 0.25                                               # v4l2-ctl start-up (as in aero_gazefit.py)
W, H = aero_pupils.W, aero_pupils.H


def load_factory(path=FACTORY):
    return json.loads(zipfile.ZipFile(path).read("gaze_charuco_data.json"))


def _homography(img, side):
    ip = np.array(img["image_points_" + side], float)
    op = np.array(img["object_points_" + side], float)[:, :2]
    if side == "right":
        ip[:, 0] -= 1280
    h, _ = cv2.findHomography(ip / 2, op, cv2.RANSAC, 2.0)
    return h


def _to_chart(h, p):
    v = h @ np.array([p[0], p[1], 1.0])
    return v[:2] / v[2]


class Geometry:
    """Image -> chart mapping per camera, relative to the lens axis (any factory image works: verified identical)."""

    def __init__(self, d):
        img = d["images"][len(d["images"]) // 2]
        self.h = {0: _homography(img, "left"), 1: _homography(img, "right")}
        hot = {0: (d["hotspot_2d_left"][0] / 2, d["hotspot_2d_left"][1] / 2),
               1: ((d["hotspot_2d_right"][0] - 1280) / 2, d["hotspot_2d_right"][1] / 2)}
        self.axis = {e: _to_chart(self.h[e], hot[e])[0] for e in (0, 1)}

    def offset(self, eye, pupil):
        """mm (chart x) of the pupil from its lens axis; left - right grows with the eyes' separation."""
        return _to_chart(self.h[eye], pupil)[0] - self.axis[eye]


def centre_pupils(stream_path, t_from, t_to):
    """Median pupil centre per eye over the lit frames recorded between t_from and t_to (CLOCK_MONOTONIC)."""
    st = json.load(open(stream_path + ".json"))
    frames = np.memmap(stream_path, np.uint8, "r")
    n = len(frames) // (W * 2 * H)
    frames = frames[:n * W * 2 * H].reshape(n, H, W * 2)
    k0 = max(0, int((t_from - st["t_stream_monotonic"] - LATENCY) * st["fps"]))
    k1 = min(n, int((t_to - st["t_stream_monotonic"] - LATENCY) * st["fps"]))
    pts = {0: [], 1: []}
    for k in range(k0, k1):
        for eye in (0, 1):
            img = np.ascontiguousarray(frames[k][:, eye * W:(eye + 1) * W])
            if not aero_pupils.is_lit(img):
                continue
            e, _ = aero_pupils.detect(img)
            if e is not None:
                pts[eye].append(e[0])
    out = {}
    for eye in (0, 1):
        if len(pts[eye]) < 50:
            raise ValueError(f"pupil not found often enough on camera {'AB'[eye]} ({len(pts[eye])} frames)")
        out[eye] = (statistics.median(p[0] for p in pts[eye]), statistics.median(p[1] for p in pts[eye]))
    return out


def ipd_two_positions(geo, p0, lens0, p1, lens1, correction=CORRECTION):
    """IPD from centre-dot pupils at lens separations lens0 and lens1 (mm). Raises ValueError if implausible."""
    d0 = geo.offset(0, p0[0]) - geo.offset(1, p0[1])
    d1 = geo.offset(0, p1[0]) - geo.offset(1, p1[1])
    raw0, raw1 = lens0 + d0, lens1 + d1
    if abs(d0 - d1) < 0.5:
        raise ValueError(f"the pupils hardly moved with the lenses ({d0:+.2f} / {d1:+.2f} mm) -- headset slipped?")
    k = (lens1 - lens0) / (d0 - d1)
    ipd = lens0 + k * d0
    result = {"ipd": round(float(ipd + correction), 1), "measured": round(float(ipd), 2), "k": round(float(k), 3),
              "raw": (round(float(raw0), 2), round(float(raw1), 2)), "correction": correction}
    if not 0.8 <= k <= 1.8 or abs(raw0 - raw1) > 2.5:
        raise ValueError(f"inconsistent measurement {result} -- headset moved during the measurement?")
    return result


def lens_range():
    """(min, max) lens separation in mm from the headset's own table (0x8040)."""
    try:
        pts = json.load(open(os.path.expanduser("~/.config/monado/varjo-aero-ipd-calibration.json")))["ipd_calibration"]
        mm = [p["ipd"] for p in pts]
        return min(mm), max(mm)
    except (OSError, KeyError, ValueError):
        return 58.5, 72.0


def current_ipd_conf():
    """AERO_IPD_MM from varjo-aero.conf, or None."""
    try:
        for raw in open(os.path.expanduser("~/.config/monado/varjo-aero.conf")):
            line = raw.split("#", 1)[0].strip()
            if line.startswith("AERO_IPD_MM="):
                return float(line.split("=", 1)[1])
    except (OSError, ValueError):
        pass
    return None
