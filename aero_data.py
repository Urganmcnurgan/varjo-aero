"""aero_data.py -- where the Aero tools find their data files, and loaders for the shipped ones.

data_path(NAME): the user's own copy in ~/.config/monado/NAME if it exists, else the copy shipped in data/ next to this
file. The shipped data is the same for every Aero:
  varjo-aero-fans.bin              fan curves (main-MCU 0x74 payload)                 (aero_fans.py)
  varjo-aero-backlight-mask.bin    lens-shaped backlight mask, 0/1 per channel        (aero_backlight.py)
  varjo-aero-texture-sizes.json    per-quality render sizes                           (aero_texsizes.py)
  eyecam-init.txt                  eye-camera setup commands, hex, one per line       (aero_eyecam.py, aero_eyetrack.py)
  glint-leds.txt                   glint-LED frames to the main MCU: "SECONDS HEX"    (aero_eyecam.py, aero_eyetrack.py)
Per-headset data (lens distortion, IPD table, eye calibration) is never shipped: aero_storage.py reads it from the
headset, the gaze calibration is made per user."""
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
USER_DIR = os.path.expanduser("~/.config/monado")


def data_path(name):
    user = os.path.join(USER_DIR, name)
    return user if os.path.exists(user) else os.path.join(DATA_DIR, name)


def eyecam_init():
    """Eye-camera setup commands (UVC extension unit SET_CUR payloads), in the order they are sent."""
    with open(data_path("eyecam-init.txt")) as f:
        return [bytes.fromhex(line.split("#")[0].strip()) for line in f if line.split("#")[0].strip()]


def glint_frames():
    """[(seconds after the first frame, frame bytes)]: the four glint-LED patterns and their timing."""
    out = []
    with open(data_path("glint-leds.txt")) as f:
        for line in f:
            line = line.split("#")[0].strip()
            if line:
                t, h = line.split()
                out.append((float(t), bytes.fromhex(h)))
    return out
