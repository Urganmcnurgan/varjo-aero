#!/usr/bin/env python3
"""aero_eyecam.py [--dry] [--glint] [SECONDS] -- first light for the Aero's eye cameras.

Start-up order:
  1. start streaming 640x400 YUYV @ 200 fps (the stream has to run BEFORE the sensor writes);
  2. send the camera setup commands (data/eyecam-init.txt) byte for byte: UVC extension unit 4, selector 2, 384-byte
     SET_CUR commands, each followed by GET_CUR polls of the reply. Format: cmd 0x50 write /
     0x51 read | target 0xa3 cam0, 0xa5 cam1 (I2C 0xc0, 2-byte register, 1 data byte), 0xa2 bridge (4-byte
     address) | ... | value at byte 16. The setup writes 8 registers per sensor (exposure 0x3500-02, gain 0x3509,
     flip/binning 0x3820-21, strobe 0x3927-28) and 2 bridge registers;
  3. --glint: send the first glint-LED patterns (main MCU cmd 0x43, data/glint-leds.txt) -- the IR
     illumination; on its own (no streaming cameras) the MCU's glint queue fills and answers error 2.
Then saves the raw stream and PNGs of the first and last frame, split into the two sensor images (each YUYV line
of 1280 bytes = two 640-pixel 8-bit images), and prints mean brightness per sensor.
--dry: print what would be sent, touch nothing. Recordings: ~/.local/state/varjo-aero/eyecam/ (AERO_EYECAM_DIR); camera setup + LED patterns: data/ (aero_data.py)."""
import fcntl, glob, os, struct, subprocess, sys, time, ctypes
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aero_data

DRY = "--dry" in sys.argv; GLINT = "--glint" in sys.argv
# --set CAM:REG=VAL[,...]: extra sensor writes after the setup, e.g. --set 1:3928=21,1:392a=e0
EXTRA = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--set=")]
args = [a for a in sys.argv[1:] if not a.startswith("--")]; secs = float(args[0]) if args else 4
E = os.path.expanduser(os.environ.get("AERO_EYECAM_DIR", "~/.local/state/varjo-aero/eyecam")); os.makedirs(E, exist_ok=True); DEV = os.environ.get("AERO_EYECAM", "/dev/v4l/by-id/usb-Varjo_Technologies_Varjo_Eye_Tracking_Camera-video-index0"); W, H, FPS = 640, 400, 200

xu_cmds = aero_data.eyecam_init()                          # camera setup commands (shipped in data/)

def glint_frames():                                          # first 0x43 frames to the MCU (shipped, data/)
    return aero_data.glint_frames()                          # (seconds after the first glint frame, bytes)

print(f"{len(xu_cmds)} camera setup commands:")
for c in xu_cmds:
    if c[0] == 0x50 and c[1] in (0xa3, 0xa5): print(f"  cam{0 if c[1] == 0xa3 else 1} reg {(c[7] << 8) | c[8]:#06x} = {c[16]:#04x}")
    else: print(f"  {c[:20].hex()}")
gl = glint_frames() if GLINT else []
if GLINT: print(f"{len(gl)} glint frames:", *[f"+{t:.2f}s {g.hex()}" for t, g in gl], sep="\n  ")
if DRY: sys.exit()

UVCIOC_CTRL_QUERY = 0xC0107521                               # _IOWR('u', 0x21, struct uvc_xu_control_query) (16 B)
def xu(fd, query, data):
    buf = ctypes.create_string_buffer(bytes(data), 384)
    q = struct.pack("BBBxH2xQ", 4, 2, query, 384, ctypes.addressof(buf))
    fcntl.ioctl(fd, UVCIOC_CTRL_QUERY, bytearray(q))
    return buf.raw

raw = f"{E}/stream_{time.strftime('%H%M%S')}.yuyv"
n = int(secs * FPS)
v = subprocess.Popen(["v4l2-ctl", "-d", DEV, f"--set-fmt-video=width={W},height={H},pixelformat=YUYV",
                      f"--set-parm={FPS}", "--stream-mmap", f"--stream-count={n}", f"--stream-to={raw}"],
                     stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
t_stream = time.monotonic()                                  # frame k ~ t_stream + latency + k / FPS
open(raw + ".json", "w").write(__import__("json").dumps({"t_stream_monotonic": t_stream, "fps": FPS, "w": W, "h": H}))
time.sleep(0.5)                                              # streaming first, then the setup
fd = os.open(DEV, os.O_RDWR)
def run_cmd(c, tries=100):
    """one command at a time: poll until the reply is '56' + an echo of THIS command (55 = busy)"""
    xu(fd, 0x01, c)
    for _ in range(tries):
        r = xu(fd, 0x81, b"\0" * 384); time.sleep(0.0015)
        if r[0] == 0x56 and r[1:10] == c[:9]: return r
    return None
for c in xu_cmds:
    r = run_cmd(c)
    if c[0] == 0x50: print(f"  sent {c[:3].hex()} reg {c[5:9].hex()} " + ("done" if r else "NO COMPLETION"), flush=True)
def read_reg(cam, reg):                                      # reply '56' + echo, value at byte 17
    r = run_cmd(bytes([0x51, 0xa3 if cam == 0 else 0xa5, 0xc0, 2, 1, 0, 0, reg >> 8, reg & 0xff, 0x90, 1, 0, 1]))
    return r[17] if r else None
for spec in ",".join(EXTRA).split(",") if EXTRA else []:
    cam, rv = spec.split(":"); reg, val = (int(x, 16) for x in rv.split("="))
    cmd = bytes([0x50, 0xa3 if cam == "0" else 0xa5, 0xc0, 2, 1, 0, 0, reg >> 8, reg & 0xff, 0x90, 1, 0, 1, 0, 0, 0, val])
    run_cmd(cmd)
    print(f"  extra write cam{cam} {reg:#06x} = {val:#04x} -> reads back {read_reg(int(cam), reg)}", flush=True)
def read_bridge(addr):                                       # bridge (a2) per-eye control, value at byte 17
    r = run_cmd(bytes([0x51, 0xa2, 0, 4, 1]) + addr.to_bytes(4, "big") + bytes([0x90, 1, 0, 1]))
    return r[17] if r else None
# Bridge per-eye control (0x80181033 left / 0x80181833 right, 0x0a -> 0x08): the first write to the RIGHT channel
# lands before that channel is ready right after stream start and is lost -> camera B never gets its LED light (dark).
# Re-apply and verify both after the init; then both cameras are lit.
for addr in (0x80181033, 0x80181833):
    for attempt in range(5):
        if read_bridge(addr) == 0x08: break
        run_cmd(bytes([0x50, 0xa2, 0, 4, 1]) + addr.to_bytes(4, "big") + bytes([0x90, 1, 0, 1, 0, 0, 0, 0x08]))
        time.sleep(0.05)
if "--watch-bridge" in sys.argv:                            # how long does a bridge write hold?
    t1 = time.time()
    while time.time() - t1 < 2:
        print(f"  +{time.time() - t1:4.2f}s bridge L {read_bridge(0x80181033)} R {read_bridge(0x80181833)}", flush=True)
        time.sleep(0.2)
for addr in (0x80181033, 0x80181833):
    bv = read_bridge(addr); print(f"  bridge {addr:#010x} = {bv:#04x}" if bv is not None else f"  bridge {addr:#010x}: no reply", flush=True)
for cam in (0, 1):
    v3006 = read_reg(cam, 0x3006)
    print(f"  cam{cam} reg 0x3006 = {v3006:#04x}" if v3006 is not None else f"  cam{cam} reg 0x3006: no reply", flush=True)
os.close(fd)
if GLINT:
    import serial
    port = glob.glob("/dev/serial/by-id/usb-Varjo_Technologies_Varjo_Headset_*-if00")[0]
    s = serial.Serial(port, timeout=0.3, write_timeout=1); s.reset_input_buffer()
    tg = time.time()
    for t, g in gl:                                          # timing from the data file (1+2 together, 3+4 ~2.7 s later)
        while time.time() - tg < t: time.sleep(0.005)
        s.write(g); s.flush(); r = s.read(8); print(f"  +{t:.2f}s glint {g[8:12].hex()}.. reply {r.hex()}", flush=True)
    s.close()
v.wait(timeout=secs + 10)
data = open(raw, "rb").read(); fsz = W * 2 * H; frames = len(data) // fsz
print(f"captured {frames} frames -> {raw}")
try:
    import numpy as np
    from PIL import Image
    for tag, k in (("first", 0), ("last", frames - 1)):
        fr = np.frombuffer(data[k * fsz:(k + 1) * fsz], np.uint8).reshape(H, W * 2)
        a, b = fr[:, :W], fr[:, W:]
        Image.fromarray(np.hstack([a, b])).save(f"{E}/{tag}.png")
        print(f"  {tag} frame: sensor A mean {a.mean():.1f} max {a.max()}, sensor B mean {b.mean():.1f} max {b.max()}  -> {E}/{tag}.png")
    lit = [((np.frombuffer(data[k * fsz:(k + 1) * fsz], np.uint8).reshape(H, W * 2)[::7, :W:7].mean() > 28),
            (np.frombuffer(data[k * fsz:(k + 1) * fsz], np.uint8).reshape(H, W * 2)[::7, W::7].mean() > 28)) for k in range(frames)]
    print(f"  lit frames: sensor A {sum(a for a, _ in lit)}/{frames}, sensor B {sum(b for _, b in lit)}/{frames}")
except ImportError as e:
    print("numpy/PIL missing for PNGs:", e)
