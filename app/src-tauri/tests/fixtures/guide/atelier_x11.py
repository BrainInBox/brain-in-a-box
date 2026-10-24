"""A program for the guide's capture tests on an X11 desktop (src/capture/x11.rs, the tests named
"bureau"): plain windows of known colours, nothing else, no data. Xlib through ctypes: nothing to
install but the X server's own library.

    atelier_x11.py fenetre <title> <x> <y> <w> <h> <rrggbb>            a window of that colour
    atelier_x11.py dessus  <title> <x> <y> <w> <h> <rrggbb>            the same, always on top
    atelier_x11.py modal   <title> <x> <y> <w> <h> <rrggbb> <rrggbb>   a window, then a modal dialog of
                                                                       the second colour over it
    atelier_x11.py intrus  <title> <x> <y> <w> <h> <rrggbb> <id>       a dialog of another program's
                                                                       window (WM_TRANSIENT_FOR)
    atelier_x11.py terminal <title> <x> <y> <w> <h> <rrggbb>           a window that says it is xterm

Each window's id is printed once it is shown. The program lives until it is killed.
"""
import ctypes
import os
import sys
import time
from ctypes import POINTER, Structure, c_char_p, c_int, c_long, c_uint, c_ulong, c_void_p

X = ctypes.cdll.LoadLibrary("libX11.so.6")
X.XOpenDisplay.restype = c_void_p
X.XOpenDisplay.argtypes = [c_char_p]
X.XDefaultScreen.argtypes = [c_void_p]
X.XRootWindow.restype = c_ulong
X.XRootWindow.argtypes = [c_void_p, c_int]
X.XCreateSimpleWindow.restype = c_ulong
X.XCreateSimpleWindow.argtypes = [c_void_p, c_ulong, c_int, c_int, c_uint, c_uint, c_uint, c_ulong, c_ulong]
X.XInternAtom.restype = c_ulong
X.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
X.XChangeProperty.argtypes = [c_void_p, c_ulong, c_ulong, c_ulong, c_int, c_int, c_void_p, c_int]
X.XSetTransientForHint.argtypes = [c_void_p, c_ulong, c_ulong]
X.XMapWindow.argtypes = [c_void_p, c_ulong]
X.XFlush.argtypes = [c_void_p]
X.XSync.argtypes = [c_void_p, c_int]


class SizeHints(Structure):
    _fields_ = [("flags", c_long), ("x", c_int), ("y", c_int), ("width", c_int), ("height", c_int),
                ("min_width", c_int), ("min_height", c_int), ("max_width", c_int), ("max_height", c_int),
                ("width_inc", c_int), ("height_inc", c_int), ("min_aspect", c_int * 2), ("max_aspect", c_int * 2),
                ("base_width", c_int), ("base_height", c_int), ("win_gravity", c_int)]


X.XSetWMNormalHints.argtypes = [c_void_p, c_ulong, POINTER(SizeHints)]

US_POSITION, US_SIZE = 1, 2
XA_ATOM, XA_CARDINAL, XA_STRING, XA_WM_CLASS = 4, 6, 31, 67


def main():
    mode, title = sys.argv[1], sys.argv[2]
    x, y, w, h = (int(v) for v in sys.argv[3:7])
    colors = sys.argv[7:]
    d = X.XOpenDisplay(None)
    if not d:
        sys.exit("no X display")
    root = X.XRootWindow(d, X.XDefaultScreen(d))

    def atom(name):
        return X.XInternAtom(d, name.encode(), 0)

    def atoms(w_, name, values):
        data = (c_ulong * len(values))(*[atom(v) for v in values])
        X.XChangeProperty(d, w_, atom(name), XA_ATOM, 32, 0, data, len(values))

    def window(name, at, color, klass=(b"atelier", b"Atelier")):
        wx, wy, ww, wh = at
        win = X.XCreateSimpleWindow(d, root, wx, wy, ww, wh, 0, 0, int(color, 16))
        utf8 = name.encode("utf-8")
        X.XChangeProperty(d, win, atom("_NET_WM_NAME"), atom("UTF8_STRING"), 8, 0, utf8, len(utf8))
        X.XChangeProperty(d, win, atom("WM_NAME"), XA_STRING, 8, 0, utf8, len(utf8))
        pid = (c_ulong * 1)(os.getpid())
        X.XChangeProperty(d, win, atom("_NET_WM_PID"), XA_CARDINAL, 32, 0, pid, 1)
        cls = klass[0] + b"\0" + klass[1] + b"\0"
        X.XChangeProperty(d, win, XA_WM_CLASS, XA_STRING, 8, 0, cls, len(cls))
        hints = SizeHints(flags=US_POSITION | US_SIZE, x=wx, y=wy, width=ww, height=wh)
        X.XSetWMNormalHints(d, win, ctypes.byref(hints))
        return win

    def show(win):
        X.XMapWindow(d, win)
        X.XSync(d, 0)
        print(win, flush=True)

    if mode == "fenetre":
        show(window(title, (x, y, w, h), colors[0]))
    elif mode == "dessus":
        win = window(title, (x, y, w, h), colors[0])
        atoms(win, "_NET_WM_STATE", ["_NET_WM_STATE_ABOVE"])
        show(win)
    elif mode == "terminal":
        show(window(title, (x, y, w, h), colors[0], (b"xterm", b"XTerm")))
    elif mode == "modal":
        main_ = window(title, (x, y, w, h), colors[0])
        show(main_)
        time.sleep(1)
        dialog = window(title + " (dialogue)", (x + w // 4, y + h // 4, w // 2, h // 2), colors[1])
        X.XSetTransientForHint(d, dialog, main_)
        atoms(dialog, "_NET_WM_WINDOW_TYPE", ["_NET_WM_WINDOW_TYPE_DIALOG"])
        atoms(dialog, "_NET_WM_STATE", ["_NET_WM_STATE_MODAL"])
        show(dialog)
    elif mode == "intrus":
        win = window(title, (x, y, w, h), colors[0])
        X.XSetTransientForHint(d, win, int(colors[1]))
        atoms(win, "_NET_WM_WINDOW_TYPE", ["_NET_WM_WINDOW_TYPE_DIALOG"])
        show(win)
    else:
        sys.exit("mode? " + mode)
    while True:
        X.XFlush(d)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
