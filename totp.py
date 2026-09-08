# SPDX-License-Identifier: MIT
"""TOTP, HOTP and Steam code generation for totpad, from a 2FAS backup.

Kept free of hardware imports so it runs on CPython (for the test suite) as
well as on CircuitPython. All of the expensive work happens once, in
``KeyStore.__init__``; generating a code afterwards is two hashes.
"""

try:
    import os
except ImportError:  # pragma: no cover
    os = None

import gc
import json
import struct

import envelope
from hashes import HASHERS, hmac_pads


DEFAULT_ALGORITHM = "SHA1"

# Steam guard codes use five characters of this alphabet instead of digits.
STEAM_ALPHABET = "23456789BCDFGHJKMNPQRTVWXY"
STEAM_DIGITS = 5

TOTP = "TOTP"
HOTP = "HOTP"
STEAM = "STEAM"

_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_B32_SKIP = "= \t-"


def base32_decode(encoded):
    """Decode a base32 secret to bytes, ignoring case and padding."""
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


def steam_encode(value):
    """Turn a truncated HMAC value into a five character Steam guard code."""
    out = ""
    for _ in range(STEAM_DIGITS):
        value, index = divmod(value, len(STEAM_ALPHABET))
        out += STEAM_ALPHABET[index]
    return out


class Key:
    """One account: its label and everything needed to produce a code."""

    def __init__(self, label, digest, ipad, opad, digits, period, kind, counter, group):
        self.label = label
        self.digest = digest
        self.ipad = ipad
        self.opad = opad
        self.digits = digits
        self.period = period
        self.kind = kind
        self.counter = counter  # HOTP only
        self.group = group

    @property
    def time_based(self):
        return self.kind != HOTP

    def at(self, counter):
        """Return the code for an explicit counter value."""
        mac = self.digest(self.opad + self.digest(self.ipad + struct.pack(">Q", counter)))
        offset = mac[-1] & 0x0F
        truncated = struct.unpack_from(">I", mac, offset)[0] & 0x7FFFFFFF

        if self.kind == STEAM:
            return steam_encode(truncated)

        code = str(truncated % 10**self.digits)
        if len(code) < self.digits:
            code = "0" * (self.digits - len(code)) + code
        return code


def find_config(root="/"):
    """Return the path of the backup in ``root``, preferring an encrypted one."""
    plain = None
    for entry in sorted(os.listdir(root)):
        path = root + entry if root.endswith("/") else root + "/" + entry
        if entry.endswith(".2fas.enc"):
            return path
        if entry.endswith(".2fas") and plain is None:
            plain = path
    if plain:
        return plain
    raise OSError("No *.2fas or *.2fas.enc backup found in " + root)


def _kind(otp):
    token = (otp.get("tokenType") or TOTP).upper()
    if "STEAM" in token:
        return STEAM
    if token == HOTP:
        return HOTP
    return TOTP


def _algorithm(otp, kind):
    """Pick a digest for a service, falling back when a build lacks one."""
    name = (otp.get("algorithm") or DEFAULT_ALGORITHM).upper()
    if kind == STEAM:
        name = DEFAULT_ALGORITHM  # Steam guard is always SHA1
    if name not in HASHERS:
        raise ValueError("Unsupported algorithm " + name)
    return HASHERS[name]


def is_encrypted(path):
    """True when a backup file is a totpad envelope rather than plain JSON."""
    with open(path, "rb") as f:
        return envelope.is_envelope(f.read(len(envelope.MAGIC)))


def _read_backup(path, key_file=None):
    """Return (services, group names by id) from a 2FAS backup file.

    An encrypted backup needs the device key file; a plaintext one is read as
    it is, so an unencrypted backup keeps working.
    """
    # One allocation, not two: bytearray(f.read()) would hold the file twice,
    # and a fragmented heap cannot always find two 20 KB blocks at once.
    gc.collect()
    try:
        size = os.stat(path)[6]
    except (AttributeError, OSError, IndexError):
        size = 0
    if size:
        raw = bytearray(size)
        with open(path, "rb") as f:
            f.readinto(raw)
    else:  # a filesystem that cannot report sizes
        with open(path, "rb") as f:
            raw = bytearray(f.read())

    if envelope.is_envelope(raw):
        if not key_file:
            raise ValueError("Backup is encrypted but no key file was given")
        gc.collect()
        # Decrypt inside the buffer just read, so the file is never held twice.
        raw = envelope.unseal_into(envelope.load_key(key_file), raw)

    try:
        data = json.loads(raw)
    except ValueError:
        raise ValueError("Backup is not valid JSON: " + path)
    except TypeError:  # a build whose json wants bytes rather than a view
        data = json.loads(bytes(raw))
    raw = None
    gc.collect()

    if "services" not in data or data.get("servicesEncrypted"):
        raise ValueError(
            "Backup is encrypted. Export it again with the password left blank; "
            "the device cannot run 2FAS's key derivation in reasonable time."
        )

    groups = {}
    for group in data.get("groups") or ():
        if group.get("id"):
            groups[group["id"]] = (group.get("name") or "").strip()
    return data["services"], groups


def _label(svc, groups, duplicated, name_width):
    """Build the on-screen label for one service.

    Accounts that share a service name get the account appended. The service
    name is clipped first, to leave room for enough of the account to tell them
    apart. Where there is no account, the 2FAS group name is used instead.
    """
    name = svc.get("name", "?").strip()
    if duplicated:
        otp = svc.get("otp", {})
        suffix = (otp.get("account") or otp.get("label") or "").strip()
        if not suffix:
            suffix = groups.get(svc.get("groupId"), "")
        if suffix:
            name = "{}/{}".format(name[:8], suffix)
    return name[:name_width]


def load_keys(path, name_width=20, key_file=None):
    """Return a list of Key, sorted by label, ignoring case.

    Entries whose algorithm this build cannot compute are skipped rather than
    taken down with the whole file.
    """
    services, groups = _read_backup(path, key_file)

    counts = {}
    for svc in services:
        n = svc.get("name", "?")
        counts[n] = counts.get(n, 0) + 1

    keys = []
    taken = {}
    for svc in services:
        secret = svc.get("secret")
        if not secret:
            continue
        otp = svc.get("otp", {})
        kind = _kind(otp)
        try:
            digest, block = _algorithm(otp, kind)
        except ValueError:
            continue

        label = _label(svc, groups, counts.get(svc.get("name", "?"), 0) > 1, name_width)
        while label in taken:
            taken[label] += 1
            label = label[: name_width - 1] + str(taken[label])
        taken[label] = 1

        ipad, opad = hmac_pads(base32_decode(secret), digest, block)
        keys.append(
            Key(
                label=label,
                digest=digest,
                ipad=ipad,
                opad=opad,
                digits=STEAM_DIGITS if kind == STEAM else (otp.get("digits") or 6),
                period=otp.get("period") or 30,
                kind=kind,
                counter=otp.get("counter") or 0,
                group=groups.get(svc.get("groupId"), ""),
            )
        )

    if not keys:
        raise ValueError("No usable keys in " + path)

    keys.sort(key=lambda k: k.label.lower())
    return keys


class KeyStore:
    """The loaded key set, with one cached code per key per counter value.

    HOTP counters advance only when a code is actually used, and are persisted
    separately from the backup so that reinstalling the backup does not rewind
    them.
    """

    def __init__(
        self,
        path=None,
        name_width=20,
        utc_offset=0,
        root="/",
        counter_file="/hotp.json",
        key_file=None,
    ):
        self.path = path or find_config(root)
        self.encrypted = is_encrypted(self.path)
        self.keys = load_keys(self.path, name_width, key_file)
        self.utc_offset = utc_offset
        self.counter_file = counter_file
        self.counters_writable = True
        self._cache = {}
        self._load_counters()

    def __len__(self):
        return len(self.keys)

    def label(self, index):
        return self.keys[index].label

    def period(self, index):
        return self.keys[index].period

    def kind(self, index):
        return self.keys[index].kind

    def group(self, index):
        return self.keys[index].group

    def time_based(self, index):
        return self.keys[index].time_based

    def _load_counters(self):
        """Restore HOTP counters, keeping whichever value is further ahead."""
        if not self.counter_file:
            return
        try:
            with open(self.counter_file, "r") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(saved, dict):
            return
        for key in self.keys:
            value = saved.get(key.label)
            if isinstance(value, int) and value > key.counter:
                key.counter = value

    def _save_counters(self):
        if not self.counter_file or not self.counters_writable:
            return False
        counters = {k.label: k.counter for k in self.keys if k.kind == HOTP}
        if not counters:
            return False
        try:
            with open(self.counter_file, "w") as f:
                json.dump(counters, f)
        except OSError:
            self.counters_writable = False  # drive is mounted on a host
            return False
        return True

    def _counter(self, index, unix_time):
        key = self.keys[index]
        if key.kind == HOTP:
            return key.counter
        return (unix_time - self.utc_offset * 3600) // key.period

    def code(self, index, unix_time):
        """Return the code for a key, computing it at most once per counter."""
        counter = self._counter(index, unix_time)
        cached = self._cache.get(index)
        if cached and cached[0] == counter:
            return cached[1]

        code = self.keys[index].at(counter)
        self._cache[index] = (counter, code)
        return code

    def used(self, index):
        """Note that a code was typed. Advances and persists HOTP counters."""
        key = self.keys[index]
        if key.kind != HOTP:
            return False
        key.counter += 1
        self._cache.pop(index, None)
        self._save_counters()
        return True
