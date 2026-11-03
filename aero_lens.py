#!/usr/bin/env python3
"""aero_lens.py IPD_MM [PORT] -- move the Aero's lenses to IPD_MM.

After every power-up the MCU homes the lens chambers and parks them at 22 steps (= 64.5 mm), so they have to be
moved to the user's IPD each time. Moves: first all the way to 0 (one approach direction -> no backlash, table "backlash" 0.6 steps), then up in chunks of <= 7 steps,
each waited for. Target steps from the headset's own factory table (flash 0x8040, ~/.config/monado/
varjo-aero-ipd-calibration.json), rounded to whole steps.
Commands: 0x30 read position (reply payload u16), 0x32 move by a signed byte of steps (reply when the move is done;
status 5 = motors not ready). Frame: [len u16][cmd][flags, 0x80 = reply][status u16 in replies][seq u16 LE][payload]."""
import glob, json, os, sys, time, serial

target_mm = float(sys.argv[1])
port = sys.argv[2] if len(sys.argv) > 2 else glob.glob("/dev/serial/by-id/usb-Varjo_Technologies_Varjo_Headset_*-if00")[0]
pts = sorted((p["steps"], p["ipd"]) for p in json.load(open(os.path.expanduser(
    "~/.config/monado/varjo-aero-ipd-calibration.json")))["ipd_calibration"])

def mm_to_steps(mm):                                  # inverse of the (monotonic) table, linear between points
    for (s0, m0), (s1, m1) in zip(pts, pts[1:]):
        if m0 <= mm <= m1: return round(s0 + (s1 - s0) * (mm - m0) / (m1 - m0))
    return pts[0][0] if mm < pts[0][1] else pts[-1][0]
def steps_to_mm(st):
    for (s0, m0), (s1, m1) in zip(pts, pts[1:]):
        if s0 <= st <= s1: return m0 + (m1 - m0) * (st - s0) / (s1 - s0)
    return pts[-1][1]

s = serial.Serial(port, timeout=0.1, write_timeout=1); s.reset_input_buffer(); seq = 0
def cmd(c, payload=b"", wait=1.0):
    global seq
    seq += 1; s.write((8 + len(payload)).to_bytes(2, "little") + bytes([c, 0, 0, 0]) + seq.to_bytes(2, "little") + payload)
    end = time.time() + wait
    while time.time() < end:
        b = s.read(2)
        if len(b) < 2: continue
        n = int.from_bytes(b, "little"); f = b + s.read(max(0, n - 2))
        if len(f) >= 8 and f[2] == c and f[3] & 0x80 and int.from_bytes(f[6:8], "little") == seq:
            return int.from_bytes(f[4:6], "little"), f[8:]
    return None, b""
def position():
    st, p = cmd(0x30)
    return int.from_bytes(p[:2], "little") if st == 0 and len(p) >= 2 else None
def move(steps):
    st, _ = cmd(0x32, steps.to_bytes(1, "little", signed=True), wait=4.0)
    if st != 0: sys.exit(f"aero_lens: move {steps:+d} failed (status {st})")

pos = None
for attempt in range(5):                                 # the MCU can be busy right after another host session
    pos = position()
    if pos is not None: break
    time.sleep(0.5)
goal = mm_to_steps(target_mm)
if pos is None: sys.exit("aero_lens: could not read the lens position")
print(f"lenses at {pos} steps ({steps_to_mm(pos):.1f} mm); target {target_mm} mm -> {goal} steps ({steps_to_mm(goal):.1f} mm)")
if pos != goal:
    if pos: move(-pos)                                # to 0 first (one approach direction)
    left = goal
    while left > 0:
        k = min(7, left); move(k); left -= k
    time.sleep(0.2)
pos = position(); s.close()
print(f"lenses now at {pos} steps ({steps_to_mm(pos):.1f} mm)" if pos is not None else "aero_lens: position read failed")
sys.exit(0 if pos == goal else 1)
