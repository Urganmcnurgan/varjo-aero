#!/bin/bash
# install.sh -- set up the Varjo Aero on Linux: tools, companion service, per-headset calibration, Envision profile,
# and (with your confirmation, via sudo) the system parts: VR EDID + kernel entry, udev fallback, NVIDIA driver patch.
#
#   ./install.sh [--monado-repo URL] [--monado-branch BRANCH] [--user-only] [--check]
#
# Run it as your normal user with the headset plugged in and powered, Envision closed. Safe to run again (updates in
# place). --user-only skips every sudo step; --check only reports what is missing.
# Installs to ~/.local/share/varjo-aero (AERO_PREFIX), settings in ~/.config/monado/varjo-aero.conf.
set -euo pipefail

SRC=$(dirname "$(readlink -f "$0")")
PREFIX=${AERO_PREFIX:-$HOME/.local/share/varjo-aero}
CONF=$HOME/.config/monado/varjo-aero.conf
MONADO_REPO=${MONADO_REPO:-}       # empty = keep the existing profile's (envision_profile.py has the default)
MONADO_BRANCH=${MONADO_BRANCH:-}       # empty = keep the existing profile's, else varjo-aero
USER_ONLY=0 CHECK=0
while [ $# -gt 0 ]; do
  case $1 in
    --monado-repo) MONADO_REPO=$2; shift ;;
    --monado-branch) MONADO_BRANCH=$2; shift ;;
    --user-only) USER_ONLY=1 ;;
    --check) CHECK=1 ;;
    *) sed -n 2,9p "$0"; exit 1 ;;
  esac
  shift
done

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok() { printf '   ok: %s\n' "$*"; }
warn() { printf '   \033[33m!! %s\033[0m\n' "$*"; }
ask() { local a; read -r -p "   $1 [y/N] " a; [ "${a,,}" = y ]; }
MISSING=0

# --- the headset ------------------------------------------------------------------------------------------------
say "Headset"
AERO_CONN="" AERO_EDID=""
for e in /sys/class/drm/card*-*/edid; do
  if [ "$(od -An -tx1 -j8 -N4 "$e" 2>/dev/null | tr -d ' \n')" = 5a544358 ]; then   # VRT 0x5843
    AERO_EDID=$e; AERO_CONN=${e%/edid}; AERO_CONN=${AERO_CONN##*/}; AERO_CONN=${AERO_CONN#card*-}
  fi
done
MCU=$(ls /dev/serial/by-id/usb-Varjo_Technologies_Varjo_Headset_*-if00 2>/dev/null | head -1 || true)
[ -n "$AERO_CONN" ] && ok "display on $AERO_CONN" || { warn "headset display not found -- plug it in (DisplayPort) and power it"; MISSING=1; }
[ -n "$MCU" ] && ok "USB controller $MCU" || { warn "headset USB not found -- is the USB cable in and the power on?"; MISSING=1; }
if [ -n "$MCU" ] && [ ! -w "$MCU" ]; then
  warn "no access to $MCU -- add yourself to the group that owns it ($(stat -c %G "$(readlink -f "$MCU")")), log out and in"
  MISSING=1
fi

# --- system part (sudo) -----------------------------------------------------------------------------------------
FW=/usr/lib/firmware/edid/varjo-aero-hmd.bin
if [ "$USER_ONLY" = 0 ]; then
  say "VR EDID (makes the headset a VR display instead of a desktop monitor)"
  if [ -f "$FW" ]; then ok "$FW installed"; else warn "$FW missing"; MISSING=1; fi
  if [ ! -f "$FW" ] && [ "$CHECK" = 0 ] && [ -n "$AERO_EDID" ]; then
    if [ "$(wc -c < "$AERO_EDID")" = 256 ]; then
      tmp=$(mktemp); python3 "$SRC/make_aero_edid.py" "$AERO_EDID" "$tmp" >/dev/null
      if ask "Install the VR EDID built from your headset's own EDID to $FW?"; then
        sudo install -Dm644 "$tmp" "$FW" && ok "installed"
      fi
      rm -f "$tmp"
    else
      warn "the headset already shows a VR EDID ($(wc -c < "$AERO_EDID") bytes) -- installed by hand? copy it to $FW"
    fi
  fi
  # Kernel entry: the VR EDID from the first detect. Applied later (udev), it races NVIDIA and the desktop.
  ENTRY="drm.edid_firmware=$AERO_CONN:edid/varjo-aero-hmd.bin"
  if grep -q "drm.edid_firmware=[^ ]*varjo-aero-hmd" /proc/cmdline; then
    ok "kernel entry active: $(grep -o 'drm.edid_firmware=[^ ]*' /proc/cmdline)"
  elif grep -qs "drm.edid_firmware=[^ \"]*varjo-aero-hmd" /etc/default/grub; then
    ok "kernel entry configured in /etc/default/grub, active after the next reboot"
  else
    warn "kernel entry missing: $ENTRY"; MISSING=1
    if [ "$CHECK" = 0 ] && [ -n "$AERO_CONN" ] && [ -f "$FW" ]; then
      if [ -f /etc/default/grub ] && grep -q '^GRUB_CMDLINE_LINUX_DEFAULT=' /etc/default/grub; then
        if ask "Add '$ENTRY' to /etc/default/grub and regenerate the grub config?"; then
          sudo cp /etc/default/grub /etc/default/grub.before-varjo-aero
          sudo sed -i "s|^GRUB_CMDLINE_LINUX_DEFAULT=\"\\(.*\\)\"|GRUB_CMDLINE_LINUX_DEFAULT=\"\\1 $ENTRY\"|" /etc/default/grub
          if command -v grub-mkconfig >/dev/null; then sudo grub-mkconfig -o /boot/grub/grub.cfg
          elif command -v update-grub >/dev/null; then sudo update-grub
          else sudo grub2-mkconfig -o /boot/grub2/grub.cfg; fi
          ok "added (active after a reboot; backup /etc/default/grub.before-varjo-aero)"
        fi
      else
        warn "not grub -- add this to your kernel command line yourself (systemd-boot: /etc/kernel/cmdline or the"
        warn "options line in /boot/loader/entries/*.conf): $ENTRY"
      fi
      # Early KMS: if the GPU driver loads from the initramfs, the firmware file has to be in it.
      if command -v mkinitcpio >/dev/null && [ ! -f /etc/mkinitcpio.conf.d/varjo-aero-edid.conf ] &&
         ask "Add the EDID file to the initramfs (mkinitcpio) and rebuild it?"; then
        echo "FILES+=($FW)" | sudo tee /etc/mkinitcpio.conf.d/varjo-aero-edid.conf >/dev/null && sudo mkinitcpio -P
      elif command -v dracut >/dev/null && [ ! -f /etc/dracut.conf.d/varjo-aero-edid.conf ] &&
           ask "Add the EDID file to the initramfs (dracut) and rebuild it?"; then
        echo "install_items+=\" $FW \"" | sudo tee /etc/dracut.conf.d/varjo-aero-edid.conf >/dev/null && sudo dracut -f
      fi
    fi
  fi

  say "udev fallback (VR EDID on other ports; needs one headset power cycle there)"
  if [ -f /etc/udev/rules.d/99-varjo-aero-edid.rules ]; then ok "installed"; else
    warn "not installed"
    if [ "$CHECK" = 0 ] && ask "Install the udev rule + service?"; then
      sudo install -Dm755 "$SRC/udev/aero-edid-override.sh" /usr/local/lib/varjo-aero/aero-edid-override.sh
      sudo install -Dm644 "$SRC/udev/varjo-aero-edid.service" /etc/systemd/system/varjo-aero-edid.service
      sudo install -Dm644 "$SRC/udev/99-varjo-aero-edid.rules" /etc/udev/rules.d/99-varjo-aero-edid.rules
      sudo systemctl daemon-reload && sudo udevadm control --reload && ok "installed"
    fi
  fi

  say "GPU driver"
  if [ -d /sys/module/nvidia ]; then
    ver=$(cat /sys/module/nvidia/version); src=/usr/src/nvidia-$ver
    if [ "$(modinfo -F license nvidia 2>/dev/null)" != "Dual MIT/GPL" ]; then
      warn "NVIDIA proprietary kernel modules: the Aero needs the OPEN modules (nvidia-open), patched -- install those first"
      MISSING=1
    elif [ ! -d "$src" ]; then
      warn "NVIDIA $ver: no DKMS source in $src -- install the -dkms variant of the open modules so it can be patched"
      MISSING=1
    else
      need=()
      grep -q mergeHeadSection "$src/src/nvidia-modeset/src/nvkms-evo.c" 2>/dev/null &&
        ok "2Heads1OR viewport fix (PR #1262)" || need+=(nvidia-pr1262-2heads1or-viewport.patch)
      grep -q mergedNonVrr "$src/src/nvidia-modeset/src/nvkms-flip.c" 2>/dev/null &&
        ok "merged-heads VRR tear fix" || need+=(nvidia-aero-merged-novrr.patch)
      if [ ${#need[@]} -gt 0 ]; then
        warn "NVIDIA $ver needs: ${need[*]} (without the first: left half of the picture in both eyes)"; MISSING=1
        if [ "$CHECK" = 0 ] && ask "Patch $src and rebuild the module with DKMS (re-run this after every NVIDIA update)?"; then
          applies=1
          for p in "${need[@]}"; do
            patch -p1 --dry-run -s -d "$src" < "$SRC/$p" >/dev/null 2>&1 || { warn "$p does not apply to NVIDIA $ver -- driver left unchanged; please report your driver version"; applies=0; }
          done
          if [ "$applies" = 1 ]; then
            for p in "${need[@]}"; do sudo patch -p1 -s -d "$src" < "$SRC/$p"; done
            sudo dkms remove "nvidia/$ver" --all || true
            # rebuild for every installed kernel that has headers, not only the running one (e.g. linux + linux-lts)
            for k in /usr/lib/modules/*/build /lib/modules/*/build; do
              [ -d "$k" ] || continue; kv=$(basename "$(dirname "$k")")
              sudo dkms install "nvidia/$ver" -k "$kv" || warn "DKMS build for kernel $kv failed"
            done
            if command -v mkinitcpio >/dev/null; then sudo mkinitcpio -P; elif command -v dracut >/dev/null; then sudo dracut -f --regenerate-all; fi
            ok "patched -- reboot to load the new module"
          fi
        fi
      fi
    fi
  else
    warn "not an NVIDIA GPU: untested so far -- please report how it goes (no driver patch known to be needed)"
  fi
fi

[ "$CHECK" = 1 ] && { say "Check done"; [ "$MISSING" = 0 ] && ok "nothing missing" || warn "see above"; exit "$MISSING"; }

# --- user part --------------------------------------------------------------------------------------------------
say "Tools -> $PREFIX"
mkdir -p "$PREFIX/data"
install -m644 "$SRC"/*.py "$PREFIX/"
install -m755 "$SRC/aero_calibrate.sh" "$SRC/monado/aero-monado.sh" "$PREFIX/"
install -m644 "$SRC/monado/aero-env.sh" "$SRC/README.md" "$PREFIX/"
install -m644 "$SRC"/data/* "$PREFIX/data/"
chmod +x "$PREFIX"/aero_*.py "$PREFIX"/xr_*.py
if [ ! -x "$PREFIX/venv/bin/python" ]; then
  python3 -m venv "$PREFIX/venv" || { warn "python3 -m venv failed -- install python3-venv (Debian/Ubuntu) and run ./install.sh again"; exit 1; }
fi
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q numpy scipy opencv-python-headless pyserial pyopenxr glfw PyOpenGL pillow
ok "tools + Python environment"
mkdir -p "$HOME/.local/bin"
ln -sf "$PREFIX/aero-monado.sh" "$HOME/.local/bin/aero-monado"
ln -sf "$PREFIX/aero_calibrate.sh" "$HOME/.local/bin/aero-calibrate"
ln -sf "$PREFIX/aero_settings.py" "$HOME/.local/bin/aero-settings"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/varjo-aero-settings.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Varjo Aero Settings
Comment=Quality, IPD, eye-tracked foveation and brightness for the Varjo Aero
Exec=/usr/bin/python3 $PREFIX/aero_settings.py
Icon=preferences-desktop-display
Categories=Settings;HardwareSettings;
EOF
python3 -c "import gi; gi.require_version('Gtk', '4.0')" 2>/dev/null ||
  warn "the settings window needs GTK 4 for Python (PyGObject: python-gobject / python3-gi + gtk4)"
ok "commands: aero-monado, aero-calibrate, aero-settings (in ~/.local/bin); 'Varjo Aero Settings' in the app menu"
command -v v4l2-ctl >/dev/null || warn "v4l2-ctl missing (package v4l-utils) -- needed for eye-tracking calibration"

say "Settings"
mkdir -p "$(dirname "$CONF")"
if ! grep -qs '^AERO_IPD_MM=' "$CONF"; then
  ipd=""; [ -t 0 ] && read -r -p "   Your IPD (distance between your pupils) in mm, e.g. 63.0: " ipd
  [[ $ipd =~ ^[0-9]{2}(\.[0-9])?$ ]] || { warn "not a number like 63.0 -- using 63.0, change AERO_IPD_MM in $CONF"; ipd=63.0; }
  cat >> "$CONF" <<EOF
# Varjo Aero settings (KEY=VALUE). Read by the companion service and the launcher; changes apply at the next VR start.
AERO_IPD_MM=$ipd
AERO_QUALITY=VeryHigh   # VeryLow Low Fair Medium High VeryHigh ExtremelyHigh UltraHigh (same levels as Varjo Base)
# AERO_GAZE=1           # eye-tracked foveation (default: on once you have run aero-calibrate)
# AERO_BRIGHTNESS=60    # backlight 0-100, or off
# AERO_COMPUTE=1        # 0 = graphics compositor
# AERO_GAZE_FILTER=varjo # gaze filter: varjo (default) / twospeed (steadier, slower) / off
EOF
fi
ok "$CONF (IPD $(grep '^AERO_IPD_MM=' "$CONF" | cut -d= -f2) mm)"

say "Per-headset calibration (read from the headset)"
if [ -n "$MCU" ]; then
  if "$PREFIX/venv/bin/python" "$PREFIX/aero_storage.py" setup "$HOME/.config/monado" "$MCU" &&
     "$PREFIX/venv/bin/python" "$PREFIX/aero_mkdist_varjo.py" "$HOME/.config/monado/varjo-aero-flash-1000.bin" \
       "$PREFIX/data/varjo-aero-precompiled.bin" "$HOME/.config/monado/varjo-aero-distortion.bin" | tail -1; then
    ok "lens distortion + IPD table"
  else
    warn "reading the headset failed (no access to the headset port? see the group note at the top) -- run ./install.sh again"
  fi
else
  warn "headset USB not found -- skipped; run ./install.sh again with the headset on"
fi

say "Companion service"
mkdir -p "$HOME/.config/systemd/user"
sed "s|@TOOLS@|$PREFIX|g; s|/usr/bin/python3|$PREFIX/venv/bin/python|" "$SRC/systemd/varjo-aero-companion.service" \
  > "$HOME/.config/systemd/user/varjo-aero-companion.service"
systemctl --user daemon-reload
systemctl --user enable -q varjo-aero-companion.service
systemctl --user restart varjo-aero-companion.service
ok "varjo-aero-companion.service running"

say "Envision profile"
envfile=$(mktemp)
env -i HOME="$HOME" PATH="$PATH" bash -c ". '$PREFIX/aero-env.sh' >/dev/null 2>&1; env" |
  grep -E '^(STEAMVR_LH_ENABLE|LH_DISCOVER_WAIT_MS|XRT_|U_PACING_)' > "$envfile"
if command -v envision >/dev/null || [ -d "$HOME/.config/envision" ]; then
  python3 "$SRC/envision_profile.py" "$MONADO_REPO" "$MONADO_BRANCH" "$envfile" && ok "profile 'Varjo Aero'"
else
  warn "Envision not found -- install it (LVRA: https://lvra.gitlab.io), then run ./install.sh again"
fi
rm -f "$envfile"

say "Done"
echo "   1. If this installed the EDID, kernel entry or driver patch: reboot."
echo "   2. Open Envision, profile 'Varjo Aero': Build (first time), then Start. Plug the headset in before starting."
echo "   3. With VR running: aero-calibrate (eye tracking, ~20 s with the headset on), then stop and start VR."
echo "   Steam games: Envision shows the launch options."
echo "   Settings anytime (quality, IPD, foveation, brightness): app menu -> 'Varjo Aero Settings', or aero-settings."
echo "   In VR, the headset's side button measures your IPD and redoes the eye calibration."
# Envision has no button for the settings window: show it once now, while the user is still reading.
if [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ] && python3 -c "import gi; gi.require_version('Gtk', '4.0')" 2>/dev/null; then
  /usr/bin/python3 "$PREFIX/aero_settings.py" >/dev/null 2>&1 &
  disown
  echo "   (The settings window is open now.)"
fi
