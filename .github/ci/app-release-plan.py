"""What .github/workflows/release.yml builds, as GITHUB_OUTPUT lines.

version  the app's version, from app/src-tauri/tauri.conf.json (MAJOR.MINOR.PATCH)
label    <version>-preview.<run number> when the run publishes, <version>-ci.<run number> otherwise
publish  true or false, from the PUBLISH environment variable the workflow sets
upload   true or false: whether the run leaves its installers as an artifact. Not for a pull
         request from another repository (HEAD_REPO is not REPOSITORY, or is gone): its build
         would sit in this repository's Actions tab under the app's own name
matrix   the platforms to build: macOS always; Windows once the Tauri config Windows
         reads declares an NSIS installer, that is when bundle.targets (from
         tauri.windows.conf.json if it sets them, else from tauri.conf.json)
         contains "nsis" or is "all"; Linux once the config Linux reads
         (tauri.linux.conf.json over tauri.conf.json) declares both an AppImage and
         a .deb ("appimage" and "deb", or "all"), on Ubuntu 22.04: what it builds
         runs on 22.04 and later (glibc, WebKitGTK 4.1)
"""
import json
import os
import re
import sys

CONF_DIR = os.path.join("app", "src-tauri")

MACOS = {"platform": "macos", "os": "macos-latest", "target": "universal-apple-darwin", "bundles": "dmg"}
WINDOWS = {"platform": "windows", "os": "windows-latest", "target": "x86_64-pc-windows-msvc", "bundles": "nsis"}
LINUX = {"platform": "linux", "os": "ubuntu-22.04", "target": "x86_64-unknown-linux-gnu", "bundles": "deb,appimage"}


def load(name):
    path = os.path.join(CONF_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def bundle_targets(conf, default):
    bundle = (conf or {}).get("bundle") or {}
    return bundle.get("targets", default)


def targets_for(base, platform_conf):
    # Tauri merges tauri.<platform>.conf.json over tauri.conf.json; a list replaces a list.
    targets = bundle_targets(platform_conf, bundle_targets(base, "all"))
    return [targets] if isinstance(targets, str) else targets


def builds_windows(base, windows_conf):
    targets = targets_for(base, windows_conf)
    return "all" in targets or "nsis" in targets


def builds_linux(base, linux_conf):
    targets = targets_for(base, linux_conf)
    return "all" in targets or ("appimage" in targets and "deb" in targets)


def main():
    base = load("tauri.conf.json")
    version = base.get("version", "")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        sys.exit(f"tauri.conf.json: version {version!r} is not MAJOR.MINOR.PATCH")
    run = os.environ["GITHUB_RUN_NUMBER"]
    if not run.isdigit():
        sys.exit(f"GITHUB_RUN_NUMBER {run!r} is not a number")
    publish = os.environ.get("PUBLISH") == "true"
    head, repo = os.environ.get("HEAD_REPO", ""), os.environ.get("REPOSITORY", "")
    upload = os.environ.get("EVENT_NAME") != "pull_request" or (head != "" and head == repo)

    include = [MACOS]
    if builds_windows(base, load("tauri.windows.conf.json")):
        include.append(WINDOWS)
    if builds_linux(base, load("tauri.linux.conf.json")):
        include.append(LINUX)

    print(f"version={version}")
    print(f"label={version}-{'preview' if publish else 'ci'}.{run}")
    print(f"publish={'true' if publish else 'false'}")
    print(f"upload={'true' if upload else 'false'}")
    print("matrix=" + json.dumps({"include": include}, separators=(",", ":")))


if __name__ == "__main__":
    main()
