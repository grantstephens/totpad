# SPDX-License-Identifier: MIT
"""Tests for the totpad code generation core.

Runs on CPython. The device path is identical apart from which hash
implementation totp.py picks up for each algorithm.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "example.2fas.example")
ENCRYPTED = os.path.join(ROOT, "encrypted.2fas.example")

# RFC 6238 appendix B. Seeds are ASCII "12345678901234567890..." truncated to
# the digest's key length, and all codes are 8 digits.
RFC6238 = {
    "SHA1": [(59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
             (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130")],
    "SHA256": [(59, "46119246"), (1111111109, "68084774"), (1111111111, "67062674"),
               (1234567890, "91819424"), (2000000000, "90698825"), (20000000000, "77737706")],
    "SHA512": [(59, "90693936"), (1111111109, "25091201"), (1111111111, "99943326"),
               (1234567890, "93441116"), (2000000000, "38618901"), (20000000000, "47863826")],
}

# RFC 4226 appendix D, the same 20 byte seed, 6 digits, counters 0..9.
RFC4226 = ["755224", "287082", "359152", "969429", "338314",
           "254676", "287922", "162583", "399871", "520489"]


def reference(secret, counter, digits=6, algorithm="sha1"):
    """An independent implementation, via the standard library."""
    key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8))
    mac = hmac.new(key, struct.pack(">Q", counter), algorithm).digest()
    offset = mac[-1] & 0x0F
    truncated = struct.unpack_from(">I", mac, offset)[0] & 0x7FFFFFFF
    return str(truncated % 10**digits).zfill(digits)


def store(**kwargs):
    kwargs.setdefault("path", EXAMPLE)
    kwargs.setdefault("counter_file", os.path.join(tempfile.mkdtemp(), "hotp.json"))
    return totp.KeyStore(**kwargs)


def index_of(s, label):
    for i in range(len(s)):
        if s.label(i) == label:
            return i
    raise AssertionError("no key labelled " + label)


class TestBase32(unittest.TestCase):
    def test_matches_stdlib(self):
        for secret in ("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", "JBSWY3DPEHPK3PXP",
                       "AAAAABBBBBCCCCCDDDDDEEEEEFFFFFGG", "MZXW6===", "MZXW6"):
            padded = secret.upper().rstrip("=")
            padded += "=" * ((8 - len(padded) % 8) % 8)
            self.assertEqual(totp.base32_decode(secret), base64.b32decode(padded), secret)

    def test_lowercase_and_separators(self):
        self.assertEqual(totp.base32_decode("jbswy3dpehpk3pxp"),
                         totp.base32_decode("JBSW-Y3DP EHPK3PXP"))

    def test_rejects_junk(self):
        with self.assertRaises(ValueError):
            totp.base32_decode("JBSW1809")


class TestHashBackends(unittest.TestCase):
    def test_sha1_always_available(self):
        self.assertIn("SHA1", totp.HASHERS)

    def test_block_sizes(self):
        self.assertEqual(totp.HASHERS["SHA1"][1], 64)
        if "SHA256" in totp.HASHERS:
            self.assertEqual(totp.HASHERS["SHA256"][1], 64)
        if "SHA512" in totp.HASHERS:
            self.assertEqual(totp.HASHERS["SHA512"][1], 128)

    def test_digests_match_stdlib(self):
        for name, (digest, _) in totp.HASHERS.items():
            expected = hashlib.new(name.lower(), b"totpad").digest()
            self.assertEqual(digest(b"totpad"), expected, name)

    def test_pads_reproduce_hmac(self):
        for name, (digest, block) in totp.HASHERS.items():
            for raw in (b"", b"short", b"x" * block, b"y" * (block * 3)):
                ipad, opad = totp.hmac_pads(raw, digest, block)
                mine = digest(opad + digest(ipad + b"message"))
                expected = hmac.new(raw, b"message", name.lower()).digest()
                self.assertEqual(mine, expected, (name, len(raw)))


class TestRFCVectors(unittest.TestCase):
    def test_totp_all_algorithms(self):
        s = store()
        for algorithm, vectors in RFC6238.items():
            if algorithm not in totp.HASHERS:
                self.skipTest(algorithm + " unavailable")
            label = "RFC6238" if algorithm == "SHA1" else "RFC6238-" + algorithm[3:]
            i = index_of(s, label)
            for unix_time, expected in vectors:
                self.assertEqual(s.code(i, unix_time), expected, (algorithm, unix_time))

    def test_hotp_counter_sequence(self):
        s = store()
        i = index_of(s, "RFC4226")
        for expected in RFC4226:
            self.assertEqual(s.code(i, 0), expected)
            s.used(i)  # advance as though it had been typed

    def test_hotp_ignores_the_clock(self):
        s = store()
        i = index_of(s, "RFC4226")
        self.assertEqual(s.code(i, 59), s.code(i, 20000000000))


class TestSteam(unittest.TestCase):
    def setUp(self):
        self.store = store()
        self.index = index_of(self.store, "SteamGuard")

    def test_five_characters_from_the_alphabet(self):
        for unix_time in (59, 1700000000, 20000000000):
            code = self.store.code(self.index, unix_time)
            self.assertEqual(len(code), 5)
            for char in code:
                self.assertIn(char, totp.STEAM_ALPHABET)

    def test_deterministic(self):
        self.assertEqual(self.store.code(self.index, 1700000000),
                         store().code(self.index, 1700000000))

    def test_changes_between_windows(self):
        self.assertNotEqual(self.store.code(self.index, 1700000000),
                            self.store.code(self.index, 1700000030))

    def test_encoding_is_base26_little_endian(self):
        # 0 maps to the first character repeated; 25 to the last then the first.
        self.assertEqual(totp.steam_encode(0), totp.STEAM_ALPHABET[0] * 5)
        self.assertEqual(totp.steam_encode(25), totp.STEAM_ALPHABET[25] + totp.STEAM_ALPHABET[0] * 4)
        self.assertEqual(totp.steam_encode(26), totp.STEAM_ALPHABET[0] + totp.STEAM_ALPHABET[1]
                         + totp.STEAM_ALPHABET[0] * 3)

    def test_algorithm_forced_to_sha1(self):
        key = self.store.keys[self.index]
        self.assertEqual(key.digest, totp.HASHERS["SHA1"][0])
        self.assertEqual(key.digits, totp.STEAM_DIGITS)


class TestAgainstStdlib(unittest.TestCase):
    def test_every_example_key(self):
        with open(EXAMPLE) as f:
            services = json.load(f)["services"]
        s = store()
        for svc in services:
            otp = svc["otp"]
            if (otp.get("algorithm") or "SHA1").upper() not in totp.HASHERS:
                continue
            if (otp.get("tokenType") or "TOTP").upper() != "TOTP":
                continue
            try:
                i = index_of(s, svc["name"][:20])
            except AssertionError:
                continue  # label was disambiguated, covered elsewhere
            digits = otp.get("digits") or 6
            period = otp.get("period") or 30
            algorithm = (otp.get("algorithm") or "sha1").lower()
            for unix_time in (59, 1700000000, 1788861556):
                self.assertEqual(
                    s.code(i, unix_time),
                    reference(svc["secret"], unix_time // period, digits, algorithm),
                    svc["name"],
                )


class TestLabelsAndGroups(unittest.TestCase):
    def setUp(self):
        self.store = store()
        self.labels = [self.store.label(i) for i in range(len(self.store))]

    def test_sorted_case_insensitively(self):
        self.assertEqual(self.labels, sorted(self.labels, key=str.lower))

    def test_labels_unique_and_within_width(self):
        self.assertEqual(len(set(self.labels)), len(self.labels))
        for label in self.labels:
            self.assertLessEqual(len(label), 20, label)

    def test_duplicate_names_get_accounts(self):
        dupes = sorted(l for l in self.labels if l.startswith("Duplicat"))
        self.assertEqual(dupes, ["Duplicat/first@examp", "Duplicat/second@exam"])

    def test_group_name_used_when_account_is_missing(self):
        grouped = sorted(l for l in self.labels if l.startswith("Grouped"))
        self.assertEqual(grouped, ["Grouped/Home", "Grouped/Work"])

    def test_group_exposed_per_key(self):
        self.assertEqual(self.store.group(index_of(self.store, "Zebra")), "Work")
        self.assertEqual(self.store.group(index_of(self.store, "Acme")), "Home")
        self.assertEqual(self.store.group(index_of(self.store, "RFC6238")), "")

    def test_unsupported_algorithm_is_skipped(self):
        self.assertNotIn("BadAlgo", self.labels)

    def test_kinds(self):
        self.assertEqual(self.store.kind(index_of(self.store, "RFC4226")), totp.HOTP)
        self.assertEqual(self.store.kind(index_of(self.store, "SteamGuard")), totp.STEAM)
        self.assertEqual(self.store.kind(index_of(self.store, "Zebra")), totp.TOTP)

    def test_time_based_flags(self):
        self.assertFalse(self.store.time_based(index_of(self.store, "RFC4226")))
        self.assertTrue(self.store.time_based(index_of(self.store, "SteamGuard")))
        self.assertTrue(self.store.time_based(index_of(self.store, "Zebra")))


class TestCaching(unittest.TestCase):
    def counted(self, s, index):
        calls = []
        key = s.keys[index]
        original = key.digest

        def counting(data):
            calls.append(data)
            return original(data)

        key.digest = counting
        return calls

    def test_one_computation_per_window(self):
        s = store()
        i = index_of(s, "Zebra")
        calls = self.counted(s, i)
        base = 1700000010  # exactly on a 30 second boundary
        first = s.code(i, base)
        self.assertEqual(len(calls), 2)  # inner and outer hash, once
        self.assertEqual(s.code(i, base + 29), first)
        self.assertEqual(len(calls), 2)  # served from cache
        self.assertNotEqual(s.code(i, base + 30), first)
        self.assertEqual(len(calls), 4)

    def test_hotp_cache_invalidated_on_use(self):
        s = store()
        i = index_of(s, "RFC4226")
        first = s.code(i, 0)
        s.used(i)
        self.assertNotEqual(s.code(i, 0), first)

    def test_used_is_a_noop_for_time_based_keys(self):
        s = store()
        i = index_of(s, "Zebra")
        before = s.code(i, 1700000010)
        self.assertFalse(s.used(i))
        self.assertEqual(s.code(i, 1700000010), before)

    def test_honours_period(self):
        s = store()
        i = index_of(s, "sixty")
        self.assertEqual(s.period(i), 60)
        self.assertEqual(s.code(i, 1700000040), s.code(i, 1700000069))
        self.assertNotEqual(s.code(i, 1700000040), s.code(i, 1700000100))

    def test_utc_offset_shifts_window(self):
        i = index_of(store(), "Zebra")
        self.assertEqual(store(utc_offset=1).code(i, 1700003600),
                         store().code(i, 1700000000))


class TestHotpPersistence(unittest.TestCase):
    def test_counter_survives_reload(self):
        path = os.path.join(tempfile.mkdtemp(), "hotp.json")
        first = store(counter_file=path)
        i = index_of(first, "RFC4226")
        first.used(i)
        first.used(i)

        second = store(counter_file=path)
        self.assertEqual(second.code(i, 0), RFC4226[2])

    def test_backup_counter_used_when_further_ahead(self):
        path = os.path.join(tempfile.mkdtemp(), "hotp.json")
        with open(path, "w") as f:
            json.dump({"RFC4226": 0}, f)
        s = store(counter_file=path)
        self.assertEqual(s.code(index_of(s, "RFC4226"), 0), RFC4226[0])

    def test_saved_counter_wins_when_ahead_of_backup(self):
        path = os.path.join(tempfile.mkdtemp(), "hotp.json")
        with open(path, "w") as f:
            json.dump({"RFC4226": 5}, f)
        s = store(counter_file=path)
        self.assertEqual(s.code(index_of(s, "RFC4226"), 0), RFC4226[5])

    def test_corrupt_counter_file_ignored(self):
        path = os.path.join(tempfile.mkdtemp(), "hotp.json")
        with open(path, "w") as f:
            f.write("{{{not json")
        s = store(counter_file=path)
        self.assertEqual(s.code(index_of(s, "RFC4226"), 0), RFC4226[0])

    def test_read_only_filesystem_degrades_quietly(self):
        s = store(counter_file="/proc/totpad-cannot-write/hotp.json")
        i = index_of(s, "RFC4226")
        self.assertTrue(s.used(i))  # still advances in memory
        self.assertFalse(s.counters_writable)
        self.assertEqual(s.code(i, 0), RFC4226[1])


class TestLoadErrors(unittest.TestCase):
    def write(self, payload):
        path = os.path.join(tempfile.mkdtemp(), "backup.2fas")
        with open(path, "w") as f:
            json.dump(payload, f)
        return path

    def test_encrypted_backup_explains_itself(self):
        with self.assertRaises(ValueError) as caught:
            totp.KeyStore(path=ENCRYPTED)
        self.assertIn("encrypted", str(caught.exception).lower())

    def test_missing_services_key(self):
        with self.assertRaises(ValueError):
            totp.KeyStore(path=self.write({"groups": []}))

    def test_empty_backup_raises(self):
        with self.assertRaises(ValueError):
            totp.KeyStore(path=self.write({"services": []}))

    def test_entry_without_secret_skipped(self):
        path = self.write({"services": [
            {"name": "NoSecret", "otp": {"tokenType": "TOTP"}},
            {"name": "Fine", "secret": "JBSWY3DPEHPK3PXP", "otp": {}},
        ]})
        s = totp.KeyStore(path=path)
        self.assertEqual([s.label(i) for i in range(len(s))], ["Fine"])

    def test_find_config_locates_backup(self):
        path = self.write({"services": [{"name": "A", "secret": "JBSWY3DPEHPK3PXP", "otp": {}}]})
        self.assertEqual(totp.find_config(os.path.dirname(path)), path)

    def test_find_config_without_backup(self):
        with self.assertRaises(OSError):
            totp.find_config(tempfile.mkdtemp())


if __name__ == "__main__":
    unittest.main()
