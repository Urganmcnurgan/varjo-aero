#!/usr/bin/env python3
"""xr_calib.py [ANGLE_DEG] [DWELL_S] -- gaze calibration session, the same dot sequence as Varjo Base: a dot in the
centre, then the four corners in a diagonal sequence, then the centre again, while the eye cameras record.

The dot is head-locked at infinity: drawn in each eye at the same view angle, positioned from that view's own FOV
(tangent space), so it fuses. Black background, round light-green dot (easier to focus on than a box). Starts the eye
cameras itself (aero_eyecam.py --glint, glint-LED patterns on) so camera frames and targets share CLOCK_MONOTONIC:
writes $AERO_EYECAM_DIR (default ~/.local/state/varjo-aero/eyecam)/calib_<time>.json with the target schedule; the recording has its own .json with the stream
start time. Default: +-12 deg corners, 3 s per target. Chime (monitor speakers) at the end.
Normally run by aero-calibrate (or the side button via xr_ipdcalib.py) with ~/.local/share/varjo-aero/venv/bin/python."""
import sys, time, math, ctypes, json, os, subprocess
import glfw, xr
from OpenGL import GL
from xr.utils.gl import ContextObject
from xr.utils.gl.glfw_util import GLFWOffscreenContextProvider

ANG = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
DWELL = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
LEAD = 3.0                                                    # cameras + LEDs settle before the first target
TARGETS = [(0, 0), (-ANG, ANG), (ANG, -ANG), (ANG, ANG), (-ANG, -ANG), (0, 0)]   # centre, TL, BR, TR, BL, centre
E = os.path.expanduser(os.environ.get("AERO_EYECAM_DIR", "~/.local/state/varjo-aero/eyecam")); os.makedirs(E, exist_ok=True)

glfw.init_hint(glfw.PLATFORM, glfw.PLATFORM_X11)              # same GLX binding fix as xr_testpattern.py
import xr.utils.gl as _xgl, xr.utils.gl.context_object as _xco
def _glx_binding(cp):
    from OpenGL import GLX
    b = _xgl.GLXGraphicsBinding(cp); gb = b.graphics_binding
    dpy, ctx = GLX.glXGetCurrentDisplay(), GLX.glXGetCurrentContext()
    fbid = ctypes.c_int(0); GLX.glXQueryContext(dpy, ctx, GLX.GLX_FBCONFIG_ID, ctypes.byref(fbid))
    n = ctypes.c_int(0); attrs = (ctypes.c_int * 3)(GLX.GLX_FBCONFIG_ID, fbid.value, 0)
    cfgs = GLX.glXChooseFBConfig(dpy, 0, attrs, ctypes.byref(n))
    if n.value < 1: raise RuntimeError("no GLX FBConfig for the current context")
    gb.glx_fbconfig = cfgs[0]
    vis = GLX.glXGetVisualFromFBConfig(dpy, cfgs[0])
    if vis: gb.visualid = vis.contents.visualid
    return b
_xgl.create_graphics_binding = _glx_binding
if hasattr(_xco, "create_graphics_binding"): _xco.create_graphics_binding = _xgl.create_graphics_binding

def rect(x, y, w, h, c):
    GL.glScissor(int(x), int(y), max(1, int(w)), max(1, int(h))); GL.glClearColor(*c, 1); GL.glClear(GL.GL_COLOR_BUFFER_BIT)

total = LEAD + DWELL * len(TARGETS)
with ContextObject(
    context_provider=GLFWOffscreenContextProvider(gl_version=(4, 6)),
    instance_create_info=xr.InstanceCreateInfo(enabled_extension_names=[xr.KHR_OPENGL_ENABLE_EXTENSION_NAME]),
    reference_space_create_info=xr.ReferenceSpaceCreateInfo(reference_space_type=xr.ReferenceSpaceType.LOCAL),
) as ctx:
    here = os.path.dirname(os.path.abspath(__file__))
    cam = subprocess.Popen([sys.executable, os.path.join(here, "aero_eyecam.py"), "--glint", str(int(total + 2))],
                           stdout=open(f"{E}/calib_eyecam.log", "w"), stderr=subprocess.STDOUT, cwd=here)
    t0 = time.monotonic(); sched = [{"t": t0 + LEAD + i * DWELL, "dwell": DWELL, "ax": ax, "ay": ay}
                                    for i, (ax, ay) in enumerate(TARGETS)]
    tag = time.strftime("%H%M%S")
    json.dump({"angle": ANG, "targets": sched}, open(f"{E}/calib_{tag}.json", "w"), indent=1)
    print(f"calibration: {len(TARGETS)} targets x {DWELL} s at +-{ANG} deg -> {E}/calib_{tag}.json", flush=True)
    frames = drawn = 0
    for frame_state in ctx.frame_loop():
        frames += 1; got = False
        now = time.monotonic() - t0
        idx = int((now - LEAD) // DWELL) if now >= LEAD else -1
        for i, view in enumerate(ctx.view_loop(frame_state)):
            got = True
            f = view.fov; w, h = ctx.swapchains[i].width, ctx.swapchains[i].height
            GL.glDisable(GL.GL_SCISSOR_TEST); GL.glClearColor(0, 0, 0, 1); GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            if 0 <= idx < len(TARGETS):
                ax, ay = (math.radians(a) for a in TARGETS[idx])
                tl, tr, tu, td = (math.tan(f.angle_left), math.tan(f.angle_right), math.tan(f.angle_up), math.tan(f.angle_down))
                x = (math.tan(ax) - tl) / (tr - tl) * w; y = (math.tan(ay) - td) / (tu - td) * h
                s = w * 0.003                                 # ~0.33 deg dot (small, for precise fixation)
                GL.glEnable(GL.GL_SCISSOR_TEST)
                GL.glClearColor(0.55, 1.0, 0.55, 1)            # round light-green dot (a dot is easier to focus on)
                r = 0.45 * s
                for dy in range(-int(r), int(r) + 1):
                    hw = math.sqrt(max(0.0, r * r - dy * dy))
                    GL.glScissor(int(x - hw), int(y + dy), max(1, int(2 * hw)), 1); GL.glClear(GL.GL_COLOR_BUFFER_BIT)
                GL.glDisable(GL.GL_SCISSOR_TEST)
        drawn += got
        if now > total: break
print(f"frames {frames}, drawn {drawn}, skipped (no valid head pose / not rendering) {frames - drawn}", flush=True)
cam.wait(timeout=30)
try:                                                         # end chime on the default output (the headset during VR)
    subprocess.run(["paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"], stderr=subprocess.DEVNULL,
                   env=dict(os.environ, XDG_RUNTIME_DIR=os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")),
                   timeout=10, check=False)
except (OSError, subprocess.SubprocessError):
    pass
print("calibration session done", flush=True)
