#!/usr/bin/env python3
"""xr_ipdcalib.py -- the side-button sequence: measure the IPD, move the lenses to it, then the
eye-tracking calibration dots. One OpenXR session, so the headset never goes black in between. ~40 s:

  1. "Look at the dot": centre dot (3 s) -> lenses move 2.4 mm -> centre dot (3 s), cameras recording
     (aero_ipdmeasure.py)
  2. IPD computed (dot stays), lenses moved to it, AERO_IPD_MM saved in varjo-aero.conf
  3. ding + "Follow the dot", then the calibration dots: centre, the four corners diagonally, centre (xr_calib.py's
     schedule), recorded (the prompt announces the second part, which is otherwise easy to miss)
  4. "Calculating..." (the session stays open), pupils + per-eye fit -> ~/.config/monado/varjo-aero-gaze-$USER.json,
     saved only if both eyes fit within MAX_FIT_DEG on average AND better than the saved one (its errors are stored
     with it as fit_deg); the result is shown as text with a sound before the session ends
A failed IPD measurement (headset slipped, eyes closed) leaves the IPD as it was and still runs the calibration.
Sounds on the default output (the headset during VR): start, IPD set, done / error. Needs the main-MCU port and the
eye cameras to itself: the companion stops its display watch and eye tracker first. Log on stdout."""
import ctypes, glob, json, math, os, re, shutil, subprocess, sys, threading, time

import glfw
import numpy as np
import xr
from PIL import Image, ImageDraw, ImageFont
from OpenGL import GL
from xr.utils.gl import ContextObject
from xr.utils.gl.glfw_util import GLFWOffscreenContextProvider

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aero_ipdmeasure  # noqa: E402

E = os.path.expanduser(os.environ.get("AERO_EYECAM_DIR", "~/.local/state/varjo-aero/eyecam"))
CONF = os.path.expanduser("~/.config/monado/varjo-aero.conf")
GAZE_CAL = os.environ.get("AERO_GAZE_CAL") or os.path.expanduser(
    f"~/.config/monado/varjo-aero-gaze-{os.environ.get('USER', 'user')}.json")
MCU = (glob.glob("/dev/serial/by-id/usb-Varjo_Technologies_Varjo_Headset_*-if00") or [""])[0]
LEAD, LOOK, ANG, DWELL, SWEEP_MM = 3.0, 3.0, 12.0, 3.0, 2.4
TARGETS = [(0, 0), (-ANG, ANG), (ANG, -ANG), (ANG, ANG), (-ANG, -ANG), (0, 0)]   # as xr_calib.py
SOUNDS = "/usr/share/sounds/freedesktop/stereo/"
MAX_FIT_DEG = 4.0                                            # mean leave-one-out error per eye (typical 1.5-2.5)
FONT = next((f for f in ("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",                  # Arch
                         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",      # Debian/Ubuntu
                         "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf")    # Fedora
             if os.path.exists(f)), None)                    # None = PIL's built-in font
os.makedirs(E, exist_ok=True)

state = {"target": None, "text": None, "done": False}


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def sound(name):
    if os.path.exists(SOUNDS + name) and shutil.which("paplay"):
        subprocess.Popen(["paplay", SOUNDS + name], stderr=subprocess.DEVNULL)


def camera(seconds):
    """Start an eye-camera recording with the glint-LED patterns; returns (process, stream path)."""
    for old in glob.glob(f"{E}/stream_*.yuyv"):                # left over by an earlier failed run (several GB each)
        os.remove(old)
    before = set(glob.glob(f"{E}/stream_*.yuyv"))
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "aero_eyecam.py"), "--glint", str(int(seconds) + 1)],
                         stdout=open(f"{E}/ipdcalib_eyecam.log", "a"), stderr=subprocess.STDOUT, cwd=HERE,
                         env=dict(os.environ, AERO_EYECAM_DIR=E))
    for _ in range(50):                                      # the recording file appears within a second
        new = set(glob.glob(f"{E}/stream_*.yuyv")) - before
        if new:
            return p, new.pop()
        time.sleep(0.1)
    raise RuntimeError("eye cameras did not start")


def lenses(mm):
    """Move the lenses to mm (aero_lens.py); returns (separation before, separation after) in mm."""
    r = subprocess.run([sys.executable, os.path.join(HERE, "aero_lens.py"), f"{mm:.1f}", MCU],
                       capture_output=True, text=True, timeout=40, check=False)
    got = re.findall(r"\(([\d.]+) mm\)", r.stdout)
    if r.returncode != 0 or len(got) < 2:
        raise RuntimeError(f"lens move failed: {r.stdout.strip()} {r.stderr.strip()[-200:]}")
    return float(got[0]), float(got[-1])


def save_ipd(ipd):
    lines = open(CONF).read().splitlines() if os.path.exists(CONF) else []
    lines = [ln for ln in lines if not re.match(r"\s*AERO_IPD_MM\s*=", ln)] + [f"AERO_IPD_MM={ipd:.1f}"]
    with open(CONF + ".tmp", "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(CONF + ".tmp", CONF)


def look(ax, ay, seconds):
    """Show the dot at (ax, ay) deg; returns the steady window (eyes landed, before the next move)."""
    state["target"] = (ax, ay)
    t = time.monotonic()
    time.sleep(seconds)
    return t + 0.8, t + seconds - 0.3


def finish(text, snd, seconds=3.0):
    """Show the result in the headset with a sound, then let the session end."""
    state["target"], state["text"] = None, text
    sound(snd)
    time.sleep(seconds)


def sequence():
    try:
        sound("bell.oga")
        state["text"] = "Look at the dot"
        lo, hi = aero_ipdmeasure.lens_range()
        # 1. IPD: centre dot at two lens separations, cameras recording throughout
        cam, stream = camera(LEAD + 2 * LOOK + 6)
        time.sleep(LEAD)
        w0 = look(0, 0, LOOK)
        start = aero_ipdmeasure.current_ipd_conf() or 63.0
        second = start + SWEEP_MM if start + SWEEP_MM <= hi - 0.2 else start - SWEEP_MM
        lens0, lens1 = lenses(second)
        w1 = look(0, 0, LOOK)
        cam.wait(timeout=30)
        try:
            geo = aero_ipdmeasure.Geometry(aero_ipdmeasure.load_factory())
            p0 = aero_ipdmeasure.centre_pupils(stream, *w0)
            p1 = aero_ipdmeasure.centre_pupils(stream, *w1)
            res = aero_ipdmeasure.ipd_two_positions(geo, p0, lens0, p1, lens1)
            ipd = min(max(res["ipd"], lo), hi)
            log(f"IPD measured {res['measured']} mm + {res['correction']} -> {ipd} mm (k {res['k']}, raw {res['raw']})")
            json.dump(res, open(f"{E}/ipd_{time.strftime('%Y%m%d-%H%M%S')}.json", "w"), indent=1)
            lenses(ipd)
            save_ipd(ipd)
            sound("message.oga")
        except (ValueError, OSError, KeyError) as e:
            log(f"IPD measurement failed, keeping {start} mm: {e}")
            lenses(start)
            sound("dialog-warning.oga")
        os.remove(stream)
        # 2. calibration dots at the final lens position (the cameras move with the lenses)
        state["text"] = "Follow the dot"
        sound("bell.oga")
        time.sleep(2.5)
        state["target"], state["text"] = None, None
        cam, stream = camera(LEAD + DWELL * len(TARGETS) + 1)
        t0 = time.monotonic()
        sched = [{"t": t0 + LEAD + i * DWELL, "dwell": DWELL, "ax": ax, "ay": ay} for i, (ax, ay) in enumerate(TARGETS)]
        calib = f"{E}/calib_{time.strftime('%H%M%S')}.json"
        json.dump({"angle": ANG, "targets": sched}, open(calib, "w"), indent=1)
        time.sleep(LEAD)
        for ax, ay in TARGETS:
            look(ax, ay, DWELL)
        state["target"], state["text"] = None, "Calculating..."
        cam.wait(timeout=30)
        log("dots done, fitting...")
        subprocess.run([sys.executable, os.path.join(HERE, "aero_pupils.py"), stream, stream[:-5]],
                       capture_output=True, check=True)
        r = subprocess.run([sys.executable, os.path.join(HERE, "aero_gazefit.py"), calib, stream + ".json",
                            stream[:-5] + ".csv"], capture_output=True, text=True, check=False)
        log(r.stdout.strip())
        fit = json.load(open(calib[:-5] + "_fit.json"))
        if "A_pupil" not in fit or "B_pupil" not in fit:
            raise RuntimeError("calibration fit failed (pupils not found on enough targets)")
        errs = [float(m) for m in re.findall(r"pupil:.*-> mean ([\d.]+) deg", r.stdout)]
        os.remove(stream)
        try:
            prev = json.load(open(GAZE_CAL)).get("fit_deg")
        except (OSError, ValueError):
            prev = None
        if len(errs) != 2 or max(errs) > MAX_FIT_DEG:
            log(f"calibration too inaccurate ({errs} deg, limit {MAX_FIT_DEG}) -- kept the previous one")
            finish("Not accurate - press button again", "dialog-error.oga")
        elif prev and max(errs) >= max(prev):
            log(f"new calibration {errs} deg not better than the saved {prev} deg -- kept the saved one")
            finish("Done (kept the better one)", "complete.oga")
        else:
            fit["fit_deg"] = errs
            json.dump(fit, open(GAZE_CAL, "w"), indent=1)
            log(f"saved {GAZE_CAL} ({errs} deg, before {prev})")
            finish("Done", "complete.oga")
    except Exception as e:  # noqa: BLE001 -- any failure: tell the user, end the session
        log(f"FAILED: {e}")
        finish("Failed - press button again", "dialog-error.oga")
    finally:
        state["done"] = True


glfw.init_hint(glfw.PLATFORM, glfw.PLATFORM_X11)              # same GLX binding fix as xr_calib.py
import xr.utils.gl as _xgl, xr.utils.gl.context_object as _xco  # noqa: E401,E402


def _glx_binding(cp):
    from OpenGL import GLX
    b = _xgl.GLXGraphicsBinding(cp)
    gb = b.graphics_binding
    dpy, ctx = GLX.glXGetCurrentDisplay(), GLX.glXGetCurrentContext()
    fbid = ctypes.c_int(0)
    GLX.glXQueryContext(dpy, ctx, GLX.GLX_FBCONFIG_ID, ctypes.byref(fbid))
    n = ctypes.c_int(0)
    attrs = (ctypes.c_int * 3)(GLX.GLX_FBCONFIG_ID, fbid.value, 0)
    cfgs = GLX.glXChooseFBConfig(dpy, 0, attrs, ctypes.byref(n))
    if n.value < 1:
        raise RuntimeError("no GLX FBConfig for the current context")
    gb.glx_fbconfig = cfgs[0]
    vis = GLX.glXGetVisualFromFBConfig(dpy, cfgs[0])
    if vis:
        gb.visualid = vis.contents.visualid
    return b


_xgl.create_graphics_binding = _glx_binding
if hasattr(_xco, "create_graphics_binding"):
    _xco.create_graphics_binding = _xgl.create_graphics_binding


_text_cache = {}


def text_runs(text):
    """Bitmap of the text as per-row runs of lit pixels: (width, height, [(row, x0, x1)])."""
    if text not in _text_cache:
        try:
            font = ImageFont.truetype(FONT, 28)
        except (OSError, TypeError, AttributeError):         # no DejaVu font installed
            font = ImageFont.load_default()
        x0, y0, x1, y1 = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), text, font=font)
        img = Image.new("L", (x1 - x0 + 2, y1 - y0 + 2))
        ImageDraw.Draw(img).text((1 - x0, 1 - y0), text, fill=255, font=font)
        a = np.array(img) > 110
        runs = []
        for row in range(a.shape[0]):
            cols = np.flatnonzero(np.diff(np.concatenate(([0], a[row].astype(np.int8), [0]))))
            runs += [(row, int(cols[k]), int(cols[k + 1])) for k in range(0, len(cols), 2)]
        _text_cache[text] = (a.shape[1], a.shape[0], runs)
    return _text_cache[text]


def draw_text(w, h, tl, tr, tu, td, text, ay_deg=4.0, height_deg=1.4):
    """Head-locked text centred above the dot, height_deg tall, drawn with scissored clears like the dot."""
    tw, th, runs = text_runs(text)
    px_per_tan = w / (tr - tl)
    sc = max(1, int(math.tan(math.radians(height_deg)) * px_per_tan / th))
    cx = (0 - tl) / (tr - tl) * w
    top = (math.tan(math.radians(ay_deg)) - td) / (tu - td) * h + th * sc
    left = cx - tw * sc / 2
    GL.glEnable(GL.GL_SCISSOR_TEST)
    GL.glClearColor(0.55, 1.0, 0.55, 1)
    for row, x0, x1 in runs:
        GL.glScissor(int(left + x0 * sc), int(top - (row + 1) * sc), (x1 - x0) * sc, sc)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
    GL.glDisable(GL.GL_SCISSOR_TEST)


def draw_dot(ctx, i, view):
    f = view.fov
    w, h = ctx.swapchains[i].width, ctx.swapchains[i].height
    GL.glDisable(GL.GL_SCISSOR_TEST)
    GL.glClearColor(0, 0, 0, 1)
    GL.glClear(GL.GL_COLOR_BUFFER_BIT)
    tl, tr, tu, td = (math.tan(f.angle_left), math.tan(f.angle_right), math.tan(f.angle_up), math.tan(f.angle_down))
    if state["text"]:
        draw_text(w, h, tl, tr, tu, td, state["text"])
    if state["target"] is None:
        return
    ax, ay = (math.radians(a) for a in state["target"])
    x = (math.tan(ax) - tl) / (tr - tl) * w
    y = (math.tan(ay) - td) / (tu - td) * h
    r = 0.45 * w * 0.003                                     # ~0.33 deg dot (small, for precise fixation)
    GL.glEnable(GL.GL_SCISSOR_TEST)
    GL.glClearColor(0.55, 1.0, 0.55, 1)                       # light-green dot (same colour as xr_calib.py)
    for dy in range(-int(r), int(r) + 1):
        hw = math.sqrt(max(0.0, r * r - dy * dy))
        GL.glScissor(int(x - hw), int(y + dy), max(1, int(2 * hw)), 1)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
    GL.glDisable(GL.GL_SCISSOR_TEST)


def main():
    if not MCU:
        sys.exit("xr_ipdcalib: headset USB not found")
    with ContextObject(
        context_provider=GLFWOffscreenContextProvider(gl_version=(4, 6)),
        instance_create_info=xr.InstanceCreateInfo(enabled_extension_names=[xr.KHR_OPENGL_ENABLE_EXTENSION_NAME]),
        reference_space_create_info=xr.ReferenceSpaceCreateInfo(reference_space_type=xr.ReferenceSpaceType.LOCAL),
    ) as ctx:
        worker = threading.Thread(target=sequence, daemon=True)
        worker.start()
        for frame_state in ctx.frame_loop():
            for i, view in enumerate(ctx.view_loop(frame_state)):
                draw_dot(ctx, i, view)
            if state["done"]:
                break
    worker.join()


if __name__ == "__main__":
    main()
