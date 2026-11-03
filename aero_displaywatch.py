#!/usr/bin/env python3
"""aero_displaywatch.py [SECONDS] [PORT] -- watch the headset's own display status (main-MCU event 0x90).

Opens a host session (0x02 "hello" enables MCU events) and keeps it alive with the 1 Hz heartbeat (0x04),
then prints every 0x90 status event: payload byte 4 = display status, and the side button (0x50: payload byte 2 =
1 press / 0 release) as "button press" / "button release". Display status:
3 for the whole of every VR session, 2 after it ends (display idle), 0 at power-up/disconnect; sent every 10 s and on
change. Purpose: tell a working start (3) from the intermittent black start (backlight glow, no mode change) so the
launcher can retry. SECONDS 0 = until Ctrl+C. Prints "status N" on change, "." every 10 s otherwise.
Frame: [len u16][cmd][flags, 0x80 = reply][00 00][seq u16 LE][payload]."""
import glob, sys, time, serial

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 0
port = sys.argv[2] if len(sys.argv) > 2 else glob.glob("/dev/serial/by-id/usb-Varjo_Technologies_Varjo_Headset_*-if00")[0]
def session():
    s = serial.Serial(port, timeout=0.1, write_timeout=1); s.reset_input_buffer()
    s.write(bytes([8, 0, 2, 0, 0, 0, 1, 0]))                  # hello: enables events
    return s
s = session(); t0 = time.time(); seq = 1; next_hb = t0 + 0.3; last = None
while secs == 0 or time.time() - t0 < secs:
    try:
        if time.time() >= next_hb:
            seq += 1; s.write(bytes([8, 0, 4, 0, 0, 0]) + (seq & 0xffff).to_bytes(2, "little")); next_hb = time.time() + 1
        b = s.read(2)
    except (serial.SerialException, OSError) as e:          # another process touched the port (Monado probing?)
        print(f"\n{time.strftime('%H:%M:%S')} port error ({e.__class__.__name__}), reopening", flush=True)
        try: s.close()
        except Exception: pass
        time.sleep(0.5)
        try: s = session()
        except Exception: pass
        continue
    if len(b) < 2: continue
    n = int.from_bytes(b, "little"); f = b + s.read(max(0, n - 2))
    if len(f) >= 11 and f[2] == 0x50 and not f[3] & 0x80:     # side button: payload byte 2 = 1 press, 0 release
        print(f"\n{time.strftime('%H:%M:%S')} button {'press' if f[10] else 'release'}", flush=True)
        continue
    if len(f) >= 13 and f[2] == 0x90 and not f[3] & 0x80:
        st = f[8 + 4]
        if st != last: print(f"{time.strftime('%H:%M:%S')} +{time.time() - t0:5.1f}s display status {st}", flush=True); last = st
        else: print(".", end="", flush=True)
s.close()
