#!/usr/bin/env python3
"""Runs a command in a terminal (a pty) as a person at the keyboard would: each question it
asks is answered by the first --answer whose words it contains, and everything it prints is
copied to this output. Linux and macOS (the field tests run install.sh this way on Linux).

    terminal.py --answer 'Voyage key:=' --answer 'Obsidian=y' -- ./install.sh

A question is a line that ends, without a newline, in "[y/N] " or "Voyage key: ". One that no
answer matches stops the run (exit 3): a new question is never answered blindly. An answer
never used means a question the user expected was not asked (exit 4). Otherwise the command's
own exit code.
"""
import argparse
import os
import pty
import re
import select
import signal
import sys
import time

QUESTION = re.compile(r"(\[y/N\] |Voyage key: )$")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--answer", action="append", default=[], metavar="WORDS=REPLY",
                   help="a question containing WORDS gets REPLY and Enter (REPLY may be empty)")
    p.add_argument("--timeout", type=int, default=3600, help="seconds before the command is stopped")
    p.add_argument("command", nargs="+")
    a = p.parse_args(argv)
    a.answers = []
    for item in a.answer:
        words, sep, reply = item.partition("=")
        if not sep or not words:
            p.error("--answer WORDS=REPLY: %r" % item)
        a.answers.append((words, reply))
    return a


def stop(pid):
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(2)
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def main(argv=None):
    a = parse(argv if argv is not None else sys.argv[1:])
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execvp(a.command[0], a.command)
        finally:
            os._exit(127)
    used, line, out = set(), "", sys.stdout.buffer
    end = time.monotonic() + a.timeout
    code = None
    while True:
        if time.monotonic() > end:
            print("\nterminal.py: still running after %d s, stopped" % a.timeout, flush=True)
            stop(pid)
            code = 124
            break
        ready, _, _ = select.select([fd], [], [], 1.0)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:  # Linux: EIO once the command and its children closed the terminal
            chunk = b""
        if not chunk:
            break
        out.write(chunk)
        out.flush()
        line = (line + ANSI.sub("", chunk.decode("utf-8", "replace"))).rsplit("\n", 1)[-1]
        if not QUESTION.search(line):
            continue
        match = next((i for i, (words, _) in enumerate(a.answers) if words in line), None)
        if match is None:
            print("\nterminal.py: no answer for the question %r: stopped" % line.strip(), flush=True)
            stop(pid)
            code = 3
            break
        used.add(match)
        # A person reads the question before typing. Answered at once, the reply can reach the
        # terminal before `read -s` turns its echo off: an echoed Enter adds a blank line, now and then.
        time.sleep(0.5)
        os.write(fd, (a.answers[match][1] + "\n").encode("utf-8"))
        print("\nterminal.py: answered %r to %r" % (a.answers[match][1], line.strip()), flush=True)
        line = ""
    _, status = os.waitpid(pid, 0)
    if code is None:
        code = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status >> 8
        missing = [w for i, (w, _) in enumerate(a.answers) if i not in used]
        if code == 0 and missing:
            print("terminal.py: never asked about %s" % ", ".join(repr(w) for w in missing), flush=True)
            code = 4
    return code


if __name__ == "__main__":
    sys.exit(main())
