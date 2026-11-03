#!/bin/sh
# aero-edid-override.sh -- give the Varjo Aero its VR EDID on whichever connector it is plugged into.
# Run as root by varjo-aero-edid.service, which the udev rule starts on every DRM hotplug. For each connector whose
# EDID is the Aero's own (manufacturer VRT, product 0x5843: bytes 8-11 = 5a 54 43 58; 256 bytes), write the VR EDID
# (same blocks + Microsoft HMD block -> non-desktop) to that connector's debugfs edid_override and re-probe it, so the
# desktop releases it and Monado can lease it. Replaces the per-port kernel option drm.edid_firmware=DP-3:...
# Loop-safe: once the override is active the connector's EDID is the 384-byte VR one and is skipped.
FW=${AERO_EDID_FW:-/usr/lib/firmware/edid/varjo-aero-hmd.bin}
[ -r "$FW" ] || { echo "aero-edid: $FW missing" >&2; exit 1; }
for e in /sys/class/drm/card*-*/edid; do
	[ "$(od -An -tx1 -j8 -N4 "$e" 2>/dev/null | tr -d ' \n')" = 5a544358 ] || continue
	[ "$(wc -c < "$e")" = 256 ] || continue                       # already the VR EDID
	conn=${e%/edid}; dev=${conn##*/}                              # card1-DP-3
	card=${dev%%-*}; name=${dev#*-}                               # card1, DP-3
	ov=/sys/kernel/debug/dri/${card#card}/$name/edid_override
	[ -w "$ov" ] || { echo "aero-edid: $ov not writable (debugfs mounted?)" >&2; continue; }
	cat "$FW" > "$ov" && echo detect > "$conn/status" && echo "aero-edid: VR EDID on $name"
done
exit 0
