# SPDX-License-Identifier: MIT
"""Hash backends shared by the code generator and the backup envelope.

The native hashlib is C code and roughly an order of magnitude faster than
adafruit_hashlib, but CircuitPython builds only carry some algorithms:
10.3.0 on the MacroPad has sha1 and sha256, so sha512 falls back to pure Python.
"""

BLOCK_SIZES = {"SHA1": 64, "SHA256": 64, "SHA512": 128}


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


def hmac(key, message, algorithm="SHA256"):
    """One-shot HMAC. For repeated use, pre-expand the pads instead."""
    digest, block = HASHERS[algorithm]
    ipad, opad = hmac_pads(key, digest, block)
    return digest(opad + digest(ipad + message))


def equal(a, b):
    """Compare two byte strings without leaking where they differ in timing."""
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= x ^ y
    return diff == 0
