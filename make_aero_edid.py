#!/usr/bin/env python3
"""make_aero_edid.py IN OUT -- add a CTA-861 extension carrying Microsoft's HMD VSDB (version 3, desktop usage = 0)
to the Varjo Aero's EDID so the kernel sets non-desktop (drm_parse_microsoft_vsdb). Base block and the DisplayID
block with the 5760x2720@90 timing are kept byte-for-byte; only block 0's extension count + checksum change."""
import sys
src = bytearray(open(sys.argv[1], "rb").read())
assert len(src) == 256 and src[126] == 1 and src[128] == 0x70, "expected base + 1 DisplayID block"
cs = lambda b: (-sum(b[:127])) & 0xFF
cta = bytearray(128)
cta[0], cta[1], cta[3] = 0x02, 0x03, 0x80                 # CTA-861 ext, rev 3, underscan
vsdb = bytes([0x60 | 21, 0x5C, 0x12, 0xCA, 0x03, 0x27]) + bytes(16)   # vendor block, OUI CA125C, v3, desktop_usage=0, third-party=1, use case 7 (VR), container ID 0
cta[4:4 + len(vsdb)] = vsdb
cta[2] = 4 + len(vsdb)                                     # DTD offset: none follow
cta[127] = cs(cta)
base = bytearray(src[:128]); base[126] = 2; base[127] = cs(base)
out = bytes(base) + bytes(src[128:256]) + bytes(cta)
open(sys.argv[2], "wb").write(out); print(f"wrote {len(out)} bytes")
