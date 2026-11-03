#!/bin/bash
# aero-monado.sh [app...] -- run our patched Monado for the Varjo Aero from a terminal (Envision's Start button does the
# same through its profile). With no args: just the service. With args: also runs that app, e.g. aero-monado.sh sleep 1800
#
# The helpers (lenses, fans, backlight, eye tracker, audio) run in varjo-aero-companion.service whenever Monado runs;
# this script only starts Monado with the Aero's environment (aero-env.sh) and restarts it after a black start, which
# the companion reports in $XDG_RUNTIME_DIR/aero-companion.status.
# Settings: ~/.config/monado/varjo-aero.conf. Logs: /tmp/aero-monado-<time>.log, /tmp/aero-companion-<time>.*
#
# DIRECT MODE needs the VR EDID on the headset's connector (kernel drm.edid_firmware=<port>:edid/varjo-aero-hmd.bin, or
# the udev fallback) and, on NVIDIA, the open modules patched with PR #1262 (2Heads1OR viewport).
# AERO_WINDOW=1: window-mode fallback (fullscreen Wayland window; needs the ORIGINAL EDID so the headset is a desktop output).
HERE=$(dirname "$(readlink -f "$0")")
# Monado: the one Envision built for the "Varjo Aero" profile, else a development build (AERO_MONADO_BUILD).
EP=${XDG_DATA_HOME:-$HOME/.local/share}/envision/prefixes/varjo-aero
B=${AERO_MONADO_BUILD:-}
if [ -z "$AERO_MONADO_SERVICE" ] && [ -x "$EP/bin/monado-service" ] && [ -z "$B" ]; then
  SERVICE=$EP/bin/monado-service; RUNTIME_JSON=$EP/share/openxr/1/openxr_monado.json
elif [ -n "$AERO_MONADO_SERVICE" ] || [ -n "$B" ]; then
  SERVICE=${AERO_MONADO_SERVICE:-$B/src/xrt/targets/service/monado-service}; RUNTIME_JSON=${B:+$B/openxr_monado-dev.json}
else
  echo "aero-monado: no Monado found -- open Envision, profile 'Varjo Aero', and Build it once (or set AERO_MONADO_BUILD)" >&2
  exit 1
fi
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-1}
export HYPRLAND_INSTANCE_SIGNATURE=${HYPRLAND_INSTANCE_SIGNATURE:-$(ls "$XDG_RUNTIME_DIR/hypr" 2>/dev/null | head -1)}
# shellcheck source=aero-env.sh
. "$HERE/aero-env.sh"
[ -f "$RUNTIME_JSON" ] && export XR_RUNTIME_JSON=$RUNTIME_JSON
STATUS=$XDG_RUNTIME_DIR/aero-companion.status
systemctl --user is-active -q varjo-aero-companion.service ||
  echo "aero-monado: varjo-aero-companion.service is not running -- no lenses/fans/backlight/eye tracking" >&2

# The headset's connector, found by its EDID (manufacturer VRT, product 0x5843: bytes 8-11 = 5a 54 43 58).
AERO_CONNECTOR=""
for e in /sys/class/drm/card*-*/edid; do
  [ "$(od -An -tx1 -j8 -N4 "$e" 2>/dev/null | tr -d ' \n')" = 5a544358 ] && { AERO_CONNECTOR=${e%/edid}; AERO_CONNECTOR=${AERO_CONNECTOR##*/}; AERO_CONNECTOR=${AERO_CONNECTOR#card*-}; break; }
done
[ -n "$AERO_CONNECTOR" ] && echo "aero-monado: headset on $AERO_CONNECTOR" >&2 || echo "aero-monado: headset not found on any connector" >&2

if [ "${AERO_WINDOW:-0}" = 1 ]; then
  export XRT_COMPOSITOR_FORCE_WAYLAND=1 XRT_COMPOSITOR_XCB_FULLSCREEN=1   # full-size fullscreen window (comp_settings patch)
  export XRT_COMPOSITOR_DEFAULT_FRAMERATE=90                              # window mode cannot read the panel's refresh
  export XRT_COMPOSITOR_PRESENT_MODE=${AERO_PRESENT_MODE:-mailbox}       # fifo through the desktop misses every frame
  OUT=${AERO_CONNECTOR:-DP-3}
  hyprctl eval "hl.monitor({ output = \"$OUT\", mode = \"5760x2720@90\", position = \"5120x0\", scale = 1.0, transform = 0 })" >/dev/null
  hyprctl eval "hl.window_rule({ name = \"monado-aero\", match = { title = \"Monado\" }, monitor = \"$OUT\", fullscreen = true })" >/dev/null
fi

LOG=/tmp/aero-monado-$(date +%Y%m%d-%H%M%S).log; ln -sf "$LOG" /tmp/aero-monado.log
cleanup() {
  [ -n "$APP" ] && kill -- -"$APP" 2>/dev/null                # the app's own process group (setsid)
  pkill -x monado-service 2>/dev/null                         # the companion then stops the helpers
}
trap cleanup EXIT
trap 'exit 130' INT TERM
APP=""

rm -f "$XDG_RUNTIME_DIR/monado_comp_ipc"
if [ $# -eq 0 ]; then "$SERVICE" 2>&1 | tee "$LOG"; exit; fi

# Black-start retry: about one start in five the headset never gets its mode change although Monado renders. The
# companion watches the headset's own display status and reports picture/black; on black, restart (AERO_RETRIES,
# default 2). NVIDIA refusing the display (vkGetDrmDisplayEXT) cannot be fixed by retrying: pull the headset power.
wait_status() {   # wait for the companion's verdict on this start: prints picture | black | timeout
  local t=0 limit=$(( (LH_DISCOVER_WAIT_MS / 1000 + ${AERO_PICTURE_WAIT:-12} + 5) * 2 ))
  if ! systemctl --user is-active -q varjo-aero-companion.service; then   # no verdict coming: just give it time
    sleep $(( LH_DISCOVER_WAIT_MS / 1000 + 6 )); echo picture; return
  fi
  sleep 1
  while [ $t -lt $limit ]; do
    case $(cat "$STATUS" 2>/dev/null) in picture) echo picture; return ;; black) echo black; return ;; esac
    sleep 0.5; t=$((t + 1))
  done
  echo timeout
}
for attempt in $(seq 0 "${AERO_RETRIES:-2}"); do
  rm -f "$XDG_RUNTIME_DIR/monado_comp_ipc"
  # XRT_NO_STDIN: Monado ignores stdin (it used to need a "sleep infinity" pipe, which outlived every run).
  (XRT_NO_STDIN=1 "$SERVICE" > "$LOG" 2>&1 < /dev/null &)
  verdict=$(wait_status)
  if [ "$verdict" = picture ]; then
    echo "service log: $LOG (attempt $attempt): picture up -- starting the app"
    sleep 1                                                    # let Monado finish its IPC setup
    setsid "$@" & APP=$!                                       # own process group (clean stop)
    wait "$APP"; APP=""; exit 0                                # cleanup (EXIT trap) stops Monado
  fi
  if grep -aq "vkGetDrmDisplayEXT failed" "$LOG"; then
    echo "aero-monado: NVIDIA could not take over the headset display (vkGetDrmDisplayEXT). Pull the headset's power" \
         "for a few seconds, plug it back in, then start again." >&2
    exit 1
  fi
  echo "aero-monado: no picture ($verdict) -- restarting (attempt $((attempt + 1)))" >&2
  pkill -x monado-service
  sleep 3
done
echo "aero-monado: no picture after $((${AERO_RETRIES:-2} + 1)) starts" >&2
exit 1
