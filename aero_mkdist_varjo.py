#!/usr/bin/env python3
"""aero_mkdist_varjo.py ENTRY_1000 PRECOMPILED OUT [N] -- the Aero lens correction:
  * render model = the shipped Gen3 render model (PRECOMPILED, data/varjo-aero-precompiled.bin),
  * per eye/colour, combined with the headset's own factory map by shifting it in tangent space by
        offset = tan(flash_map(opct)) - tan(precompiled(opct))
    where flash_map = the headset's factory map (storage entry 0x1000) and opct = its optical centre
    (texture coords -1..1), i.e. the render model moved by the optical-centre difference,
  * maps are sampled at texture coords -1..1 (identity homography) and hold stereographic directions:
        tan = 2 s / (1 - |s|^2); the texture is mirrored on both axes vs the upright eye view (panel at 180 deg).
Output = AEROD1 table for the Monado patch."""
import sys, os, struct, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aero_lut import load
from scipy.interpolate import RegularGridInterpolator
flash_src, pre_src, out = sys.argv[1], sys.argv[2], sys.argv[3]; N = int(sys.argv[4]) if len(sys.argv) > 4 else 129

def load_pre(fn):
    b = open(fn, "rb").read(); off = 0; its = []
    while off + 8 <= len(b):
        h = struct.unpack_from("<I", b, off)[0]; tag = b[off + 4:off + 8].decode(); n = h & 0x7FFFFFFF
        its.append((tag, b[off + 8:off + 8 + n])); off += 8 + n
    kv = lambda p: dict((p[j:j + 4].decode(), struct.unpack_from("<I", p, j + 4)[0]) for j in range(0, len(p), 8))
    tx = kv(its[-1][1]); maps = {}
    for name in ("lcer", "lceg", "lceb", "rcer", "rceg", "rceb"):
        m = kv(its[tx[name]][1])
        maps[name] = np.frombuffer(its[m["data"]][1], "<f4").reshape(2, m["hgth"], m["wdth"]).astype(float)
    return maps

def sampler(A, B):
    h, w = A.shape; ia = RegularGridInterpolator((np.arange(h), np.arange(w)), A, bounds_error=False, fill_value=None)
    ib = RegularGridInterpolator((np.arange(h), np.arange(w)), B, bounds_error=False, fill_value=None)
    def tan_at(x, y):                                   # x, y = texture coords -1..1
        a = ia(((y + 1) / 2 * (h - 1), (x + 1) / 2 * (w - 1))); b = ib(((y + 1) / 2 * (h - 1), (x + 1) / 2 * (w - 1)))
        r2 = a * a + b * b; den = np.where(r2 < 0.98, 1 - r2, np.nan)
        return 2 * a / den, 2 * b / den
    return tan_at

flash = load(flash_src); pre = load_pre(pre_src)
g = np.linspace(0, 1, N); U, V = np.meshgrid(g, g)
X, Y = 1 - 2 * U, 1 - 2 * V                             # upright eye view -> texture coords (mirrored both axes)
fovs, tables = [], []
for pre_ in ("lce", "rce"):
    dirs = []
    for c in "rgb":
        name = pre_ + c; ox, oy = flash[name]["oc"]
        tf = sampler(flash[name]["A"], flash[name]["B"])(ox, oy)
        tp = sampler(*pre[name])(ox, oy)
        off = (float(tf[0] - tp[0]), float(tf[1] - tp[1]))
        tx, ty = sampler(*pre[name])(X, Y)
        dirs.append((tx + off[0], ty + off[1]))
        print(f"{name}: optical centre ({ox:.6f}, {oy:.6f}) offset ({off[0]:+.6f}, {off[1]:+.6f})")
    allx = np.concatenate([d[0][np.isfinite(d[0])] for d in dirs]); ally = np.concatenate([d[1][np.isfinite(d[1])] for d in dirs])
    tl, tr = np.percentile(allx, 1), np.percentile(allx, 99); tt, tb = np.percentile(ally, 1), np.percentile(ally, 99)
    fovs.append((tl, tr, -tt, -tb))
    for tx, ty in dirs:
        uo = (tx - tl) / (tr - tl); vo = (ty - tt) / (tb - tt)
        bad = ~np.isfinite(uo) | ~np.isfinite(vo); uo[bad] = -1; vo[bad] = -1
        tables.append(np.stack([uo, vo], -1).astype("<f4"))
    deg = lambda t: np.degrees(np.arctan(t))
    print(f"{pre_}: fov deg L{deg(tl):.1f} R{deg(tr):.1f} U{deg(-tt):.1f} D{deg(-tb):.1f}")
with open(out, "wb") as f:
    f.write(b"AEROD1\0\0"); f.write(struct.pack("<I", N))
    for fv in fovs: f.write(struct.pack("<4f", *fv))
    for t in tables: f.write(t.tobytes())
print("wrote", out, os.path.getsize(out))
