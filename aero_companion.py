#!/usr/bin/env python3
"""aero_companion.py -- runs the Varjo Aero's helpers whenever Monado runs, however it was started (Envision, the
launcher, a terminal). Meant to run all the time as the systemd user service varjo-aero-companion.service.

When the headset powers up (its main MCU appears) -- the MCU resets both at power-up -- and when AERO_IPD_MM changes:
  lenses moved to the user's IPD (aero_lens.py, one retry), then the fan curve (aero_fans.py); one after the
  other, they share the main-MCU port. Not per Monado start: concurrent port users steal each other's replies.
                                                                                    AERO_LENS=off / AERO_FANS=off
When monado-service appears:
  1. audio: the headset becomes the default output (previous default restored when Monado stops)   AERO_AUDIO=off
  2. (waits for a lens/fan run still in progress)
  3. display watch (aero_displaywatch.py): status 3 = picture up. No 3 within LH discovery + AERO_PICTURE_WAIT
     (default 12) s = black start -> desktop notification; the launcher restarts Monado itself
  4. backlight: ONE frame AERO_BL_DELAY (10) s after the start (aero_backlight.py)                 AERO_BRIGHTNESS
  5. eye tracker (aero_eyetrack.py) once the picture is up, when gaze is on (the cameras re-attach at display start)
  6. headset side button (picture up): xr_ipdcalib.py -- IPD measured, lenses moved and saved, then the calibration
     dots; the display watch and eye tracker pause meanwhile (they share the MCU port / the cameras), and so does
     the desktop overlay (it would cover the dots)
  7. desktop overlay once the picture is up: WayVR (the desktop and a game list in VR, so the next game can be
     started without taking the headset off; double-tap left B = show/hide; its panels block game input only while
     pointed at). `wayvr` on PATH, or AERO_WAYVR=/path/to/wayvr. WayVR exits by itself when Monado stops.
                                                                                    AERO_DESKTOP=off
An IPD saved while Monado runs (settings window) moves the lenses at once; the display watch pauses meanwhile.
When it stops: helpers stopped, audio restored. AERO_CONTROLLERS_OFF (default 5) minutes later, if VR has not been
started again, the controllers still on are switched off (SteamVR's lighthouse_console `poweroff`, through the
headset's Watchman dongles; SteamVR does this when it exits, Monado does not). AERO_CONTROLLERS_OFF=off disables it,
AERO_LIGHTHOUSE_CONSOLE=/path overrides the tool. After the FIRST session that had a picture (once per user, marker
WELCOMED), a desktop notification points to the settings window, with an "Open Settings" button -- shown when the
user is back at the desktop, not while the headset is on. Envision has no button for it.

SAFETY: a monado-service that is not our Aero build (stock Monado, e.g. Envision in simple mode) shows the Aero's
panels upside down without lens correction -- painful to look at. With the headset connected, such a
Monado is stopped within a second, with a sound and a notification (Envision shows no log window). Our build is
recognised by a string only it contains (MARKER). AERO_ALLOW_OTHER_MONADO=1 turns the guard off.

Settings: ~/.config/monado/varjo-aero.conf (KEY=VALUE, the AERO_* names; environment wins). From them the companion
writes ~/.config/monado/varjo-aero-session.conf, which Monado reads (u_aero.h) -- in the service and inside every app,
Steam games included. Rewritten at start and whenever varjo-aero.conf changes.
Status for the launcher: $XDG_RUNTIME_DIR/aero-companion.status = idle | starting | picture | black."""
import glob, os, shutil, signal, subprocess, sys, threading, time

TOOLS = os.path.dirname(os.path.abspath(__file__))

CONF = os.path.expanduser("~/.config/monado/varjo-aero.conf")
WELCOMED = os.path.expanduser("~/.config/monado/varjo-aero-welcomed")
SETTINGS_APP = os.path.join(TOOLS, "aero_settings.py")
SESSION = os.environ.get("AERO_SESSION_CONF", os.path.expanduser("~/.config/monado/varjo-aero-session.conf"))
RUNDIR = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
STATUS = os.path.join(RUNDIR, "aero-companion.status")
LOGDIR = os.environ.get("AERO_LOG_DIR", "/tmp")
# Eye tracker interpreter (OpenCV): the companion's own when it runs from the installed venv, else the dev venv.
VENV_PY = os.environ.get("AERO_VENV_PY") or next((p for p in (sys.executable, os.path.join(TOOLS, "eyetrack-venv", "bin",
                                                   "python")) if subprocess.run([p, "-c", "import cv2"], capture_output=True,
                                                   check=False).returncode == 0), "")
MCU_GLOB = "/dev/serial/by-id/usb-Varjo_Technologies_Varjo_Headset_*-if00"
DC_GLOB = "/dev/serial/by-id/usb-Varjo_Technologies_Varjo_Display_Controller_*-if00"


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def settings():
    """varjo-aero.conf, then the environment on top."""
    s = {}
    try:
        with open(CONF) as f:
            for raw in f:
                line = raw.split("#", 1)[0].strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    s[k.strip()] = v.strip().strip('"')
    except FileNotFoundError:
        pass
    s.update({k: v for k, v in os.environ.items() if k.startswith("AERO_")})
    return s


def gaze_cal(s):
    return s.get("AERO_GAZE_CAL") or os.path.expanduser(f"~/.config/monado/varjo-aero-gaze-{os.environ.get('USER', 'user')}.json")


def gaze_on(s):
    return s.get("AERO_GAZE", "1" if os.path.exists(gaze_cal(s)) else "0") == "1"


def write_session(s):
    """The settings Monado reads (u_aero.h): inset/context sizes (quad-view apps) and the stereo size (plain two-view
    apps, most games) from the quality level, gaze, and pass-through."""
    gaze = gaze_on(s)
    viewset = "dynamic_foveation" if gaze else "quad"
    out = {"AERO_INSET_DEG": "15" if gaze else "30", "AERO_INSET_PX": "2156" if gaze else "2360",
           "AERO_GFX_QUAD": "1", "AERO_INSET_ROUND": "1"}
    r = subprocess.run([sys.executable, os.path.join(TOOLS, "aero_texsizes.py"), viewset, s.get("AERO_QUALITY", "VeryHigh")],
                       capture_output=True, text=True, check=False)
    if r.returncode == 0 and len(r.stdout.split()) == 2:
        ctx, ins = r.stdout.split()
        out["AERO_CONTEXT_PX"], out["AERO_INSET_PX"] = ctx, ins
    else:
        log(f"quality {s.get('AERO_QUALITY')!r} unknown -- default sizes")
    r = subprocess.run([sys.executable, os.path.join(TOOLS, "aero_texsizes.py"), "stereo", s.get("AERO_QUALITY", "VeryHigh")],
                       capture_output=True, text=True, check=False)
    if r.returncode == 0 and len(r.stdout.split()) == 2:
        out["AERO_STEREO_PX"] = r.stdout.split()[0]
    if gaze:
        out["AERO_GAZE_INSET"] = "1"
    for k in ("AERO_IPD_MM", "AERO_INSET_DEG", "AERO_INSET_PX", "AERO_CONTEXT_PX", "AERO_STEREO_PX", "AERO_INSET_EDGE",
              "AERO_INSET_ROUND", "AERO_GFX_QUAD", "AERO_SRGB_FIX", "AERO_SKIP_PROFILES", "AERO_DISTORTION"):
        if k in s:
            out[k] = s[k]                                    # explicit settings win over the derived ones
    tmp = SESSION + ".tmp"
    with open(tmp, "w") as f:
        f.write("# written by aero_companion.py -- do not edit; settings go in varjo-aero.conf\n")
        f.writelines(f"{k}={v}\n" for k, v in out.items())
    os.replace(tmp, SESSION)


def set_status(st):
    with open(STATUS + ".tmp", "w") as f:
        f.write(st + "\n")
    os.replace(STATUS + ".tmp", STATUS)


MARKER = b"Varjo Aero: using 2x"                          # logged by our lighthouse-driver patch (steamvr_lh.cpp)
_build_cache = {}


def monado_pids():
    r = subprocess.run(["pgrep", "-x", "monado-service"], capture_output=True, text=True, check=False)
    return [int(p) for p in r.stdout.split()]


def monado_binary(pid):
    """The binary a monado-service process runs. /proc/PID/exe is hidden for binaries with file capabilities (our
    build and stock Monado both carry cap_sys_nice), so fall back to argv[0] -- Envision and the launcher start
    Monado by its full path."""
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        pass
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv0 = f.read().split(b"\0")[0].decode(errors="replace")
    except OSError:
        return None
    if os.path.isabs(argv0):
        return argv0
    return shutil.which(argv0)


def is_aero_build(pid):
    """True if the monado-service binary contains our patch's marker string (cached per binary). Unknown = False:
    stopping a Monado we cannot identify is better than showing an uncorrected picture."""
    exe = monado_binary(pid)
    try:
        key = (exe, os.stat(exe).st_mtime)
    except (OSError, TypeError):
        log(f"cannot identify the binary of monado-service pid {pid}")
        return False
    if key not in _build_cache:
        found, tail = False, b""
        with open(exe, "rb") as f:
            while chunk := f.read(1 << 20):
                if MARKER in tail + chunk:
                    found = True
                    break
                tail = chunk[-len(MARKER):]
        _build_cache[key] = found
    return _build_cache[key]


def stop_wrong_monado(pid):
    exe = monado_binary(pid) or f"pid {pid}"
    log(f"WRONG MONADO: {exe} is not the Varjo Aero build -- stopping it (upside-down, uncorrected picture)")
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    set_status("wrong-monado")
    sound = "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"
    if shutil.which("paplay") and os.path.exists(sound):
        subprocess.Popen(["paplay", sound])
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-u", "critical", "-a", "Varjo Aero", "Varjo Aero: wrong Monado stopped",
                        f"{exe} is not the Varjo Aero build. In Envision, use the 'Varjo Aero' profile (profiles on)."],
                       check=False)


def runtime_json(exe):
    """The OpenXR runtime manifest that belongs to a monado-service binary (Envision prefix or a build tree)."""
    if not exe:
        return None
    d = os.path.dirname(exe)
    for cand in (os.path.join(d, "..", "share", "openxr", "1", "openxr_monado.json"),
                 os.path.join(d, "..", "..", "..", "..", "openxr_monado-dev.json")):
        if os.path.exists(cand):
            return os.path.realpath(cand)
    return None


def monado_running():
    return bool(monado_pids())


def first(pattern):
    m = glob.glob(pattern)
    return m[0] if m else None


def welcome():
    """Once per user, after the first VR session with a picture: say where the settings are. A thread: notify-send
    --action waits until the notification is clicked or dismissed."""
    if os.path.exists(WELCOMED) or not shutil.which("notify-send"):
        return
    with open(WELCOMED, "w") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S\n"))
    log("first VR session done: settings notification")

    def ask():
        r = subprocess.run(["notify-send", "-a", "Varjo Aero", "-i", "preferences-desktop-display",
                            "-A", "open=Open Settings", "Varjo Aero settings",
                            ("Quality, IPD, eye-tracked foveation and brightness: app menu -> Varjo Aero Settings "
                             "(or the command aero-settings). Side button in VR: measure IPD + eye calibration.")],
                           capture_output=True, text=True, check=False)
        if r.stdout.strip() == "open":
            # System Python: the settings window needs PyGObject/GTK 4, which the tools' venv does not have.
            subprocess.Popen(["/usr/bin/python3", SETTINGS_APP], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            log("settings window opened from the notification")
    threading.Thread(target=ask, daemon=True).start()


def lighthouse_console(s):
    """SteamVR's lighthouse_console (SteamVR need not run; it only has to be installed in a Steam library)."""
    if s.get("AERO_LIGHTHOUSE_CONSOLE"):
        return s["AERO_LIGHTHOUSE_CONSOLE"]
    rel = "steamapps/common/SteamVR/tools/lighthouse/bin/linux64/lighthouse_console"
    roots = [os.path.expanduser("~/.local/share/Steam"), os.path.expanduser("~/.steam/steam")]
    try:
        with open(os.path.expanduser("~/.local/share/Steam/steamapps/libraryfolders.vdf")) as f:
            roots += [line.split('"')[3] for line in f if line.strip().startswith('"path"')]
    except (OSError, IndexError):
        pass
    return next((os.path.join(r, rel) for r in roots if os.access(os.path.join(r, rel), os.X_OK)), None)


def lh_run(exe, commands, timeout=25):
    d = os.path.dirname(exe)
    r = subprocess.run([exe], input="\n".join(commands + ["quit"]) + "\n", capture_output=True, text=True,
                       cwd=d, env=dict(os.environ, LD_LIBRARY_PATH=d), timeout=timeout, check=False)
    return r.stdout + r.stderr


def controllers_off(s):
    """Power off every controller connected to a Watchman dongle (the Aero has two built in). Monado must not run:
    it holds the dongles."""
    exe = lighthouse_console(s)
    if not exe:
        log("controllers: lighthouse_console not found (SteamVR not installed?) -- left on")
        return
    try:
        out = lh_run(exe, [])
        lines = out.splitlines()
        i = next(k for k, l in enumerate(lines) if "Attached lighthouse receiver devices" in l)
        dongles = []
        for l in lines[i + 1:]:
            if not l.startswith("\t"):
                break
            if not l.strip().startswith("LHR-"):             # LHR-... = the headset's own receiver
                dongles.append(l.strip())
        for sn in dongles:
            if monado_running():
                log("controllers: VR started again -- left on")
                return
            probe = lh_run(exe, [f"serial {sn}", "sleep 3000"])
            ctrl = next((l.split(":")[1].strip().split()[0] for l in probe.splitlines()
                         if f"Connected to receiver {sn}" in l and "LHR-" in l and "lighthouse_console:" not in l), None)
            if ctrl is None:
                continue                                     # nothing connected to this dongle (already off)
            lh_run(exe, [f"serial {sn}", "sleep 3000", "poweroff", "sleep 1000"])
            log(f"controllers: {ctrl} switched off")
    except (StopIteration, subprocess.TimeoutExpired, OSError) as e:
        log(f"controllers: power-off failed ({e!r})")


def session_env():
    """os.environ plus the desktop session variables from the systemd user manager. The service can start before
    the desktop exports DISPLAY / WAYLAND_DISPLAY to systemd (GNOME and KDE do it themselves; on Hyprland or Sway add
    `dbus-update-activation-environment --systemd --all` to the autostart), so they are looked up at use time."""
    env = dict(os.environ)
    try:
        out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True,
                             timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return env
    for line in out.splitlines():
        k, _, v = line.partition("=")
        if k in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_CURRENT_DESKTOP", "HYPRLAND_INSTANCE_SIGNATURE",
                 "DBUS_SESSION_BUS_ADDRESS") and k not in env:
            env[k] = v
    return env


class HeadsetPrep:
    """Lens move + fan curve, once per headset power-up (and on an IPD change). One run at a time."""

    def __init__(self):
        self.dev = None                                      # tty of the main MCU we prepared (re-enumerates at power-up)
        self.ipd = None
        self.done = threading.Event()
        self.done.set()

    def check(self, s):
        port = first(MCU_GLOB)
        dev = os.path.realpath(port) if port else None
        ipd = s.get("AERO_IPD_MM")
        if dev is None:
            self.dev = None
            return
        if (dev != self.dev or ipd != self.ipd) and self.done.is_set():
            self.dev, self.ipd = dev, ipd
            self.done.clear()
            threading.Thread(target=self.run, args=(s, port, ipd), daemon=True).start()
            return True                                      # caller frees the MCU port (run() waits 2 s first)
        return False

    def run(self, s, port, ipd):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            time.sleep(2)                                    # the MCU needs a moment after enumerating
            if s.get("AERO_LENS", "on") != "off" and not ipd:
                log("AERO_IPD_MM not set in varjo-aero.conf -- lenses left where they are")
            elif s.get("AERO_LENS", "on") != "off":
                with open(os.path.join(LOGDIR, f"aero-companion-{stamp}.lens"), "w") as f:
                    for _ in range(2):                       # the MCU can drop a reply right after power-up
                        if subprocess.run(["timeout", "30", sys.executable, os.path.join(TOOLS, "aero_lens.py"), ipd,
                                           port], stdout=f, stderr=subprocess.STDOUT, check=False).returncode == 0:
                            log(f"lenses at {ipd} mm")
                            break
                        time.sleep(2)
                    else:
                        log("lens move failed twice")
            if s.get("AERO_FANS", "on") != "off":
                with open(os.path.join(LOGDIR, f"aero-companion-{stamp}.fans"), "w") as f:
                    ok = subprocess.run(["timeout", "10", sys.executable, os.path.join(TOOLS, "aero_fans.py"), port],
                                        stdout=f, stderr=subprocess.STDOUT, check=False).returncode == 0
                log("fan curve sent" if ok else "fan curve failed")
        finally:
            self.done.set()


class Session:
    """One Monado run: starts the helpers in order, stops them at the end."""

    def __init__(self, s, prep, monado_exe=None):
        self.s, self.t0, self.procs, self.prev_sink, self.prep = s, time.time(), {}, None, prep
        self.monado_exe = monado_exe
        self.picture = self.black = self.backlight_sent = self.gaze_started = self.desktop_started = False
        self.status_buf = ""
        self.ipdcalib = None                                 # running side-button sequence (Popen)
        self.status_paused = False                           # display watch stopped for a lens/fan run mid-session
        self.stamp = time.strftime("%Y%m%d-%H%M%S")
        self.limit = int(s.get("LH_DISCOVER_WAIT_MS", "9000")) / 1000 + float(s.get("AERO_PICTURE_WAIT", "12"))
        set_status("starting")
        log("Monado started")
        self.audio_to_headset()
        # The display watch shares the main-MCU port with a lens/fan run: start it after that, off the main loop.
        threading.Thread(target=self.mcu_sequence, daemon=True).start()

    def mcu_sequence(self):
        self.prep.done.wait(75)                              # a lens/fan run in progress owns the port
        self.spawn("status", [sys.executable, "-u", os.path.join(TOOLS, "aero_displaywatch.py"), "0"])

    def logfile(self, name):
        return open(os.path.join(LOGDIR, f"aero-companion-{self.stamp}.{name}"), "w")

    def spawn(self, name, cmd, env=None):
        self.procs[name] = subprocess.Popen(cmd, stdout=subprocess.PIPE if name == "status" else self.logfile(name),
                                            stderr=subprocess.STDOUT, start_new_session=True, env=env)
        if name == "status":
            os.set_blocking(self.procs[name].stdout.fileno(), False)

    def audio_to_headset(self):
        if self.s.get("AERO_AUDIO", "on") == "off" or not shutil.which("pactl"):
            return
        sinks = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, check=False).stdout
        aero = next((l.split()[1] for l in sinks.splitlines() if "Varjo_Headset_Audio" in l), None)
        cur = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, check=False).stdout.strip()
        if aero and aero != cur and subprocess.run(["pactl", "set-default-sink", aero], check=False).returncode == 0:
            self.prev_sink = cur
            log(f"audio -> headset (was {cur})")

    def stop_procs(self, *names):
        for name in names:
            p = self.procs.pop(name, None)
            if p is not None and p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:                # e.g. WayVR saving its state
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait(timeout=3)

    def pause_status(self):
        """A lens/fan run started mid-session (IPD saved in the settings window): it needs the main-MCU port, which
        the display watch holds -- two users of the port steal each other's replies and can wedge the MCU."""
        if "status" in self.procs:
            self.stop_procs("status")
            self.status_paused = True
            log("display watch paused for the lens move")

    def tick(self):
        if self.status_paused and self.prep.done.is_set() and self.ipdcalib is None:
            self.status_paused = False
            self.spawn("status", [sys.executable, "-u", os.path.join(TOOLS, "aero_displaywatch.py"), "0"])
            log("display watch resumed")
        p = self.procs.get("status")
        if p is not None and p.stdout is not None:
            try:
                chunk = os.read(p.stdout.fileno(), 4096).decode(errors="replace")
            except BlockingIOError:
                chunk = ""
            self.status_buf = (self.status_buf + chunk)[-200:]
            if "button press" in chunk and self.picture and self.ipdcalib is None:
                self.start_ipdcalib()
            if "display status 3" in self.status_buf and not self.picture:
                self.picture = True
                set_status("picture")
                log(f"picture up after {time.time() - self.t0:.0f} s")
        age = time.time() - self.t0
        if not self.backlight_sent and age >= float(self.s.get("AERO_BL_DELAY", "10")):
            self.backlight_sent = True
            port, level = first(DC_GLOB), self.s.get("AERO_BRIGHTNESS", "60")
            if level != "off" and port:
                self.spawn("backlight", ["timeout", "10", sys.executable, os.path.join(TOOLS, "aero_backlight.py"),
                                         port, level, "0"])
        if self.ipdcalib is not None and self.ipdcalib.poll() is not None:
            self.ipdcalib = None
            self.prep.ipd = settings().get("AERO_IPD_MM")    # the sequence moved the lenses itself
            self.spawn("status", [sys.executable, "-u", os.path.join(TOOLS, "aero_displaywatch.py"), "0"])
            self.gaze_started = False                        # restart the tracker with the new calibration
            self.desktop_started = False
            log("side-button sequence finished")
        if self.ipdcalib is None and self.picture and not self.gaze_started and gaze_on(settings()):
            self.gaze_started = True
            if VENV_PY:
                self.spawn("gaze", [VENV_PY, os.path.join(TOOLS, "aero_eyetrack.py"), "0", gaze_cal(self.s),
                                    "--no-recenter"])
                log("eye tracker started")
            else:
                log("eye tracker not available (no Python with OpenCV)")
        if self.ipdcalib is None and self.picture and not self.desktop_started:
            self.desktop_started = True
            self.start_desktop(settings())
        if not self.picture and not self.black and age > self.limit:
            self.black = True
            set_status("black")
            log("black start: no picture")
            if shutil.which("notify-send"):
                subprocess.run(["notify-send", "-a", "Varjo Aero", "Varjo Aero: no picture",
                                "The headset display did not come up. Stop VR and start it again."], check=False)

    def start_desktop(self, s):
        """WayVR as an OpenXR overlay on this Monado (its runtime manifest, whatever active_runtime.json says)."""
        if s.get("AERO_DESKTOP", "on") == "off":
            return
        exe = s.get("AERO_WAYVR") or shutil.which("wayvr")
        if not exe or not os.access(exe, os.X_OK):
            log("desktop overlay: WayVR not found (install it, or set AERO_WAYVR)")
            return
        env = session_env()
        rt = runtime_json(self.monado_exe)
        if rt:
            env["XR_RUNTIME_JSON"] = rt
        # --replace: take over from a copy started by hand; --show: visible at once (the user starts here)
        self.spawn("desktop", [exe, "--openxr", "--show", "--replace"], env=env)
        log(f"desktop overlay started ({exe})")

    def start_ipdcalib(self):
        """Side button: free the MCU port and the cameras, run the IPD + calibration sequence in the headset."""
        log("side button: IPD measurement + eye calibration")
        self.stop_procs("gaze", "status", "desktop")
        env = session_env()
        rt = runtime_json(self.monado_exe)
        if rt:
            env["XR_RUNTIME_JSON"] = rt
        self.ipdcalib = subprocess.Popen([sys.executable, os.path.join(TOOLS, "xr_ipdcalib.py")],
                                         stdout=self.logfile("ipdcalib"), stderr=subprocess.STDOUT, env=env,
                                         start_new_session=True)
        self.procs["ipdcalib"] = self.ipdcalib

    def stop(self):
        for p in self.procs.values():
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for p in self.procs.values():
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
        if self.prev_sink:
            subprocess.run(["pactl", "set-default-sink", self.prev_sink], check=False)
            log(f"audio restored ({self.prev_sink})")
        set_status("idle")
        log("Monado stopped")
        if self.picture:
            welcome()


def main():
    s = settings()
    write_session(s)
    set_status("idle")
    conf_mtime = os.path.getmtime(CONF) if os.path.exists(CONF) else 0
    session = None
    prep = HeadsetPrep()
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    controllers_off_at = None
    log(f"companion up (tools {TOOLS}, gaze {'on' if gaze_on(s) else 'off'})")
    while not stop:
        pids = monado_pids()
        running = bool(pids)
        s_now = settings()
        if (session is None or session.ipdcalib is None) and prep.check(s_now) and session is not None:
            session.pause_status()                           # the sequence moves the lenses itself, hence the guard
        if running and session is None and s_now.get("AERO_ALLOW_OTHER_MONADO") != "1" and first(MCU_GLOB):
            wrong = [p for p in pids if not is_aero_build(p)]
            for p in wrong:
                stop_wrong_monado(p)
            if wrong:
                time.sleep(0.5)
                continue
        if running and session is None:
            log("Monado is the Aero build: " + ", ".join(str(monado_binary(p)) for p in pids))
            session = Session(s_now, prep, monado_binary(pids[0]))
            controllers_off_at = None
        elif not running and session is not None:
            session.stop()
            session = None
            mins = s_now.get("AERO_CONTROLLERS_OFF", "5")
            if mins != "off":
                try:
                    controllers_off_at = time.time() + float(mins) * 60
                    log(f"controllers off in {float(mins):g} min unless VR starts again")
                except ValueError:
                    log(f"AERO_CONTROLLERS_OFF={mins!r} is not a number of minutes -- controllers left on")
        if not running and controllers_off_at is not None and time.time() >= controllers_off_at:
            controllers_off_at = None
            threading.Thread(target=controllers_off, args=(s_now,), daemon=True).start()
        if session is not None:
            session.tick()
        mt = os.path.getmtime(CONF) if os.path.exists(CONF) else 0
        if mt != conf_mtime:                                 # settings changed: Monado picks them up next start
            conf_mtime = mt
            write_session(settings())
            log("settings changed, session file rewritten")
        time.sleep(0.5)
    if session is not None:
        session.stop()


if __name__ == "__main__":
    main()
