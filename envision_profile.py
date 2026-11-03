#!/usr/bin/env python3
"""envision_profile.py MONADO_REPO BRANCH ENV_FILE -- add (or update) the "Varjo Aero" profile in Envision's config.
An empty MONADO_REPO / BRANCH keeps the existing profile's (a re-install must not reset a working source, e.g.
replace a local checkout with the placeholder URL), else DEFAULT_REPO / DEFAULT_BRANCH. xrizer: the existing
profile's source if it has one, else XRIZER_REPO / XRIZER_BRANCH.

Envision has no profile import, so this edits ~/.config/envision/envision.json (user_profiles) directly, with Envision
closed and a backup next to it. Profile: Monado from MONADO_REPO/BRANCH (our patched Monado; Envision clones, builds and
sets CAP_SYS_NICE itself), lighthouse driver SteamVR, xrizer for OpenVR games, environment = the KEY=VALUE lines of
ENV_FILE (the exports of aero-env.sh). Field names: Envision 3.x src/profile.rs."""
import json, os, shutil, subprocess, sys, time

UUID = "varjo-aero"
FORKS = "https://github.com/Urganmcnurgan"                      # where the patched Monado and xrizer are published
DEFAULT_REPO = f"{FORKS}/monado.git"
DEFAULT_BRANCH = "varjo-aero"
XRIZER_REPO, XRIZER_BRANCH = f"{FORKS}/xrizer.git", "varjo-aero"   # eye-tracked foveation for OpenVR games
CONFIG = os.path.expanduser("~/.config/envision/envision.json")
DATA = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "envision")


def main():
    repo, branch, env_file = sys.argv[1:4]
    if subprocess.run(["pgrep", "-x", "envision"], capture_output=True, check=False).returncode == 0:
        sys.exit("envision_profile: close Envision first (it rewrites its config on exit)")
    cfg = {}
    if os.path.exists(CONFIG):
        shutil.copy2(CONFIG, f"{CONFIG}.before-varjo-aero-{time.strftime('%Y%m%d-%H%M%S')}")
        with open(CONFIG) as f:
            cfg = json.load(f)
    old = next((p for p in cfg.get("user_profiles", []) if p.get("uuid") == UUID), {})
    repo = repo or old.get("xrservice_repo") or DEFAULT_REPO
    branch = branch or old.get("xrservice_branch") or DEFAULT_BRANCH
    old_ovr = old.get("ovr_comp") or {}
    xr_repo = old_ovr.get("repo") or XRIZER_REPO
    xr_branch = old_ovr.get("branch") or XRIZER_BRANCH
    env = {}
    with open(env_file) as f:
        for line in f:
            if "=" in line:
                k, v = line.rstrip("\n").split("=", 1)
                env[k] = v
    env.setdefault("ORT_DISABLE_TELEMETRY", "1")          # Envision's own default for new profiles
    pdir = os.path.join(DATA, UUID)
    feature = lambda kind, name: {"feature_type": kind, "enabled": False, "path": os.path.join(pdir, name),  # noqa: E731
                                  "repo": None, "branch": None}
    profile = {
        "uuid": UUID, "name": "Varjo Aero",
        "xrservice_type": "Monado", "xrservice_path": os.path.join(pdir, "xrservice"),
        "xrservice_repo": repo, "xrservice_branch": branch, "xrservice_cmake_flags": {},
        "opencomposite_path": os.path.join(pdir, "opencomposite"), "opencomposite_repo": None,
        "opencomposite_branch": None,
        "ovr_comp": {"mod_type": "Xrizer", "repo": xr_repo, "branch": xr_branch, "path": os.path.join(pdir, "xrizer")},
        "features": {"libsurvive": feature("Libsurvive", "libsurvive"), "basalt": feature("Basalt", "basalt"),
                     "openhmd": feature("OpenHmd", "openhmd"), "mercury_enabled": False},
        "environment": env,
        "prefix": os.path.join(DATA, "prefixes", UUID),
        "is_system_profile": False, "editable": True, "pull_on_build": True,
        "lighthouse_driver": "SteamVR", "xrservice_launch_options": "", "skip_dependency_check": False,
    }
    profiles = [p for p in cfg.get("user_profiles", []) if p.get("uuid") != UUID]
    cfg["user_profiles"] = profiles + [profile]
    cfg["selected_profile_uuid"] = UUID
    # Envision's simple mode (profiles off) ignores every profile and starts stock Monado: on the Aero that is an
    # upside-down picture without lens correction (uncomfortable to look at). Profiles must be on.
    cfg["profiles_enabled"] = True
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG + ".tmp", "w") as f:
        json.dump(cfg, f, indent=1)
    os.replace(CONFIG + ".tmp", CONFIG)
    print(f"Envision profile 'Varjo Aero' ({repo} {branch}) written to {CONFIG} and selected")


if __name__ == "__main__":
    main()
