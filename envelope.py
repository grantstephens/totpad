# SPDX-License-Identifier: MIT
"""Device-bound encryption for the 2FAS backup.

The backup is encrypted under a 32 byte key that lives only on the MacroPad, so
a copy of the file on its own -- synced to a phone, sitting in a host backup,
grabbed off a mounted drive -- is useless. It does not protect against someone
holding the device: the key and the ciphertext share one flash, and an RP2040
can be dumped over BOOTSEL. See the security section of README.md.

Layout, all fixed width but the ciphertext:

    b"TPAD" | version | nonce (16) | ciphertext | tag (32)

AES-256 in counter mode, then HMAC-SHA256 over everything before the tag.
Encrypt-then-MAC, and the tag is checked before any plaintext is produced.

Counter mode comes from aesio when its behaviour can be proven to match the
standard, which is checked at import with the AES-256-CTR known answer test from
NIST SP 800-38A. That is 30 times faster than doing the counter arithmetic in
Python, which matters because it runs at every boot. If the check ever fails,
for instance after a firmware change, the portable implementation below takes
over: slower, but the same answers, and no silent divergence.
"""

import os

from hashes import Hmac, hmac, equal

MAGIC = b"TPAD"
VERSION = 1
NONCE_BYTES = 16
TAG_BYTES = 32
KEY_BYTES = 32
HEADER_BYTES = len(MAGIC) + 1 + NONCE_BYTES

_ENC_INFO = b"totpad/aes-256-ctr/v1"
_MAC_INFO = b"totpad/hmac-sha256/v1"

# AES-256-CTR from NIST SP 800-38A F.5.5. The initial counter ends in 0xff, so
# these four blocks also exercise carry propagation between counter bytes.
KAT_KEY = bytes.fromhex(
    "603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
KAT_NONCE = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
KAT_PLAIN = bytes.fromhex(
    "6bc1bee22e409f96e93d7e117393172a"
    "ae2d8a571e03ac9c9eb76fac45af8e51"
    "30c81c46a35ce411e5fbc1191a0a52ef"
    "f69f2445df4f9b17ad2b417be66c3710")
KAT_CIPHER = bytes.fromhex(
    "601ec313775789a5b7a7f504bbf3d228"
    "f443e3ca4d62b59aca84e990cacaf5c5"
    "2b0930daa23de94ce87017ba2d84988d"
    "dfc9c58db67aada613c2dd08457941a6")

try:
    import aesio
except ImportError:
    aesio = None

try:
    from aes import AES as _Cipher  # pure Python block cipher, always present
except ImportError:  # pragma: no cover
    _Cipher = None



def new_key():
    """A fresh device key. Keep it on the device and nowhere public."""
    return os.urandom(KEY_BYTES)


def load_key(path):
    with open(path, "rb") as f:
        key = f.read(KEY_BYTES + 1)
    if len(key) != KEY_BYTES:
        raise ValueError(
            "Key file {} must be exactly {} bytes".format(path, KEY_BYTES)
        )
    return key


def derive(master):
    """Split the master key into separate encryption and authentication keys."""
    if len(master) != KEY_BYTES:
        raise ValueError("Master key must be %d bytes" % KEY_BYTES)
    return hmac(master, _ENC_INFO), hmac(master, _MAC_INFO)


MASK128 = (1 << 128) - 1
MAC_CHUNK = 512  # bytes fed to the MAC at a time


def ctr_crypt_into(key, nonce, buf):
    """XOR a mutable buffer with the AES-CTR keystream, in place.

    In place because the alternative is holding the ciphertext and the plaintext
    at once, and 20 KB twice over is more than the device can spare.
    """
    if FAST_CTR:
        # One C call for the whole buffer. Verified equivalent to the loop below
        # by the known answer test at import, and by test_envelope.py.
        aesio.AES(key, aesio.MODE_CTR, nonce).encrypt_into(buf, buf)
        return buf
    return _portable_ctr_into(key, nonce, buf)


def _portable_ctr_into(key, nonce, buf):
    """The same transform with the counter arithmetic in Python."""
    cipher = _Cipher(key)
    counter = int.from_bytes(nonce, "big")
    size = len(buf)
    for start in range(0, size, 16):
        block = cipher.encrypt_block((counter & MASK128).to_bytes(16, "big"))
        counter += 1
        end = start + 16
        if end > size:
            end = size
        width = end - start
        mixed = int.from_bytes(bytes(buf[start:end]), "big") ^ int.from_bytes(
            block[:width], "big"
        )
        buf[start:end] = mixed.to_bytes(width, "big")
    return buf


def ctr_crypt(key, nonce, data):
    """XOR data with the AES-CTR keystream, returning a new bytes. Its own inverse."""
    buf = bytearray(data)
    ctr_crypt_into(key, nonce, buf)
    return bytes(buf)


def is_envelope(blob):
    return blob[: len(MAGIC)] == MAGIC


def seal(master, plaintext, nonce=None):
    """Encrypt and authenticate plaintext under a master key."""
    enc_key, mac_key = derive(master)
    nonce = nonce or os.urandom(NONCE_BYTES)
    if len(nonce) != NONCE_BYTES:
        raise ValueError("Nonce must be %d bytes" % NONCE_BYTES)

    body = MAGIC + bytes([VERSION]) + nonce + ctr_crypt(enc_key, nonce, plaintext)
    return body + hmac(mac_key, body)


def unseal_into(master, buf):
    """Verify and decrypt in place, returning a memoryview of the plaintext.

    ``buf`` must be a bytearray holding the whole envelope; its ciphertext
    region is overwritten with plaintext. Nothing here copies the payload, so
    peak memory stays at one copy of the file.
    """
    if not is_envelope(buf):
        raise ValueError("Not a totpad envelope")
    if len(buf) < HEADER_BYTES + TAG_BYTES:
        raise ValueError("Envelope is truncated")
    version = buf[len(MAGIC)]
    if version != VERSION:
        raise ValueError("Unsupported envelope version %d" % version)

    enc_key, mac_key = derive(master)
    view = memoryview(buf)
    end = len(buf) - TAG_BYTES

    mac = Hmac(mac_key)
    for start in range(0, end, MAC_CHUNK):
        stop = start + MAC_CHUNK
        mac.update(view[start : stop if stop < end else end])
    if not equal(mac.digest(), bytes(view[end:])):
        raise ValueError(
            "Envelope failed authentication: wrong key file, or the file was "
            "modified"
        )

    nonce = bytes(view[len(MAGIC) + 1 : HEADER_BYTES])
    ctr_crypt_into(enc_key, nonce, view[HEADER_BYTES:end])
    return view[HEADER_BYTES:end]


def unseal(master, blob):
    """Verify and decrypt, returning bytes. Copies; see unseal_into."""
    buf = bytearray(blob)
    return bytes(unseal_into(master, buf))


# A nonce whose low word is saturated. SP 800-38A alone cannot tell a 128 bit
# counter from a 32 bit one, because its counter never overflows the low word,
# so an implementation that increments only the last four bytes would pass. This
# case diverges on the second block.
KAT_CARRY_NONCE = bytes(12) + b"\xff\xff\xff\xff"
KAT_CARRY_BLOCKS = 3


def _native_ctr_is_standard():
    """True when aesio's counter mode agrees with the portable implementation.

    Checked against the published vector, and against a carry case the published
    vector does not reach. A mismatch means the fast path would produce files the
    portable path cannot read, so it is refused.
    """
    if aesio is None or _Cipher is None:
        return False
    try:
        out = bytearray(len(KAT_PLAIN))
        aesio.AES(KAT_KEY, aesio.MODE_CTR, KAT_NONCE).encrypt_into(KAT_PLAIN, out)
        if bytes(out) != KAT_CIPHER:
            return False

        size = 16 * KAT_CARRY_BLOCKS
        expected = bytearray(size)
        _portable_ctr_into(KAT_KEY, KAT_CARRY_NONCE, expected)
        native = bytearray(size)
        aesio.AES(KAT_KEY, aesio.MODE_CTR, KAT_CARRY_NONCE).encrypt_into(bytes(size), native)
        return bytes(native) == bytes(expected)
    except Exception:  # any aesio surprise means use the portable path
        return False


FAST_CTR = _native_ctr_is_standard()
BACKEND = "aesio-ctr" if FAST_CTR else ("python-ctr" if _Cipher else "none")
