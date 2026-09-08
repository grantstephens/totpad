# SPDX-License-Identifier: MIT
"""TOTP core for totpad.

Kept free of hardware imports so it runs on CPython (for the test suite) as
well as on CircuitPython. All of the expensive work happens once, in
``KeyStore.__init__``; generating a code afterwards is two SHA1 hashes.
"""

try:
    import os
except ImportError:  # pragma: no cover
    os = None

import json
import struct

# The native hashlib is C code and roughly an order of magnitude faster than
# adafruit_hashlib, but it is not in every CircuitPython build.
try:
    import hashlib

    hashlib.new("sha1", b"x").digest()

    def sha1(data):
        return hashlib.new("sha1", data).digest()

except (ImportError, ValueError, TypeError):  # pragma: no cover
    import adafruit_hashlib

    def sha1(data):
        return adafruit_hashlib.sha1(data).digest()


_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_B32_SKIP = "= \t-"
_BLOCK = 64  # SHA1 block size


def base32_decode(encoded):
    """Decode a base32 TOTP secret to bytes, ignoring case and padding."""
    out = bytearray()
    bits = 0
    bitbuff = 0
    for c in encoded.upper():
        n = _B32.find(c)
        if n < 0:
            if c in _B32_SKIP:
                continue
            raise ValueError("Not base32: " + c)
        bitbuff = (bitbuff << 5) | n
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append(bitbuff >> bits)
            bitbuff &= (1 << bits) - 1
    return bytes(out)


def hmac_pads(key):
    """Pre-expand a raw HMAC key into its inner and outer 64 byte pads."""
    if len(key) > _BLOCK:
        key = sha1(key)
    key = key + b"\0" * (_BLOCK - len(key))
    return (
        bytes(b ^ 0x36 for b in key),
        bytes(b ^ 0x5C for b in key),
    )


def find_config(root="/"):
    """Return the path of the single *.2fas backup in ``root``."""
    for entry in sorted(os.listdir(root)):
        if entry.endswith(".2fas"):
            return root + entry if root.endswith("/") else root + "/" + entry
    raise OSError("No *.2fas backup found in " + root)


def _label(svc, duplicated, name_width):
    """Build the on-screen label for one service.

    Accounts that share a service name get the account appended. The service
    name is clipped first, to leave room for enough of the account to tell them
    apart.
    """
    name = svc.get("name", "?").strip()
    if duplicated:
        otp = svc.get("otp", {})
        account = (otp.get("account") or otp.get("label") or "").strip()
        if account:
            name = "{}/{}".format(name[:8], account)
    return name[:name_width]


def load_keys(path, name_width=20):
    """Return [(label, ipad, opad, digits, period), ...] from a 2FAS backup.

    Sorted by label, ignoring case, so turning the knob walks the list in the
    order it reads. Non-TOTP entries are skipped.
    """
    with open(path, "r") as f:
        services = json.load(f)["services"]

    counts = {}
    for svc in services:
        n = svc.get("name", "?")
        counts[n] = counts.get(n, 0) + 1

    keys = []
    taken = {}
    for svc in services:
        otp = svc.get("otp", {})
        if otp.get("tokenType", "TOTP").upper() != "TOTP":
            continue  # HOTP and Steam guard are not supported
        secret = svc.get("secret")
        if not secret:
            continue

        label = _label(svc, counts.get(svc.get("name", "?"), 0) > 1, name_width)
        while label in taken:
            taken[label] += 1
            label = label[: name_width - 1] + str(taken[label])
        taken[label] = 1

        ipad, opad = hmac_pads(base32_decode(secret))
        keys.append(
            (label, ipad, opad, otp.get("digits") or 6, otp.get("period") or 30)
        )

    if not keys:
        raise ValueError("No TOTP keys in " + path)

    keys.sort(key=lambda k: k[0].lower())
    return keys


class KeyStore:
    """The loaded key set, with one cached code per key per time step."""

    def __init__(self, path=None, name_width=20, utc_offset=0, root="/"):
        self.path = path or find_config(root)
        self.keys = load_keys(self.path, name_width)
        self.utc_offset = utc_offset
        self._cache = {}

    def __len__(self):
        return len(self.keys)

    def label(self, index):
        return self.keys[index][0]

    def period(self, index):
        return self.keys[index][4]

    def code(self, index, unix_time):
        """Return the code for a key, computing it at most once per time step."""
        _, ipad, opad, digits, period = self.keys[index]
        counter = (unix_time - self.utc_offset * 3600) // period

        cached = self._cache.get(index)
        if cached and cached[0] == counter:
            return cached[1]

        mac = sha1(opad + sha1(ipad + struct.pack(">Q", counter)))
        offset = mac[-1] & 0x0F
        truncated = struct.unpack_from(">I", mac, offset)[0] & 0x7FFFFFFF

        otp = str(truncated % 10**digits)
        if len(otp) < digits:
            otp = "0" * (digits - len(otp)) + otp

        self._cache[index] = (counter, otp)
        return otp
