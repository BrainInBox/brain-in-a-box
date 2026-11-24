"""The libraries the AppImage leaves to the system, though linuxdeploy puts them in it.

The AppImage is built on Ubuntu 22.04 and leaves EGL, GL and Mesa to the system (linuxdeploy's
exclusions). Mesa's EGL needs libwayland-client.so.0: when the AppImage carries 22.04's (1.20), that
one is already loaded (WebKitGTK links it) and Mesa gets it instead of the system's. Mesa 26
(Ubuntu 26.04) needs symbols wayland 1.20 does not have (wl_display_create_queue_with_name,
wl_display_dispatch_queue_timeout, wl_fixes_interface): its EGL does not load and WebKit stops
(« Could not create default EGL display: EGL_BAD_PARAMETER. Aborting... »), the window empty.
Every system with EGL has libwayland-client (Mesa's EGL depends on it), newer than 22.04's or the
same: its ABI only grows.

    python3 -I appimage_libs.py check APPDIR    # fails when APPDIR/usr/lib holds one of them
    python3 -I appimage_libs.py trim APPDIR     # takes them out (their symlinks too)

appimage-tools.sh runs it on the AppImage it unpacks (`trim`, then `check`).
"""
import os
import sys

SYSTEM = ("libwayland-client.so.0",)


def stem(name):
    """libwayland-client.so.0.20.0 -> libwayland-client.so"""
    i = name.find(".so")
    return name[:i + 3] if i >= 0 else None


def carried(appdir):
    """The files and links under APPDIR/usr/lib that are one of SYSTEM (any version of its name)."""
    lib = os.path.join(appdir, "usr", "lib")
    if not os.path.isdir(lib):
        raise ValueError("%s: no usr/lib, not an unpacked AppImage" % appdir)
    stems = {stem(n) for n in SYSTEM}
    out = []
    for top, dirs, files in os.walk(lib):
        for n in files + [d for d in dirs if os.path.islink(os.path.join(top, d))]:
            if stem(n) in stems:
                out.append(os.path.join(top, n))
    return sorted(out)


def main(argv):
    if len(argv) != 2 or argv[0] not in ("check", "trim"):
        print("usage: appimage_libs.py check|trim APPDIR", file=sys.stderr)
        return 2
    what, appdir = argv
    try:
        found = carried(appdir)
    except ValueError as e:
        print("::error::%s" % e)
        return 1
    if what == "check":
        if found:
            print("::error::the AppImage carries what it must leave to the system (Mesa's EGL needs the "
                  "system's): %s" % ", ".join(os.path.relpath(p, appdir) for p in found))
            return 1
        print("%s: leaves %s to the system" % (appdir, ", ".join(SYSTEM)))
        return 0
    for p in found:
        os.remove(p)
        print("taken out: %s" % os.path.relpath(p, appdir))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
