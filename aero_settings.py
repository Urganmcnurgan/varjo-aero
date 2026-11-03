#!/usr/bin/env python3
"""aero_settings.py -- "Varjo Aero" settings window: quality, IPD, eye-tracked foveation, brightness, VR desktop.

Edits ~/.config/monado/varjo-aero.conf (other lines and comments are kept). The companion rewrites Monado's session
file when the file changes. When each value applies: IPD at once (the companion moves the lenses, Monado re-reads the
render IPD every second); quality when an app starts (Monado's client library reads the session file at
xrCreateInstance, even while the service keeps running); foveation and brightness at the next VR start.
The headset's side button also writes AERO_IPD_MM; Save keeps that value unless the IPD field was changed here.
Runs with the system Python (PyGObject, GTK 4).
The lens range comes from the headset's own factory table (~/.config/monado/varjo-aero-ipd-calibration.json)."""
import json, os, re, sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

CONF = os.path.expanduser("~/.config/monado/varjo-aero.conf")
IPD_TABLE = os.path.expanduser("~/.config/monado/varjo-aero-ipd-calibration.json")
GAZE_CAL = os.path.expanduser(f"~/.config/monado/varjo-aero-gaze-{os.environ.get('USER', 'user')}.json")
# The Aero's quality levels (the names users know from Varjo Base), pixels per degree in the foveated centre.
QUALITY = [("VeryLow", "Very low", 27), ("Low", "Low", 29), ("Fair", "Fair", 30), ("Medium", "Medium", 32),
           ("High", "High", 33), ("VeryHigh", "Very high (Varjo's default)", 35),
           ("ExtremelyHigh", "Extremely high", 37), ("UltraHigh", "Ultra high", 39)]


APPLY_NOTE = ("IPD applies at once (the lenses move). Quality applies when you start the game again. "
              "Foveation, brightness and the VR desktop apply at the next VR start.")


def read_conf():
    vals = {}
    try:
        with open(CONF) as f:
            for raw in f:
                line = raw.split("#", 1)[0].strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return vals


def write_conf(updates):
    """Set KEY=VALUE for each update (None = remove), keeping every other line and trailing comments."""
    try:
        lines = open(CONF).read().splitlines()
    except FileNotFoundError:
        lines = ["# Varjo Aero settings (KEY=VALUE). Read by the companion service and the launcher."]
    done = set()
    out = []
    for line in lines:
        m = re.match(r"\s*([A-Z_]+)\s*=", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            if updates[key] is not None and key not in done:
                comment = line[line.index("#"):] if "#" in line else ""
                out.append(f"{key}={updates[key]}" + (f"   {comment}" if comment else ""))
            done.add(key)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in done and v is not None]
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    with open(CONF + ".tmp", "w") as f:
        f.write("\n".join(out) + "\n")
    os.replace(CONF + ".tmp", CONF)


def ipd_range():
    try:
        pts = json.load(open(IPD_TABLE))["ipd_calibration"]
        mm = [p["ipd"] for p in pts]
        return min(mm), max(mm)
    except (OSError, KeyError, ValueError):
        return 58.5, 66.9                                    # a typical headset's table, as a fallback


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Varjo Aero")
        self.set_default_size(460, -1)
        v = read_conf()
        grid = Gtk.Grid(column_spacing=16, row_spacing=14, margin_top=20, margin_bottom=20, margin_start=20,
                        margin_end=20)
        self.set_child(grid)

        def label(text, row, sub=None):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.append(Gtk.Label(label=text, xalign=0))
            if sub:
                s = Gtk.Label(label=sub, xalign=0, wrap=True, max_width_chars=28)
                s.add_css_class("dim-label")
                box.append(s)
            grid.attach(box, 0, row, 1, 1)

        label("Quality", 0, "Render resolution for every app, Varjo Base's levels")
        self.quality = Gtk.DropDown.new_from_strings([f"{name} -- {ppd} PPD" for _, name, ppd in QUALITY])
        keys = [k for k, _, _ in QUALITY]
        self.quality.set_selected(keys.index(v.get("AERO_QUALITY", "VeryHigh")) if v.get("AERO_QUALITY") in keys
                                  else keys.index("VeryHigh"))
        grid.attach(self.quality, 1, 0, 1, 1)

        lo, hi = ipd_range()
        label("IPD (mm)", 1, f"Your pupil distance; lenses reach {lo:.1f}-{hi:.1f}")
        self.ipd = Gtk.SpinButton.new_with_range(lo, hi, 0.1)
        self.ipd.set_digits(1)
        try:
            self.ipd.set_value(float(v.get("AERO_IPD_MM", "63.0")))
        except ValueError:
            self.ipd.set_value(63.0)
        self.ipd_shown = self.ipd.get_value()                # to tell a side-button measurement from an edit here
        grid.attach(self.ipd, 1, 1, 1, 1)

        have_cal = os.path.exists(GAZE_CAL)
        label("Eye-tracked foveation", 2, "Sharpest where you look" if have_cal
              else "Needs an eye calibration first (headset side button in VR, or aero-calibrate)")
        self.gaze = Gtk.Switch(halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
        self.gaze.set_active(have_cal and v.get("AERO_GAZE", "1") != "0")
        self.gaze.set_sensitive(have_cal)
        self.have_cal = have_cal                             # without a calibration the switch is not the user's choice
        grid.attach(self.gaze, 1, 2, 1, 1)

        label("Brightness", 3, "Backlight; Varjo's default is 60")
        self.bright = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.bright.set_hexpand(True)
        b = v.get("AERO_BRIGHTNESS", "60")
        self.bright.set_value(float(b) if b.isdigit() else 60)
        self.bright_conf = b                                 # e.g. "off": kept unless the slider is moved
        self.bright_moved = False
        self.bright.connect("value-changed", lambda *_: setattr(self, "bright_moved", True))
        self.bright.set_draw_value(True)
        grid.attach(self.bright, 1, 3, 1, 1)

        label("Desktop in VR", 4, "Your desktop and game list in the headset (WayVR); double-tap left B to show or hide")
        self.desktop = Gtk.Switch(halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
        self.desktop.set_active(v.get("AERO_DESKTOP", "on") != "off")
        grid.attach(self.desktop, 1, 4, 1, 1)

        self.status = Gtk.Label(label=APPLY_NOTE, xalign=0, wrap=True)
        self.status.add_css_class("dim-label")
        grid.attach(self.status, 0, 5, 2, 1)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self.on_save)
        buttons.append(close)
        buttons.append(save)
        grid.attach(buttons, 0, 6, 2, 1)

    def on_save(self, *_):
        ipd, kept = f"{self.ipd.get_value():.1f}", ""
        try:
            on_disk = float(read_conf().get("AERO_IPD_MM", ""))
        except ValueError:
            on_disk = None
        # The side button may have measured a new IPD while this window was open: keep it unless the field was edited.
        edited_here = abs(self.ipd.get_value() - self.ipd_shown) >= 0.05
        if on_disk is not None and not edited_here and abs(on_disk - self.ipd_shown) >= 0.05:
            ipd, kept = f"{on_disk:.1f}", f" Kept the IPD measured in the headset ({on_disk:.1f} mm)."
            self.ipd.set_value(on_disk)
        self.ipd_shown = float(ipd)
        updates = {
            "AERO_QUALITY": QUALITY[self.quality.get_selected()][0],
            "AERO_IPD_MM": ipd,
            "AERO_DESKTOP": "on" if self.desktop.get_active() else "off",
        }
        if self.have_cal:                                    # else a later calibration would stay switched off
            updates["AERO_GAZE"] = "1" if self.gaze.get_active() else "0"
        if self.bright_moved or self.bright_conf.isdigit():
            updates["AERO_BRIGHTNESS"] = str(int(self.bright.get_value()))
        write_conf(updates)
        self.status.set_label("Saved." + kept + " " + APPLY_NOTE)


def main():
    app = Gtk.Application(application_id="io.github.varjo_aero.Settings")
    app.connect("activate", lambda a: Window(a).present())
    sys.exit(app.run(sys.argv[:1]))


if __name__ == "__main__":
    main()
