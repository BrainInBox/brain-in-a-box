"""An AppImage starts with the runtime it was built with (.github/ci/appimage-tools.sh pins it):
byte for byte, but for the section appimagetool writes into it, the image's MD5 (.digest_md5).
The others it could write stay as the runtime has them: ours are neither signed (.sha256_sig,
.sig_key) nor given update information (.upd_info).

    python3 -I appimage_runtime.py RUNTIME IMAGE
"""
import struct
import sys

WRITTEN = (".digest_md5",)


def sections(elf):
    """{name: (offset, size)} of a 64-bit little-endian ELF's sections."""
    if elf[:4] != b"\x7fELF" or elf[4:6] != b"\x02\x01":
        raise ValueError("not a 64-bit little-endian ELF")
    shoff, = struct.unpack_from("<Q", elf, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", elf, 0x3a)
    heads = [struct.unpack_from("<IIQQQQIIQQ", elf, shoff + i * shentsize) for i in range(shnum)]
    names = heads[shstrndx][4]
    out = {}
    for name_at, _type, _flags, _addr, offset, size, *_ in heads:
        start = names + name_at
        out[elf[start:elf.index(b"\0", start)].decode("ascii", "replace")] = (offset, size)
    return out


def first_difference(runtime, image):
    """None when `image` starts with `runtime` outside WRITTEN, else the first offset that differs."""
    places = sections(runtime)
    missing = [n for n in WRITTEN if n not in places]
    if missing:
        raise ValueError("no %s section: not an AppImage runtime" % ", ".join(missing))
    if len(image) < len(runtime):
        return len(image)
    a, b = bytearray(runtime), bytearray(image[:len(runtime)])
    for offset, size in (places[n] for n in WRITTEN):
        a[offset:offset + size] = b[offset:offset + size] = bytes(size)
    return None if a == b else next(i for i, (x, y) in enumerate(zip(a, b)) if x != y)


def main(argv):
    if len(argv) != 2:
        print("usage: appimage_runtime.py RUNTIME IMAGE", file=sys.stderr)
        return 2
    with open(argv[0], "rb") as f:
        runtime = f.read()
    with open(argv[1], "rb") as f:
        image = f.read()
    try:
        at = first_difference(runtime, image)
    except ValueError as e:
        print("::error::%s: %s" % (argv[0], e))
        return 1
    if at is not None:
        print("::error::%s does not start with the pinned runtime %s (first difference at byte %d of %d)"
              % (argv[1], argv[0], at, len(runtime)))
        return 1
    print("%s: starts with the pinned runtime (%d bytes)" % (argv[1], len(runtime)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
