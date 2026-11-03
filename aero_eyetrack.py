#!/usr/bin/env python3
"""aero_eyetrack.py [SECONDS] [CALIBRATION.json] -- live eye tracking on the Aero: cameras + LEDs + pupil tracker
+ calibration -> gaze angles over UDP.

  1. opens the eye camera (/dev/v4l/by-id/...Eye_Tracking_Camera-video-index0; AERO_EYECAM overrides) (640x400 YUYV @ 200 fps, raw) and runs the camera setup through the UVC extension unit
     (the same commands as aero_eyecam.py, one at a time, plus the bridge right-channel re-apply);
  2. sends the four glint-LED patterns to the main MCU with the standard spacing (1+2, then 3+4 at +2.7 s);
  3. per lit frame and eye: aero_pupil_fast.PupilTracker -> pupil centre -> calibration (affine, aero_gazefit.py:
     [cx, cy, 1] @ C = [gaze_x_deg, gaze_y_deg]; +x right, +y up, head-locked view angles);
  4. sends one JSON datagram per frame to 127.0.0.1:5005:
       {"t": monotonic, "frame": k, "A": [x, y] or null, "B": [x, y] or null, "gaze": [x, y] or null}
     ("gaze" = mean of the eyes that have a pupil). Prints a 2 Hz summary.
  4b. gaze filter for the published gaze (aero_gazefilter.py): AERO_GAZE_FILTER=varjo (foveation filter: ~16 ms
     velocity prediction + 0.5 deg fixation stabiliser; DEFAULT), twospeed (the earlier two-speed smoother) or off; AERO_GAZE_SMOOTH=0 = off. Environment wins; otherwise read from
     ~/.config/monado/varjo-aero.conf and RE-READ every second, so the filter can be switched while a game runs.
  5. publishes the newest gaze to /dev/shm/aero-gaze for any process (Monado's client library reads it for the
     gaze-driven quad-view inset, AERO_GAZE_INSET=1): struct <d f f I I> = t (CLOCK_MONOTONIC s), gaze x, gaze y
     (deg, +x right, +y up), valid (0/1), seq (incremented per write, odd while writing).
Re-centre (a centre dot at every session start; --no-recenter turns it off): from 1.5 to 5.0 s after
start the user looks at the centre; each eye's median gaze there becomes its offset, subtracted afterwards. It
corrects for the headset sitting differently than during calibration (pupil-only mapping: a shift or tilt on the
face moves the pupils in the images; re-wearing can put one camera's gaze 10 deg off vertically).
Default calibration ~/.config/monado/varjo-aero-gaze-$USER.json (per user); SECONDS 0 = run until Ctrl+C.
Runs in ~/.local/share/varjo-aero/venv (OpenCV, pyserial)."""
import ctypes, fcntl, glob, json, os, socket, struct, sys, time
# One thread for the math libraries (set before numpy/cv2 load): their default pools spin while idle -- OpenBLAS is
# loaded twice (numpy + scipy wheels) and burned ~7 of 24 threads during VR, costing the game frame rate. The per-frame work is a few small arrays; one thread keeps up.
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
import numpy as np, cv2, serial
cv2.setNumThreads(1)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aero_pupil_fast import PupilTracker
from aero_gazefilter import GazeFilter
import aero_data

RECENTER = "--no-recenter" not in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith("--")]
secs = float(args[0]) if args else 0
calf = args[1] if len(args) > 1 else os.path.expanduser(f"~/.config/monado/varjo-aero-gaze-{os.environ.get('USER', 'user')}.json")
cal = json.load(open(calf)); C = {0: np.array(cal["A_pupil"]), 1: np.array(cal["B_pupil"])}
DEV = os.environ.get("AERO_EYECAM", "/dev/v4l/by-id/usb-Varjo_Technologies_Varjo_Eye_Tracking_Camera-video-index0"); W, H = 640, 400
UVCIOC_CTRL_QUERY = 0xC0107521

# --- camera stream (raw YUYV: 400x640x2 bytes = A|B side by side as 8-bit images) ---
cap = cv2.VideoCapture(DEV, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV")); cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H); cap.set(cv2.CAP_PROP_FPS, 200); cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
if not cap.isOpened(): sys.exit("aero_eyetrack: cannot open " + DEV)
for _ in range(20): cap.read()                               # start streaming before the setup

# --- camera setup over the extension unit, one command at a time ---
fd = os.open(DEV, os.O_RDWR)
def xu(query, data):
    buf = ctypes.create_string_buffer(bytes(data), 384)
    fcntl.ioctl(fd, UVCIOC_CTRL_QUERY, bytearray(struct.pack("BBBxH2xQ", 4, 2, query, 384, ctypes.addressof(buf))))
    return buf.raw
def run_cmd(c, tries=100):
    xu(0x01, c)
    for _ in range(tries):
        r = xu(0x81, b"\0" * 384); time.sleep(0.0015)
        if r[0] == 0x56 and r[1:10] == c[:9]: return r
init = aero_data.eyecam_init()                               # camera setup commands (shipped in data/)
ok = sum(run_cmd(c) is not None for c in init)
for addr in (0x80181033, 0x80181833):                        # bridge per-eye control: re-apply (right channel)
    for _ in range(5):
        r = run_cmd(bytes([0x51, 0xa2, 0, 4, 1]) + addr.to_bytes(4, "big") + bytes([0x90, 1, 0, 1]))
        if r is not None and r[17] == 0x08: break
        run_cmd(bytes([0x50, 0xa2, 0, 4, 1]) + addr.to_bytes(4, "big") + bytes([0x90, 1, 0, 1, 0, 0, 0, 0x08]))
os.close(fd)
print(f"camera setup: {ok} commands done", flush=True)

# --- glint-LED patterns, with their standard spacing (sent from the frame loop) ---
glints = aero_data.glint_frames()
mcu = serial.Serial(glob.glob("/dev/serial/by-id/usb-Varjo_Technologies_Varjo_Headset_*-if00")[0], timeout=0, write_timeout=1)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); dst = ("127.0.0.1", 5005)
SHM = "/dev/shm/aero-gaze"; shm_fd = os.open(SHM, os.O_RDWR | os.O_CREAT, 0o644); os.ftruncate(shm_fd, 24); shm_seq = 0
CONF = os.path.expanduser("~/.config/monado/varjo-aero.conf")
def filter_mode():
    if os.environ.get("AERO_GAZE_SMOOTH") == "0": return "off"
    if os.environ.get("AERO_GAZE_FILTER"): return os.environ["AERO_GAZE_FILTER"]
    try:
        with open(CONF) as fh:
            for l in fh:
                k, _, v = l.split("#")[0].strip().partition("=")
                if k.strip() == "AERO_GAZE_FILTER": return v.strip()
    except OSError: pass
    return "varjo"
gf = GazeFilter(filter_mode()); conf_mtime = None; conf_check = 0.0
print(f"gaze filter: {gf.mode}", flush=True)
def check_filter_mode(now):
    """re-read the filter choice once a second when the settings file changed"""
    global conf_mtime, conf_check
    if now - conf_check < 1.0: return
    conf_check = now
    try: m = os.stat(CONF).st_mtime
    except OSError: m = None
    if m == conf_mtime: return
    conf_mtime = m; old = gf.mode; gf.set_mode(filter_mode())
    if gf.mode != old: print(f"gaze filter: {old} -> {gf.mode}", flush=True)
def publish(t, g):
    global shm_seq
    shm_seq += 2                                             # even = stable; readers retry on a changed seq
    os.pwrite(shm_fd, struct.pack("<dffII", t, g[0] if g else 0.0, g[1] if g else 0.0, 1 if g else 0, shm_seq), 0)
trk = {0: PupilTracker(), 1: PupilTracker()}
t0 = time.monotonic(); k = 0; last_print = t0; gl_i = 0
def new_stats(): return {"frames": 0, "A": 0, "B": 0, "lit": [0, 0], "peak": [0.0, 0.0]}   # lit frames / peak mean per camera
stats = new_stats()
offset = {0: np.zeros(2), 1: np.zeros(2)}; rc = {0: [], 1: []}; rc_done = not RECENTER
try:
    while secs == 0 or time.monotonic() - t0 < secs:
        while gl_i < len(glints) and time.monotonic() - t0 >= glints[gl_i][0]:
            mcu.write(glints[gl_i][1]); gl_i += 1
        okf, f = cap.read()
        if not okf: continue
        fr = f.reshape(H, W * 2); k += 1; stats["frames"] += 1
        out = {"t": time.monotonic(), "frame": k, "A": None, "B": None, "gaze": None}
        for eye in (0, 1):
            img = fr[:, eye * W:(eye + 1) * W]
            m = float(img[::7, ::7].mean()); stats["peak"][eye] = max(stats["peak"][eye], m)
            if m <= 40: continue                             # LEDs-off phase of the pattern
            stats["lit"][eye] += 1
            e = trk[eye].update(img)
            if e is None: continue
            g = np.array([e[0][0], e[0][1], 1.0]) @ C[eye]
            if not rc_done and 1.5 <= time.monotonic() - t0 <= 5.0: rc[eye].append(g)
            g = g - offset[eye]
            out["AB"[eye]] = [round(float(g[0]), 2), round(float(g[1]), 2)]; stats["AB"[eye]] += 1
        if not rc_done and time.monotonic() - t0 > 5.0:
            for eye in (0, 1):
                if len(rc[eye]) >= 20: offset[eye] = np.median(np.array(rc[eye]), axis=0)
            print(f"re-centre: offset A {offset[0].round(2).tolist()} deg ({len(rc[0])} samples), "
                  f"B {offset[1].round(2).tolist()} deg ({len(rc[1])} samples)", flush=True)
            rc_done = True
        both = [out[x] for x in "AB" if out[x]]
        if both: out["gaze"] = [round(sum(v[0] for v in both) / len(both), 2), round(sum(v[1] for v in both) / len(both), 2)]
        sock.sendto(json.dumps(out).encode(), dst)
        check_filter_mode(out["t"])
        publish(out["t"], gf(out["t"], out["gaze"], "".join(e for e in "AB" if out[e])))
        now = time.monotonic()
        if now - last_print >= 0.5:
            n = max(stats["frames"], 1)
            print(f"{now - t0:5.1f}s {stats['frames'] / (now - last_print):5.0f} fps  A {out['A']}  B {out['B']}  gaze {out['gaze']}"
                  f"  lit {100 * stats['lit'][0] // n}/{100 * stats['lit'][1] // n}%"
                  f" peak {stats['peak'][0]:.0f}/{stats['peak'][1]:.0f}", flush=True)
            last_print = now; stats = new_stats()
except KeyboardInterrupt:
    pass
cap.release(); mcu.close()
