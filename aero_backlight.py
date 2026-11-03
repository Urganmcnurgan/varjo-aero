#!/usr/bin/env python3
"""aero_backlight.py PORT [LEVEL] [SECONDS]   (SECONDS 0 = send ONE frame and exit) -- feed the Varjo Aero's segmented backlight (Display Controller,
USB 2f9a:0235) with a uniform level.

Frame, 0x910 = 2320 bytes:
  header   01 00 10 09 00 00 00 00          (bytes 2-3 = total length, LE)
  crc32    of the 8 header bytes, LE        (standard CRC-32, poly 0xEDB88320)
  payload  2 eyes x 8 rows x 24 cols x 3 u16 LE  = 2304 bytes
  crc32    of the payload, LE
Value = level(0-100) * 81.9 (level 60 -> 4914). One frame per level change is enough (at most one every 5 ms);
with SECONDS > 0 the frame is resent once a second so a watchdog, if any, stays fed.
DRY RUN with PORT '-' prints the frame without opening anything.
AERO_BL_MASK=FILE: 1152 bytes (0/1 per channel, frame order) -- channels marked 0 stay dark. Default: the lens-shaped
mask shipped in data/ (aero_data.py; meant for level 60, the default); AERO_BL_MASK="" = all channels lit."""
import os, sys, time, zlib, struct, functools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aero_data
print = functools.partial(print, flush=True)   # logs survive a kill
port = sys.argv[1]; level = int(sys.argv[2]) if len(sys.argv) > 2 else 60; secs = float(sys.argv[3]) if len(sys.argv) > 3 else 60
assert 0 <= level <= 100
value = int(level * 81.9)
header = bytes([0x01, 0x00, 0x10, 0x09, 0, 0, 0, 0])
mask_file = os.environ.get("AERO_BL_MASK", aero_data.data_path("varjo-aero-backlight-mask.bin"))
mask = open(mask_file, "rb").read() if mask_file else b"\x01" * 1152
assert len(mask) == 1152, f"mask must be 1152 bytes, got {len(mask)}"
payload = b"".join(struct.pack("<H", value if m else 0) for m in mask)
frame = header + struct.pack("<I", zlib.crc32(header)) + payload + struct.pack("<I", zlib.crc32(payload))
assert len(frame) == 0x910, len(frame)
print(f"level {level} -> value {value}; {sum(mask)}/1152 channels lit; frame {len(frame)} bytes; head {frame[:16].hex(' ')} ... tail {frame[-8:].hex(' ')}")
if port == "-": sys.exit()
import serial
s = serial.Serial(port, timeout=0.1, write_timeout=1)   # never hang if the controller stops reading
end = time.time() + secs; n = 0
if secs == 0:   # one frame per change: repeated sends can wedge the controller
    s.write(frame); s.flush(); time.sleep(0.5); r = s.read(256)
    print(f"sent 1 frame; reply {r.hex(' ') if r else 'none'}"); s.close(); sys.exit()
while time.time() < end:
    try:
        s.write(frame); s.flush(); n += 1
    except serial.SerialTimeoutException:
        print(f"  write stalled after {n} frames (controller not reading)"); time.sleep(1); continue
    r = s.read(256)
    if r: print(f"  {n:3d} reply {len(r)} bytes: {r[:48].hex(' ')}")
    time.sleep(1)
s.close(); print(f"sent {n} frames")
