#!/usr/bin/env python3
"""aero_lut.py -- load the Varjo Aero factory distortion maps (headset storage entry 0x1000) and invert them.

Each map (6: eye L/R x colour R/G/B): two 128x128 float planes (A then B) over a viewing-direction grid,
x index spanning tangent [htmn, htmx], y index spanning [vtmn, vtmx]; values = panel position (centred, ~+-0.5)."""
import struct, numpy as np
from scipy.interpolate import RegularGridInterpolator

def tlv(b):
    off = 0; out = []
    while off + 8 <= len(b):
        h = struct.unpack_from("<I", b, off)[0]; tag = b[off + 4:off + 8].decode(); n = h & 0x7FFFFFFF
        out.append((tag, b[off + 8:off + 8 + n])); off += 8 + n
    return out

def kv(p):
    d = {}
    for k in range(0, len(p), 8):
        key = p[k:k + 4].decode(); raw = p[k + 4:k + 8]
        u = struct.unpack("<I", raw)[0]
        d[key] = u if key in ("wdth", "hgth", "data", "opct", "lcer", "lceg", "lceb", "rcer", "rceg", "rceb", "vers", "off1", "off2", "off3") else struct.unpack("<f", raw)[0]
    return d

def load(path):
    items = tlv(open(path, "rb").read())
    txtr = kv(next(p for t, p in items if t == "TXTR"))
    maps = {}
    for name in ("lcer", "lceg", "lceb", "rcer", "rceg", "rceb"):
        m = kv(items[txtr[name]][1]); w, h = m["wdth"], m["hgth"]
        f = np.frombuffer(items[m["data"]][1], dtype="<f4").reshape(2, h, w)
        m["A"], m["B"] = f[0].astype(np.float64), f[1].astype(np.float64)
        m["oc"] = np.frombuffer(items[m["opct"]][1], dtype="<f4").astype(float)
        m["tx"] = np.linspace(m["htmn"], m["htmx"], w); m["ty"] = np.linspace(m["vtmn"], m["vtmx"], h)
        maps[name] = m
    return maps

def forward(m):
    """direction (tx, ty) -> panel (A, B), bilinear."""
    ia = RegularGridInterpolator((m["ty"], m["tx"]), m["A"], bounds_error=False, fill_value=None)
    ib = RegularGridInterpolator((m["ty"], m["tx"]), m["B"], bounds_error=False, fill_value=None)
    return lambda tx, ty: (ia((ty, tx)), ib((ty, tx)))

def invert(m, PA, PB, iters=30):
    """panel positions (PA, PB arrays) -> directions (tx, ty) by Newton iteration on the forward map."""
    fwd = forward(m)
    # start from a linear fit of the map
    TX, TY = np.meshgrid(m["tx"], m["ty"])
    ca = np.polyfit(m["A"].ravel(), TX.ravel(), 1); cb = np.polyfit(m["B"].ravel(), TY.ravel(), 1)
    tx = np.polyval(ca, PA); ty = np.polyval(cb, PB); e = 1e-4
    for _ in range(iters):
        a, b = fwd(tx, ty)
        ra, rb = a - PA, b - PB
        a_x, b_x = fwd(tx + e, ty); a_y, b_y = fwd(tx, ty + e)
        J11, J21 = (a_x - a) / e, (b_x - b) / e; J12, J22 = (a_y - a) / e, (b_y - b) / e
        det = J11 * J22 - J12 * J21; det = np.where(np.abs(det) < 1e-12, 1e-12, det)
        dx = (J22 * ra - J12 * rb) / det; dy = (-J21 * ra + J11 * rb) / det
        tx = tx - dx; ty = ty - dy
    a, b = fwd(tx, ty)
    return tx, ty, np.hypot(a - PA, b - PB)
