#!/usr/bin/env python3
"""aero_storage.py -- read the Varjo Aero's own storage (factory calibration) over USB, no Varjo software needed.

  aero_storage.py list [PORT]                 list the entries (key, type, length, crc)
  aero_storage.py read KEY OUT [PORT]         read one entry (KEY hex, e.g. 1000) to OUT
  aero_storage.py setup [OUTDIR] [PORT]       read 0x1000 (lens distortion), 0x8040 (IPD table) and 0x8041 (factory
                                              eye-camera / IPD chart data, a zip) into OUTDIR (default
                                              ~/.config/monado): varjo-aero-flash-1000.bin,
                                              varjo-aero-ipd-calibration.json, varjo-aero-eyecam-factory.zip

Main MCU (2f9a:0200, CDC ACM), frame [len u16][cmd][flags, 0x80 = reply][status u16 in replies][seq u16][payload]:
  0x8D list:  payload = previous key u16 + 4 zero bytes (key 0 = start). Reply payload = next key u16, type u32,
              length u32, crc u32; status 4 = no more entries.
  0x8B read:  payload = key u16, offset u32, length u16 (<= 512), 2 zero bytes. Reply: 18-byte header, then the data.
The crc is the zlib CRC-32 of the entry; every read is checked against it."""
import glob, os, sys, time, zlib, serial

CHUNK = 512


def default_port():
    ports = glob.glob("/dev/serial/by-id/usb-Varjo_Technologies_Varjo_Headset_*-if00")
    if not ports:
        sys.exit("aero_storage: headset main MCU not found (/dev/serial/by-id/usb-Varjo_Technologies_Varjo_Headset_*)")
    return ports[0]


class Mcu:
    def __init__(self, port):
        self.s = serial.Serial(port, timeout=0.1, write_timeout=1)
        self.s.reset_input_buffer()
        self.seq = 0

    def cmd(self, c, payload, wait=2.0):
        """Send one command, return (status, reply payload); other frames (async events) are skipped."""
        self.seq = (self.seq + 1) & 0xFFFF
        self.s.write((8 + len(payload)).to_bytes(2, "little") + bytes([c, 0, 0, 0]) + self.seq.to_bytes(2, "little")
                     + payload)
        end = time.time() + wait
        while time.time() < end:
            b = self.s.read(2)
            if len(b) < 2:
                continue
            n = int.from_bytes(b, "little")
            f = b + self.s.read(max(0, n - 2))
            if len(f) >= 8 and f[2] == c and f[3] & 0x80 and int.from_bytes(f[6:8], "little") == self.seq:
                return int.from_bytes(f[4:6], "little"), f[8:]
        return None, b""

    def entries(self):
        key, out = 0, []
        while True:
            st, p = self.cmd(0x8D, key.to_bytes(2, "little") + bytes(4))
            if st == 4:
                return out
            if st != 0 or len(p) < 14:
                sys.exit(f"aero_storage: list failed after key 0x{key:04X} (status {st})")
            key = int.from_bytes(p[0:2], "little")
            out.append((key, int.from_bytes(p[2:6], "little"), int.from_bytes(p[6:10], "little"),
                        int.from_bytes(p[10:14], "little")))

    def read(self, key, length):
        data = b""
        while len(data) < length:
            n = min(CHUNK, length - len(data))
            for _attempt in range(3):
                st, p = self.cmd(0x8B, key.to_bytes(2, "little") + len(data).to_bytes(4, "little")
                                 + n.to_bytes(2, "little") + bytes(2))
                if st == 0 and len(p) >= 10 + n:
                    break
            else:
                sys.exit(f"aero_storage: read 0x{key:04X} failed at offset {len(data)} (status {st})")
            data += p[10:10 + n]                         # 18-byte reply header = 8 frame + 10 (key, 2x u32)
        return data


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("list", "read", "setup"):
        sys.exit(__doc__)
    if args[0] == "list":
        m = Mcu(args[1] if len(args) > 1 else default_port())
        for key, typ, length, crc in m.entries():
            print(f"0x{key:04X}  type {typ}  {length:8d} bytes  crc 0x{crc:08X}")
    elif args[0] == "read":
        key = int(args[1], 16)
        m = Mcu(args[3] if len(args) > 3 else default_port())
        ent = {k: (t, ln, c) for k, t, ln, c in m.entries()}
        if key not in ent:
            sys.exit(f"aero_storage: no entry 0x{key:04X}")
        data = m.read(key, ent[key][1])
        if zlib.crc32(data) != ent[key][2]:
            sys.exit(f"aero_storage: entry 0x{key:04X} crc mismatch -- not written")
        open(args[2], "wb").write(data)
        print(f"0x{key:04X}: {len(data)} bytes -> {args[2]} (crc32 0x{zlib.crc32(data):08X}, listed 0x{ent[key][2]:08X})")
    else:
        outdir = os.path.expanduser(args[1] if len(args) > 1 else "~/.config/monado")
        m = Mcu(args[2] if len(args) > 2 else default_port())
        ent = {k: (t, ln, c) for k, t, ln, c in m.entries()}
        os.makedirs(outdir, exist_ok=True)
        for key, name in ((0x1000, "varjo-aero-flash-1000.bin"), (0x8040, "varjo-aero-ipd-calibration.json"),
                          (0x8041, "varjo-aero-eyecam-factory.zip")):
            if key not in ent:
                sys.exit(f"aero_storage: the headset has no entry 0x{key:04X}")
            data = m.read(key, ent[key][1])
            if zlib.crc32(data) != ent[key][2]:
                sys.exit(f"aero_storage: entry 0x{key:04X} crc mismatch -- not written")
            path = os.path.join(outdir, name)
            open(path, "wb").write(data)
            print(f"0x{key:04X}: {len(data)} bytes -> {path}")


if __name__ == "__main__":
    main()
