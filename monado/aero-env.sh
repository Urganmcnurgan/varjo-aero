# aero-env.sh -- the environment monado-service needs for the Varjo Aero. Sourced by aero-monado.sh; the installer
# turns the same exports into the Envision profile's environment, so both start Monado identically.
# User settings: ~/.config/monado/varjo-aero.conf (AERO_* KEY=VALUE; the environment wins). Settings Monado reads
# itself (inset, quality, gaze) go through the session file the companion writes (u_aero.h), not through here.
# shellcheck shell=bash

# AERO_* from varjo-aero.conf, unless already set in the environment.
if [ -f "$HOME/.config/monado/varjo-aero.conf" ]; then
  while IFS='=' read -r k v; do
    k=${k%%#*}; k=${k// /}
    v=${v%%#*}; v=${v%"${v##*[![:space:]]}"}; v=${v#"${v%%[![:space:]]*}"}; v=${v#\"}; v=${v%\"}  # comment, blanks, quotes
    case $k in AERO_*) [ -z "${!k+x}" ] && export "$k=$v" ;; esac
  done < "$HOME/.config/monado/varjo-aero.conf"
fi

# 9 s lighthouse discovery: with 5 s a base station often arrived late ("Cannot add device after setup").
export STEAMVR_LH_ENABLE=true LH_DISCOVER_WAIT_MS=${AERO_LH_WAIT_MS:-9000}
# Compute compositor by default (games draw their own desktop mirror). AERO_COMPUTE=0 = graphics
# (XRT_WINDOW_PEEK works there). The panels' 180 deg rotation is done in the lens lookup for both.
export XRT_COMPOSITOR_COMPUTE=${AERO_COMPUTE:-1}
export XRT_COMPOSITOR_SCALE_PERCENTAGE=140
# Render IPD: AERO_IPD_MM reaches Monado through the session file (the companion writes it), like Envision starts.
# Predict poses to the panels' display time: vsync + period/2 + display latency (6.9 ms) = 12.5 ms at 90 Hz.
# NVIDIA has no VK_GOOGLE_display_timing -> Monado's fake pacer, default 4 ms.
export U_PACING_COMP_PRESENT_TO_DISPLAY_OFFSET_MS=${AERO_DISPLAY_OFFSET_MS:-12.5}
# Direct-mode tear: fixed by the driver patch nvidia-aero-merged-novrr.patch or nvidia_modeset
# conceal_vrr_caps=1. Without either: present 1 ms before vblank so the tear lands in rows the lens does not show.
NV_SRC=/usr/src/nvidia-$(cat /sys/module/nvidia_modeset/version 2>/dev/null)
if [ ! -d /sys/module/nvidia_modeset ] ||
   [ "$(cat /sys/module/nvidia_modeset/parameters/conceal_vrr_caps 2>/dev/null)" = Y ] ||
   grep -q mergedNonVrr "$NV_SRC/src/nvidia-modeset/src/nvkms-flip.c" 2>/dev/null; then
  [ -n "$AERO_COMP_MS" ] && export U_PACING_COMP_MIN_TIME_MS=$AERO_COMP_MS
  [ -n "$AERO_COMP_PERCENT" ] && export U_PACING_COMP_TIME_FRACTION_PERCENT=$AERO_COMP_PERCENT
else
  echo "aero-env: no NVIDIA VRR tear fix found (merged-novrr patch / conceal_vrr_caps) -- using the 1 ms tear workaround" >&2
  export U_PACING_COMP_MIN_TIME_MS=${AERO_COMP_MS:-1.0} U_PACING_COMP_TIME_FRACTION_PERCENT=${AERO_COMP_PERCENT:-5}
fi
