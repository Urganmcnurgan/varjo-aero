#!/bin/bash
# aero-calibrate -- eye-tracking calibration for the Varjo Aero, the same dot sequence as Varjo Base: look at a green
# dot in the centre, then as it moves to the four corners (diagonal order) and back to the centre (~20 s).
# Needs Monado running with a picture (Envision Start, or aero-monado). Writes the per-user calibration
# ~/.config/monado/varjo-aero-gaze-$USER.json that the eye tracker uses; restart VR afterwards to use it.
# Steps: xr_calib.py (dots + camera recording) -> aero_pupils.py (pupils) -> aero_gazefit.py (per-eye mapping).
set -e
TOOLS=$(dirname "$(readlink -f "$0")")
PY=$TOOLS/venv/bin/python; [ -x "$PY" ] || PY=python3
export AERO_EYECAM_DIR=${AERO_EYECAM_DIR:-$HOME/.local/state/varjo-aero/eyecam}
OUT=${AERO_GAZE_CAL:-$HOME/.config/monado/varjo-aero-gaze-$USER.json}
mkdir -p "$AERO_EYECAM_DIR"

pgrep -x monado-service >/dev/null || { echo "aero-calibrate: start VR first (Envision Start, or aero-monado)" >&2; exit 1; }
# The cameras can only be used by one program: stop the live eye tracker (it starts again with the next VR session).
pkill -f "[a]ero_eyetrack\.py" && sleep 1 || true

echo "Put the headset on. A green dot appears in the centre: look at it, and follow it to each corner and back."
echo "Starting in 5 s..."; sleep 5
before=$(date +%s)
"$PY" "$TOOLS/xr_calib.py"

calib=$(ls -t "$AERO_EYECAM_DIR"/calib_[0-9][0-9][0-9][0-9][0-9][0-9].json 2>/dev/null | head -1)
stream=$(ls -t "$AERO_EYECAM_DIR"/stream_*.yuyv 2>/dev/null | head -1)
if [ -z "$calib" ] || [ -z "$stream" ] || [ "$(stat -c %Y "$stream")" -lt "$before" ]; then
  echo "aero-calibrate: no recording from this session (see $AERO_EYECAM_DIR/calib_eyecam.log)" >&2; exit 1
fi
trap 'rm -f "$stream"' EXIT                              # several GB of raw camera frames, also on failure
echo "Finding the pupils..."
"$PY" "$TOOLS/aero_pupils.py" "$stream" "${stream%.yuyv}" >/dev/null
echo "Fitting..."
"$PY" "$TOOLS/aero_gazefit.py" "$calib" "$stream.json" "${stream%.yuyv}.csv"
fit=${calib%.json}_fit.json
"$PY" - "$fit" <<'EOF' || { echo "aero-calibrate: calibration failed (pupils not found on enough targets) -- try again" >&2; exit 1; }
import json, sys
fit = json.load(open(sys.argv[1]))
sys.exit(0 if "A_pupil" in fit and "B_pupil" in fit else 1)
EOF
mkdir -p "$(dirname "$OUT")"
cp "$fit" "$OUT"
echo "Saved $OUT -- stop and start VR to use it."
