# Varjo Aero on Linux

Run the **Varjo Aero** headset on Linux with [Monado](https://monado.freedesktop.org) and
[Envision](https://gitlab.com/gabmus/envision): corrected lenses, eye-tracked foveated rendering, the
headset's fans, backlight, motorised lenses and audio, and Steam games through Proton.

The Aero is no longer supported by its maker. This project keeps it useful.

> **Status: alpha.** It works well day to day on one machine (see *Tested on*). Please report how it goes on
> yours, good or bad.

## What works

- **Picture**: direct mode at 90 Hz with the headset's own factory lens correction; both eyes upright and fused.
- **Eye tracking**: calibration with the same dot sequence as Varjo Base, and the gaze drives the sharp
  centre of the image (quad views) in OpenXR apps and in OpenVR games that support eye-tracked foveated rendering
  (Half-Life: Alyx).
- **Quality levels**: the same levels as Varjo Base (VeryLow ... UltraHigh), for foveated and plain stereo apps.
- **Side button**: measures your IPD with the eye cameras, moves the lenses, and redoes the eye calibration.
- **Headset hardware**: lens motors (IPD), fans, backlight brightness, temperatures, audio switches to the
  headset while VR runs.
- **Controllers**: Valve Index controllers and base stations (lighthouse); they switch off a few minutes after VR stops.
- **Desktop in VR**: WayVR (formerly WlxOverlay-S) starts with every session if installed.
- **Games tested**: Beat Saber, Red Matter 2, Half-Life: Alyx, Assetto Corsa Competizione, Microsoft Flight
  Simulator 2024; Blender VR.

## Requirements

- Linux with a Wayland desktop (tested on Hyprland) and XWayland, PipeWire or PulseAudio (`pactl`, `paplay`),
  `notify-send`, systemd user services. The desktop must pass its session to systemd: GNOME and KDE do; on
  Hyprland, Sway and similar add `dbus-update-activation-environment --systemd --all` to your autostart.
- `v4l-utils` (eye-tracking calibration), Python 3 with `venv` (`python3-venv` on Debian/Ubuntu).
- **NVIDIA**: the *open* kernel modules as DKMS (`nvidia-open-dkms` or your distro's equivalent). The installer
  patches them (two small patches in this repo; re-run the installer after every NVIDIA driver update).
  AMD and Intel are untested -- reports welcome.
- [Envision](https://gitlab.com/gabmus/envision) 3.x (it builds Monado and xrizer for you), Steam for games.
- Lighthouse tracking: base stations and SteamVR installed through Steam (only its lighthouse driver is used).
- Python 3 with GTK 4 bindings (`python-gobject`) for the settings window.

## Install

1. Plug the headset in (DisplayPort + USB) and power it. Close Envision.
2. Run the installer as your normal user:
   ```
   git clone https://github.com/Urganmcnurgan/varjo-aero
   cd varjo-aero
   ./install.sh
   ```
   It installs the tools to `~/.local/share/varjo-aero`, asks for your IPD, reads the headset's own calibration
   over USB, sets up the companion service and an Envision profile **"Varjo Aero"**, and -- each step only after
   asking you, via `sudo` -- the VR display mode (EDID + kernel entry), a udev fallback, and the NVIDIA patches.
   `./install.sh --check` only reports what is missing; `--user-only` skips every `sudo` step.
3. Reboot if the installer changed the kernel entry or the driver.
4. Open Envision, choose the profile **Varjo Aero**, and **Build** (first time only; it takes a while).
5. **Start** in Envision. Put the headset on.
6. Eye tracking: with VR running, press the headset's **side button** and follow the prompts: look at the dot, then
   follow the dots. (Or run `aero-calibrate`.) From then on eye-tracked foveation is on automatically.

## Everyday use

- Turn the controllers on, then **Start** in Envision. A chime plays when the picture is up.
- Steam games: set the launch options Envision shows for the profile, then press Play in Steam.
- **Side button** (any time in VR): IPD measurement, lenses, and eye calibration. A calibration is only saved if it
  is better than the one you have.
- **Settings**: app menu -> *Varjo Aero Settings*, or `aero-settings`.
- When you are done: **Stop** in Envision, then unplug the headset's power (it does not fully power off).

## Settings

`~/.config/monado/varjo-aero.conf`, one `KEY=VALUE` per line (the settings window edits it for you). Changes apply
at the next VR start unless noted:

| key | default | what it does |
|---|---|---|
| `AERO_QUALITY` | `VeryHigh` | render resolution: VeryLow, Low, Fair, Medium, High, VeryHigh, ExtremelyHigh, UltraHigh |
| `AERO_IPD_MM` | (asked at install) | your IPD; the lenses move there at every start |
| `AERO_GAZE` | on with a calibration | `0` = fixed foveation instead of eye-tracked |
| `AERO_GAZE_FILTER` | `varjo` | gaze filter: `varjo` (fast, predicts eye movement), `twospeed` (steadier, slower), `off`; changes apply within a second, even mid-game |
| `AERO_BRIGHTNESS` | `60` | backlight 0-100, or `off` |
| `AERO_COMPUTE` | `1` | `0` = graphics compositor instead of compute (re-run `./install.sh` after changing it) |
| `AERO_DESKTOP` | `on` | `off` = don't start WayVR |
| `AERO_AUDIO` | `on` | `off` = don't switch audio to the headset |
| `AERO_CONTROLLERS_OFF` | `5` | minutes after VR stops before the controllers switch off; `off` disables |

## Games

- **Beat Saber**: nothing special.
- **Red Matter 2**: launch option `%command% -hmd=OpenXR` (the controller fix is applied automatically).
- **Half-Life: Alyx**: turn on *Foveated Rendering* in the game's Advanced Performance settings to get
  eye-tracked foveation (it comes from the xrizer that Envision builds for this profile).
- **Microsoft Flight Simulator 2024** (Steam): eye-tracked foveated rendering works.
  - Use **GE-Proton** (tested: GE-Proton11-12; install with ProtonUp-Qt or protonup) and launch option
    `PROTON_ENABLE_WAYLAND=1 AERO_SRGB_FIX=0 %command%`. Under Proton Experimental the mouse barely moves in VR
    (Proton Experimental has no Wayland mode; `PROTON_ENABLE_WAYLAND=1` does nothing there) and the picture is
    less stable. `AERO_SRGB_FIX=0`: the automatic colour fix for Proton games makes MSFS too dark; if the cockpit
    then looks washed out, lower the game's own VR darkening option.
  - In the game's VR graphics: **Dynamic settings off** (it makes the game rebuild its VR images constantly, which
    shows as flicker), foveated rendering on, reprojection off (it can crash the game, and Monado does not use it).
    Quality VeryHigh with DLSS Quality is about the sharpness of the same settings under Varjo Base.
  - Honeycomb Alpha / Bravo: the Bravo throttle needs `PROTON_ENABLE_HIDRAW=0x294b/0x1901` added to the launch
    options and a udev rule giving you access to it:
    `echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="294b", TAG+="uaccess"' | sudo tee /etc/udev/rules.d/70-honeycomb.rules`,
    then replug it.

## Known issues

- Roughly 1 start in 5 shows a black headset. A desktop notification says so: press **Stop** and **Start** in
  Envision. (`aero-monado <app>` from a terminal retries by itself.)
- Half-Life: Alyx can crash or hang at certain points in the game (under investigation; not related to eye tracking).
- The installer sets up the VR display mode for the DisplayPort output the headset is on. On another output, the
  first plug-in after a reboot can come up as a desktop monitor: unplug the headset's power and plug it in again.
  Best keep the headset on the same output.
- After an NVIDIA driver update, re-run `./install.sh` to re-apply the driver patches.

## Tested on

ArcoLinux (Arch-based), Hyprland 0.56, NVIDIA RTX 3090 with the open modules 615.78 (patched), Envision 3.2,
Valve Index controllers and base stations.

## How it fits together

- **Monado** (patched; Envision builds it from the `varjo-aero` branch of our fork) drives the display, lens
  correction, quad views and eye gaze.
- **xrizer** (patched, same) lets OpenVR games run on Monado, with eye-tracked foveation.
- **Companion service** (`aero_companion.py`, a systemd user service) does everything else whenever VR starts, however
  it is started: lenses, fans, backlight, audio, eye tracker, desktop overlay, black-start retry, controllers off.
- `monado/aero-monado.sh` starts VR from a terminal instead of Envision.

## License

The tools in this repository: [GPL-3.0](LICENSE). The patched Monado (BSL-1.0) and xrizer (GPL-3.0) keep their
own licenses; the NVIDIA patches follow the open kernel modules' license (dual MIT/GPL-2.0).
