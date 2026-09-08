# SPDX-License-Identifier: MIT
"""Tests for the totpad TOTP core.

Runs on CPython. The device code path is identical, apart from which SHA1
implementation totp.py picks up.
"""

import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import totp  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLE = os.path.join(os.path.dirname(HERE), "example.2fas.example")

# RFC 6238 appendix B, SHA1 column. Secret is "12345678901234567890".
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
RFC_VECTORS = (
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
)


def reference_totp(secret, unix_time, digits=6, period=30):
    """An independent implementation, via the standard library."""
    key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8))
    counter = unix_time // period
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    truncated = struct.unpack_from(">I", mac, offset)[0] & 0x7FFFFFFF
    return str(truncated % 10**digits).zfill(digits)


class TestBase32(unittest.TestCase):
    def test_matches_stdlib(self):
        for secret in (
            RFC_SECRET,
            "JBSWY3DPEHPK3PXP",
            "AAAAABBBBBCCCCCDDDDDEEEEEFFFFFGG",  # 32 chars, the common length
            "MZXW6===",
            "MZXW6",
        ):
            padded = secret.upper().rstrip("=")
            padded += "=" * ((8 - len(padded) % 8) % 8)
            self.assertEqual(
                totp.base32_decode(secret), base64.b32decode(padded), secret
            )

    def test_lowercase_and_separators(self):
        self.assertEqual(
            totp.base32_decode("jbswy3dpehpk3pxp"),
            totp.base32_decode("JBSW-Y3DP EHPK3PXP"),
        )

    def test_rejects_junk(self):
        with self.assertRaises(ValueError):
            totp.base32_decode("JBSW1809")


class TestHmacPads(unittest.TestCase):
    def test_pads_reproduce_hmac(self):
        for raw in (b"", b"short", b"x" * 64, b"y" * 200):
            ipad, opad = totp.hmac_pads(raw)
            msg = b"message"
            mine = totp.sha1(opad + totp.sha1(ipad + msg))
            self.assertEqual(mine, hmac.new(raw, msg, hashlib.sha1).digest(), raw[:8])


class TestRFC6238(unittest.TestCase):
    def test_vectors(self):
        store = totp.KeyStore(path=EXAMPLE)
        index = [i for i in range(len(store)) if store.label(i) == "RFC6238"][0]
        for unix_time, expected in RFC_VECTORS:
            self.assertEqual(store.code(index, unix_time), expected, unix_time)


class TestKeyStore(unittest.TestCase):
    def setUp(self):
        self.store = totp.KeyStore(path=EXAMPLE)

    def test_skips_non_totp(self):
        self.assertNotIn("CounterBased", [self.store.label(i) for i in range(len(self.store))])

    def test_sorted_case_insensitively(self):
        labels = [self.store.label(i) for i in range(len(self.store))]
        self.assertEqual(labels, sorted(labels, key=str.lower))

    def test_labels_unique_and_within_width(self):
        labels = [self.store.label(i) for i in range(len(self.store))]
        self.assertEqual(len(set(labels)), len(labels))
        for label in labels:
            self.assertLessEqual(len(label), 20, label)

    def test_duplicate_names_get_accounts(self):
        labels = [self.store.label(i) for i in range(len(self.store))]
        dupes = sorted(l for l in labels if l.startswith("Duplicat"))
        self.assertEqual(dupes, ["Duplicat/first@examp", "Duplicat/second@exam"])

    def test_agrees_with_reference(self):
        with open(EXAMPLE) as f:
            services = {s["name"]: s for s in json.load(f)["services"]}
        for index in range(len(self.store)):
            label = self.store.label(index)
            name = label.split("/")[0]
            svc = services.get(label) or services.get(name)
            if svc is None:
                continue
            digits = svc["otp"].get("digits") or 6
            period = svc["otp"].get("period") or 30
            for unix_time in (59, 1700000000, 1788861556):
                self.assertEqual(
                    self.store.code(index, unix_time),
                    reference_totp(svc["secret"], unix_time, digits, period),
                    label,
                )

    def test_honours_period(self):
        index = [i for i in range(len(self.store)) if self.store.label(i) == "sixty"][0]
        self.assertEqual(self.store.period(index), 60)
        # Same 60 second window, so the same code; the next window differs.
        self.assertEqual(self.store.code(index, 1700000000), self.store.code(index, 1700000030))
        self.assertNotEqual(self.store.code(index, 1700000000), self.store.code(index, 1700000060))

    def test_cache_is_per_time_step(self):
        calls = []
        original = totp.sha1

        def counting_sha1(data):
            calls.append(data)
            return original(data)

        totp.sha1 = counting_sha1
        try:
            store = totp.KeyStore(path=EXAMPLE)
            calls.clear()
            base = 1700000010  # exactly on a 30 second boundary
            first = store.code(0, base)
            self.assertEqual(len(calls), 2)  # inner and outer hash, once
            self.assertEqual(store.code(0, base + 29), first)
            self.assertEqual(len(calls), 2)  # served from cache
            self.assertNotEqual(store.code(0, base + 30), first)
            self.assertEqual(len(calls), 4)  # new step, recomputed
        finally:
            totp.sha1 = original

    def test_utc_offset_shifts_window(self):
        shifted = totp.KeyStore(path=EXAMPLE, utc_offset=1)
        self.assertEqual(shifted.code(0, 1700003600), self.store.code(0, 1700000000))


class TestLoadErrors(unittest.TestCase):
    def _write(self, payload):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".2fas", delete=False, dir=tempfile.mkdtemp()
        )
        json.dump(payload, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_empty_backup_raises(self):
        path = self._write({"services": []})
        with self.assertRaises(ValueError):
            totp.KeyStore(path=path)

    def test_entry_without_secret_skipped(self):
        path = self._write(
            {
                "services": [
                    {"name": "NoSecret", "otp": {"tokenType": "TOTP"}},
                    {"name": "Fine", "secret": "JBSWY3DPEHPK3PXP", "otp": {}},
                ]
            }
        )
        store = totp.KeyStore(path=path)
        self.assertEqual([store.label(i) for i in range(len(store))], ["Fine"])

    def test_find_config_locates_backup(self):
        path = self._write({"services": [{"name": "A", "secret": "JBSWY3DPEHPK3PXP", "otp": {}}]})
        root = os.path.dirname(path)
        self.assertEqual(totp.find_config(root), path)

    def test_find_config_without_backup(self):
        with self.assertRaises(OSError):
            totp.find_config(tempfile.mkdtemp())


if __name__ == "__main__":
    unittest.main()
