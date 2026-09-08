# SPDX-License-Identifier: MIT
"""Hash backends shared by the code generator and the backup envelope.

The native hashlib is C code and roughly an order of magnitude faster than
adafruit_hashlib, but CircuitPython builds only carry some algorithms:
10.3.0 on the MacroPad has sha1 and sha256, so sha512 falls back to pure Python.
"""

BLOCK_SIZES = {"SHA1": 64, "SHA256": 64, "SHA512": 128}


def _factories():
    """Map algorithm name to (incremental hash factory, HMAC block size)."""
    try:
        import hashlib as native
    except ImportError:  # pragma: no cover
        native = None
    try:
        import adafruit_hashlib as fallback
    except ImportError:
        fallback = None

    def native_new(name):
        def new():
            return native.new(name)

        return new

    def fallback_new(name):
        def new():
            return getattr(fallback, name)()

        return new

    found = {}
    for name, block in BLOCK_SIZES.items():
        for module, factory in ((native, native_new), (fallback, fallback_new)):
            if module is None:
                continue
            try:
                probe = factory(name.lower())
                obj = probe()
                obj.update(b"probe")
                obj.digest()
            except (AttributeError, ValueError, TypeError):
                continue
            found[name] = (probe, block)
            break
    return found


NEW = _factories()


def _backends():
    try:
        import hashlib as native
    except ImportError:  # pragma: no cover
        native = None
    try:
        import adafruit_hashlib as fallback
    except ImportError:
        fallback = None

    def native_digest(name):
        def digest(data):
            return native.new(name, data).digest()

        return digest

    def fallback_digest(name):
        def digest(data):
            return getattr(fallback, name)(data).digest()

        return digest

    found = {}
    for name, block in BLOCK_SIZES.items():
        for module, factory in ((native, native_digest), (fallback, fallback_digest)):
            if module is None:
                continue
            try:
                digest = factory(name.lower())
                digest(b"probe")
            except (AttributeError, ValueError, TypeError):
                continue
            found[name] = (digest, block)
            break
    return found


HASHERS = _backends()


def hmac_pads(key, digest, block=64):
    """Pre-expand a raw HMAC key into its inner and outer pads."""
    if len(key) > block:
        key = digest(key)
    key = key + b"\0" * (block - len(key))
    return (
        bytes(b ^ 0x36 for b in key),
        bytes(b ^ 0x5C for b in key),
    )


class Hmac:
    """Incremental HMAC.

    Feeding the message in pieces matters on the device: concatenating a pad
    onto a 20 KB backup needs a second 20 KB allocation, which an RP2040 with a
    display and a dozen libraries loaded does not have to spare.
    """

    def __init__(self, key, algorithm="SHA256"):
        factory, block = NEW[algorithm]
        digest, _ = HASHERS[algorithm]
        self._factory = factory
        self._ipad, self._opad = hmac_pads(key, digest, block)
        self._inner = factory()
        self._inner.update(self._ipad)

    def update(self, data):
        self._inner.update(data)
        return self

    def digest(self):
        outer = self._factory()
        outer.update(self._opad)
        outer.update(self._inner.digest())
        return outer.digest()


def hmac(key, message, algorithm="SHA256"):
    """One-shot HMAC, streamed so no copy of the message is made."""
    return Hmac(key, algorithm).update(message).digest()


def equal(a, b):
    """Compare two byte strings without leaking where they differ in timing."""
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= x ^ y
    return diff == 0
