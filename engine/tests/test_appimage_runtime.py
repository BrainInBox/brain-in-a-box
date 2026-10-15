"""The AppImage's runtime, the code that runs first on every double click: pinned in
.github/ci/appimage-tools.sh, given to appimagetool (LDAI_RUNTIME_FILE), and checked in every
AppImage built (.github/ci/appimage_runtime.py). Without it, appimagetool puts in the runtime of
type2-runtime's "continuous" release of the day, unchecked, into what release.yml publishes.

    python -I -m unittest discover -s engine/tests -v
"""
import importlib.util
import io
import re
import struct
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("appimage_runtime", ROOT / ".github" / "ci" / "appimage_runtime.py")
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)


def elf(sections):
    """A 64-bit little-endian ELF with these (name, bytes) sections, as type2-runtime's has them."""
    names = b"\0"
    body, heads = b"", [bytes(64)]
    for name, data in sections + [(".shstrtab", None)]:
        at = len(names)
        names += name.encode() + b"\0"
        if data is None:
            data = names
        heads.append(struct.pack("<IIQQQQIIQQ", at, 1, 0, 0, 64 + len(body), len(data), 0, 0, 1, 0))
        body += data
    head = bytearray(64)
    head[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", head, 0x28, 64 + len(body))
    struct.pack_into("<HHH", head, 0x3a, 64, len(heads), len(heads) - 1)
    return bytes(head) + body + b"".join(heads)


class Runtime(unittest.TestCase):
    RUNTIME = elf([(".text", b"runtime code" * 4), (".digest_md5", bytes(16)), (".upd_info", bytes(32)),
                   (".sha256_sig", bytes(32))])

    def image(self, change=None, digest=b"\x11" * 16, payload=b"hsqs squashfs of the app"):
        b = bytearray(self.RUNTIME)
        offset, _ = ar.sections(self.RUNTIME)[".digest_md5"]
        b[offset:offset + 16] = digest
        if change is not None:
            b[change] ^= 0xFF
        return bytes(b) + payload

    def test_the_image_starts_with_the_runtime_but_for_its_md5(self):
        self.assertIsNone(ar.first_difference(self.RUNTIME, self.image()))

    def test_any_other_byte_is_another_runtime(self):
        places = ar.sections(self.RUNTIME)
        for where in (0x30, places[".text"][0] + 5, places[".upd_info"][0], places[".sha256_sig"][0] + 31):
            self.assertEqual(ar.first_difference(self.RUNTIME, self.image(change=where)), where)
        self.assertEqual(ar.first_difference(self.RUNTIME, self.RUNTIME[:100]), 100)

    def test_a_file_that_is_no_runtime_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no .digest_md5 section"):
            ar.first_difference(elf([(".text", b"x")]), self.image())
        with self.assertRaisesRegex(ValueError, "not a 64-bit"):
            ar.sections(b"#!/bin/sh\n")

    def test_the_command_says_so_and_fails(self):
        tmp = Path(self._tmp())
        (tmp / "runtime").write_bytes(self.RUNTIME)
        for name, data, code, said in (("good.AppImage", self.image(), 0, "starts with the pinned runtime"),
                                       ("bad.AppImage", self.image(change=0x50), 1, "first difference at byte 80")):
            (tmp / name).write_bytes(data)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(ar.main([str(tmp / "runtime"), str(tmp / name)]), code)
            self.assertIn(said, out.getvalue())

    def _tmp(self):
        import shutil
        import tempfile
        d = tempfile.mkdtemp(prefix="appimage-runtime-")
        self.addCleanup(shutil.rmtree, d, True)
        return d


class Pins(unittest.TestCase):
    """The runtime is pinned like the tools, from a tagged release, and every workflow that builds an
    AppImage hands it to the build and checks what came out."""

    def test_the_runtime_is_pinned_from_a_tagged_release(self):
        text = (ROOT / ".github" / "ci" / "appimage-tools.sh").read_text(encoding="utf-8")
        pin = re.search(r"^runtime-x86_64 ([0-9a-f]{64}) (\S+)$", text, re.M)
        self.assertIsNotNone(pin)
        self.assertRegex(pin.group(2), r"^https://github\.com/AppImage/type2-runtime/releases/download/\d{8}/runtime-x86_64$")
        self.assertNotIn("/continuous/", text)
        self.assertIn('echo "LDAI_RUNTIME_FILE=$runtime" >> "$GITHUB_ENV"', text)

    def test_every_appimage_built_is_checked_and_its_tools_come_a_step_before(self):
        for name in ("release.yml", "test.yml", "terrain.yml"):
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            steps = re.split(r"\n      - ", text)
            fetch = [i for i, st in enumerate(steps) if re.search(r'appimage-tools\.sh"?\s*$', st, re.M)]
            build = [i for i, st in enumerate(steps) if "cargo tauri build" in st and "appimage" in st]
            check = [i for i, st in enumerate(steps) if re.search(r'appimage-tools\.sh"? check "\$', st)]
            self.assertTrue(fetch and check, name)
            # LDAI_RUNTIME_FILE reaches the steps after the one that wrote $GITHUB_ENV.
            for b in build or check:
                self.assertTrue(any(f < b for f in fetch), "%s: no tools before step %d" % (name, b))
                self.assertNotIn(b, fetch, "%s: the runtime would not reach this build" % name)
            self.assertEqual(len(fetch), len(check), name)


if __name__ == "__main__":
    unittest.main()
