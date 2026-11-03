#!/usr/bin/env python3
"""aero_fans.py -- give the Aero's main MCU its fan curves (cmd 0x74), once.

  aero_fans.py PORT [PAYLOAD]         send it once to the main MCU (2f9a:0200) and print the reply;
                                      PORT '-' = dry run. PAYLOAD default: varjo-aero-fans.bin (aero_data.py: user copy, else shipped)

0x74 (MCU fw 0.17.0): 8 sensors x 16 bytes = 8 (temp degC, fan %) pairs per sensor, written to the MCU's fan table.
Without it the MCU keeps its built-in table (first point 70 degC -> 85 %), so the fans stay off until 70 degC; the
shipped curve idles them at 10 % from 47 degC.
Frame: [len u16][cmd][flags, 0x80 = reply][00 00][seq u16 LE][payload]."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aero_data

def show(p):
    for s in range(8):
        pts = [(p[s * 16 + i], p[s * 16 + i + 1]) for i in range(0, 16, 2) if p[s * 16 + i] or p[s * 16 + i + 1]]
        if pts: print(f"  sensor {s}: " + ", ".join(f"{t} C -> {v} %" for t, v in pts))

port = sys.argv[1]
payload = open(sys.argv[2] if len(sys.argv) > 2 else aero_data.data_path("varjo-aero-fans.bin"), "rb").read()
assert len(payload) == 128
frame = (8 + len(payload)).to_bytes(2, "little") + bytes([0x74, 0, 0, 0]) + (1).to_bytes(2, "little") + payload
print(f"0x74 frame {len(frame)} bytes:"); show(payload)
if port == "-": sys.exit()
import serial
s = serial.Serial(port, timeout=0.2, write_timeout=1)
s.reset_input_buffer(); s.write(frame); s.flush()
end = time.time() + 2
while time.time() < end:                       # skip events/heartbeats until our 0x74 reply
    b = s.read(2)
    if len(b) < 2: continue
    n = int.from_bytes(b, "little"); r = b + s.read(max(0, n - 2))
    if len(r) >= 6 and r[2] == 0x74 and r[3] & 0x80:
        st = int.from_bytes(r[4:6], "little")
        print(f"reply {r.hex(' ')}  -> {'OK' if st == 0 else f'error {st:#x}'}"); break
else:
    print("no 0x74 reply within 2 s")
s.close()
