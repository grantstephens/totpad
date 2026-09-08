#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Install files over the USB serial console, without mounting the drive.

boot.py hides CIRCUITPY unless a key is held at power-on, and while it is hidden
the filesystem is writable to the device rather than to the host. That is the
right default, but it means the normal copy-to-drive route is unavailable. This
pushes files through the raw REPL instead, which works in that state.

Needs pyserial on the host. The device must be running CircuitPython, not sitting
in the bootloader.

    tools/push_serial.py build/totp.mpy build/usage.mpy code.py
    tools/push_serial.py --exec "import gc; print(gc.mem_free())"
"""

import argparse
import base64
import os
import sys
import time

try:
    import serial
except ImportError:
    raise SystemExit("pyserial is required: pip install pyserial")

CHUNK = 256  # base64 characters per write, a multiple of 4


class Device:
    """A raw-REPL session. Buffers reads so nothing is dropped."""

    def __init__(self, port, timeout=0.3):
        self.serial = serial.Serial(port, 115200, timeout=timeout)
        self.buf = b""

    def read_until(self, needle, budget=30):
        deadline = time.time() + budget
        while needle not in self.buf:
            if time.time() > deadline:
                raise RuntimeError(
                    "timed out waiting for %r, last saw %r" % (needle, self.buf[-200:])
                )
            self.buf += self.serial.read(4096)
        head, _, self.buf = self.buf.partition(needle)
        return head

    def __enter__(self):
        self.serial.write(b"\x03")  # interrupt code.py
        time.sleep(0.3)
        self.serial.write(b"\x03")
        time.sleep(0.3)
        self.serial.read(200000)
        self.buf = b""
        self.serial.write(b"\x01")  # raw REPL
        self.read_until(b"raw REPL; CTRL-B to exit")
        self.read_until(b">")
        return self

    def __exit__(self, *exc):
        self.serial.write(b"\x02")  # friendly REPL
        self.serial.close()

    def run(self, code, budget=60):
        self.serial.write(code.encode() + b"\x04")
        self.read_until(b"OK", budget)
        out = self.read_until(b"\x04", budget)
        err = self.read_until(b"\x04", budget)
        self.read_until(b">", budget)
        if err.strip():
            raise RuntimeError(err.decode(errors="replace").strip())
        return out.decode(errors="replace").strip()

    def put(self, source, dest):
        data = open(source, "rb").read()
        self.run("import binascii")
        self.run("_f = open(%r, 'wb')" % dest)
        encoded = base64.b64encode(data).decode()
        for start in range(0, len(encoded), CHUNK):
            self.run("_f.write(binascii.a2b_base64(%r))" % encoded[start : start + CHUNK])
        self.run("_f.close()")
        landed = int(self.run("import os; print(os.stat(%r)[6])" % dest))
        if landed != len(data):
            raise RuntimeError(
                "%s: wrote %d bytes but device reports %d" % (dest, len(data), landed)
            )
        return landed

    def reload(self):
        """Leave the REPL and let code.py run again."""
        self.serial.write(b"\x02")
        time.sleep(0.2)
        self.serial.write(b"\x04")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("files", nargs="*", help="files to copy to the device root")
    parser.add_argument("-p", "--port", default="/dev/ttyACM0")
    parser.add_argument("-e", "--exec", dest="command", help="run a statement and print its output")
    parser.add_argument("--reload", action="store_true", help="restart code.py afterwards")
    args = parser.parse_args()

    if not args.files and not args.command:
        parser.error("give files to push, or --exec")

    with Device(args.port) as device:
        for path in args.files:
            size = device.put(path, "/" + os.path.basename(path))
            print("%-24s %6d bytes" % ("/" + os.path.basename(path), size))
        if args.command:
            print(device.run(args.command))
        if args.reload:
            device.reload()
            print("code.py restarted")


if __name__ == "__main__":
    main()
