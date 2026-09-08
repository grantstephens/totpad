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

AES is used strictly as a block cipher here, with the counter arithmetic and the
XOR done in this file. aesio does have a counter mode of its own, but relying on
it would mean trusting its counter semantics to match the host's byte for byte;
driving ECB from shared code removes that question.
"""

import os

from hashes import hmac, equal

MAGIC = b"TPAD"
VERSION = 1
NONCE_BYTES = 16
TAG_BYTES = 32
KEY_BYTES = 32
HEADER_BYTES = len(MAGIC) + 1 + NONCE_BYTES

_ENC_INFO = b"totpad/aes-256-ctr/v1"
_MAC_INFO = b"totpad/hmac-sha256/v1"

try:
    import aesio

    class _Cipher:
        """AES block encryption via the native aesio module."""

        def __init__(self, key):
            self._aes = aesio.AES(key, aesio.MODE_ECB)
            self._out = bytearray(16)

        def encrypt_block(self, block):
            self._aes.encrypt_into(block, self._out)
            return bytes(self._out)

    BACKEND = "aesio"

except ImportError:
    from aes import AES as _Cipher  # pure Python, for the host and the tests

    BACKEND = "python"


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


def ctr_crypt(key, nonce, data):
    """XOR data with the AES-CTR keystream. Its own inverse."""
    cipher = _Cipher(key)
    counter = int.from_bytes(nonce, "big")
    out = bytearray(len(data))
    for start in range(0, len(data), 16):
        block = cipher.encrypt_block((counter & ((1 << 128) - 1)).to_bytes(16, "big"))
        counter += 1
        chunk = data[start : start + 16]
        size = len(chunk)
        mixed = int.from_bytes(chunk, "big") ^ int.from_bytes(block[:size], "big")
        out[start : start + size] = mixed.to_bytes(size, "big")
    return bytes(out)


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


def unseal(master, blob):
    """Verify and decrypt, raising ValueError on anything unexpected."""
    if not is_envelope(blob):
        raise ValueError("Not a totpad envelope")
    if len(blob) < HEADER_BYTES + TAG_BYTES:
        raise ValueError("Envelope is truncated")
    version = blob[len(MAGIC)]
    if version != VERSION:
        raise ValueError("Unsupported envelope version %d" % version)

    enc_key, mac_key = derive(master)
    body, tag = blob[:-TAG_BYTES], blob[-TAG_BYTES:]
    if not equal(hmac(mac_key, body), tag):
        raise ValueError(
            "Envelope failed authentication: wrong key file, or the file was "
            "modified"
        )
    return ctr_crypt(enc_key, body[len(MAGIC) + 1 : HEADER_BYTES], body[HEADER_BYTES:])
