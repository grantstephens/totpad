# SPDX-License-Identifier: MIT
"""Tests for the AES implementation and the backup envelope."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import envelope  # noqa: E402
import hashes  # noqa: E402
from aes import AES  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "example.2fas.example")

# FIPS-197 appendix C.
FIPS197 = [
    (bytes(range(16)), "00112233445566778899aabbccddeeff", "69c4e0d86a7b0430d8cdb78070b4c55a"),
    (bytes(range(24)), "00112233445566778899aabbccddeeff", "dda97ca4864cdfe06eaf70a0ec0d7191"),
    (bytes(range(32)), "00112233445566778899aabbccddeeff", "8ea2b7ca516745bfeafc49904b496089"),
]

# NIST SP 800-38A F.5.5, AES-256-CTR. The counter block increments as one big
# endian integer across all 16 bytes, which is what ctr_crypt does.
SP800_38A_KEY = bytes.fromhex(
    "603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
SP800_38A_NONCE = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
SP800_38A_PLAIN = bytes.fromhex(
    "6bc1bee22e409f96e93d7e117393172a"
    "ae2d8a571e03ac9c9eb76fac45af8e51"
    "30c81c46a35ce411e5fbc1191a0a52ef"
    "f69f2445df4f9b17ad2b417be66c3710")
SP800_38A_CIPHER = bytes.fromhex(
    "601ec313775789a5b7a7f504bbf3d228"
    "f443e3ca4d62b59aca84e990cacaf5c5"
    "2b0930daa23de94ce87017ba2d84988d"
    "dfc9c58db67aada613c2dd08457941a6")


class TestAES(unittest.TestCase):
    def test_fips197_vectors(self):
        for key, plain, expected in FIPS197:
            got = AES(key).encrypt_block(bytes.fromhex(plain))
            self.assertEqual(got.hex(), expected, len(key) * 8)

    def test_rejects_bad_key_length(self):
        for length in (0, 15, 17, 31, 33, 64):
            with self.assertRaises(ValueError):
                AES(b"k" * length)

    def test_rejects_bad_block_length(self):
        with self.assertRaises(ValueError):
            AES(bytes(32)).encrypt_block(b"short")


class TestCounterMode(unittest.TestCase):
    def test_sp800_38a_vector(self):
        got = envelope.ctr_crypt(SP800_38A_KEY, SP800_38A_NONCE, SP800_38A_PLAIN)
        self.assertEqual(got, SP800_38A_CIPHER)

    def test_is_its_own_inverse(self):
        back = envelope.ctr_crypt(SP800_38A_KEY, SP800_38A_NONCE, SP800_38A_CIPHER)
        self.assertEqual(back, SP800_38A_PLAIN)

    def test_partial_final_block(self):
        key, nonce = bytes(32), bytes(16)
        for length in (0, 1, 15, 16, 17, 31, 33, 1000):
            data = bytes((i * 7) & 0xFF for i in range(length))
            self.assertEqual(
                envelope.ctr_crypt(key, nonce, envelope.ctr_crypt(key, nonce, data)),
                data, length)

    def test_counter_carries_across_byte_boundaries(self):
        """A nonce of all 0xff wraps the counter; it must not crash or repeat."""
        key = bytes(32)
        nonce = b"\xff" * 16
        stream = envelope.ctr_crypt(key, nonce, bytes(64))
        blocks = [stream[i:i + 16] for i in range(0, 64, 16)]
        self.assertEqual(len(set(blocks)), 4)


class TestKeyDerivation(unittest.TestCase):
    def test_new_key_length_and_randomness(self):
        first, second = envelope.new_key(), envelope.new_key()
        self.assertEqual(len(first), envelope.KEY_BYTES)
        self.assertNotEqual(first, second)

    def test_subkeys_differ_from_master_and_each_other(self):
        master = envelope.new_key()
        enc, mac = envelope.derive(master)
        self.assertEqual(len(enc), 32)
        self.assertEqual(len(mac), 32)
        self.assertNotEqual(enc, mac)
        self.assertNotEqual(enc, master)
        self.assertNotEqual(mac, master)

    def test_derivation_is_deterministic(self):
        master = envelope.new_key()
        self.assertEqual(envelope.derive(master), envelope.derive(master))

    def test_rejects_wrong_master_length(self):
        for length in (0, 16, 31, 33):
            with self.assertRaises(ValueError):
                envelope.derive(b"k" * length)

    def test_load_key_checks_length(self):
        path = os.path.join(tempfile.mkdtemp(), "keyfile.bin")
        with open(path, "wb") as f:
            f.write(b"too short")
        with self.assertRaises(ValueError):
            envelope.load_key(path)

    def test_load_key_round_trip(self):
        path = os.path.join(tempfile.mkdtemp(), "keyfile.bin")
        key = envelope.new_key()
        with open(path, "wb") as f:
            f.write(key)
        self.assertEqual(envelope.load_key(path), key)


class TestSealUnseal(unittest.TestCase):
    def setUp(self):
        self.key = envelope.new_key()
        with open(EXAMPLE, "rb") as f:
            self.plaintext = f.read()

    def test_round_trip(self):
        blob = envelope.seal(self.key, self.plaintext)
        self.assertEqual(envelope.unseal(self.key, blob), self.plaintext)

    def test_output_is_recognisable_and_sized(self):
        blob = envelope.seal(self.key, self.plaintext)
        self.assertTrue(envelope.is_envelope(blob))
        self.assertEqual(len(blob), envelope.HEADER_BYTES + len(self.plaintext)
                         + envelope.TAG_BYTES)

    def test_ciphertext_hides_the_plaintext(self):
        blob = envelope.seal(self.key, self.plaintext)
        for needle in (b"services", b"JBSWY3DPEHPK3PXP", b"example.com"):
            self.assertIn(needle, self.plaintext)
            self.assertNotIn(needle, blob)

    def test_nonce_makes_each_sealing_unique(self):
        first = envelope.seal(self.key, self.plaintext)
        second = envelope.seal(self.key, self.plaintext)
        self.assertNotEqual(first, second)
        self.assertEqual(envelope.unseal(self.key, first),
                         envelope.unseal(self.key, second))

    def test_wrong_key_is_rejected(self):
        blob = envelope.seal(self.key, self.plaintext)
        with self.assertRaises(ValueError) as caught:
            envelope.unseal(envelope.new_key(), blob)
        self.assertIn("authentication", str(caught.exception))

    def test_tampered_ciphertext_is_rejected(self):
        blob = bytearray(envelope.seal(self.key, self.plaintext))
        blob[envelope.HEADER_BYTES + 10] ^= 0x01
        with self.assertRaises(ValueError):
            envelope.unseal(self.key, bytes(blob))

    def test_tampered_nonce_is_rejected(self):
        blob = bytearray(envelope.seal(self.key, self.plaintext))
        blob[len(envelope.MAGIC) + 2] ^= 0x80
        with self.assertRaises(ValueError):
            envelope.unseal(self.key, bytes(blob))

    def test_tampered_tag_is_rejected(self):
        blob = bytearray(envelope.seal(self.key, self.plaintext))
        blob[-1] ^= 0x01
        with self.assertRaises(ValueError):
            envelope.unseal(self.key, bytes(blob))

    def test_truncation_is_rejected(self):
        blob = envelope.seal(self.key, self.plaintext)
        for cut in (1, envelope.HEADER_BYTES, len(blob) - 1):
            with self.assertRaises(ValueError):
                envelope.unseal(self.key, blob[:cut])

    def test_plaintext_input_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            envelope.unseal(self.key, self.plaintext)
        self.assertIn("Not a totpad envelope", str(caught.exception))

    def test_unknown_version_is_rejected(self):
        blob = bytearray(envelope.seal(self.key, self.plaintext))
        blob[len(envelope.MAGIC)] = 99
        with self.assertRaises(ValueError) as caught:
            envelope.unseal(self.key, bytes(blob))
        self.assertIn("version", str(caught.exception))

    def test_empty_payload(self):
        blob = envelope.seal(self.key, b"")
        self.assertEqual(envelope.unseal(self.key, blob), b"")


class TestConstantTimeCompare(unittest.TestCase):
    def test_matches_equality(self):
        self.assertTrue(hashes.equal(b"abc", b"abc"))
        self.assertFalse(hashes.equal(b"abc", b"abd"))
        self.assertFalse(hashes.equal(b"abc", b"ab"))
        self.assertTrue(hashes.equal(b"", b""))


class TestHmac(unittest.TestCase):
    def test_matches_stdlib(self):
        import hmac as stdlib_hmac

        for algorithm in sorted(hashes.HASHERS):
            for key in (b"", b"key", b"k" * 200):
                self.assertEqual(
                    hashes.hmac(key, b"message", algorithm),
                    stdlib_hmac.new(key, b"message", algorithm.lower()).digest(),
                    (algorithm, len(key)))


class TestKeyStoreIntegration(unittest.TestCase):
    """The device path: an encrypted backup plus a key file on the drive."""

    def setUp(self):
        import totp

        self.totp = totp
        self.dir = tempfile.mkdtemp()
        self.key = envelope.new_key()
        self.key_path = os.path.join(self.dir, "keyfile.bin")
        with open(self.key_path, "wb") as f:
            f.write(self.key)
        with open(EXAMPLE, "rb") as f:
            plaintext = f.read()
        self.enc_path = os.path.join(self.dir, "totp.2fas.enc")
        with open(self.enc_path, "wb") as f:
            f.write(envelope.seal(self.key, plaintext))

    def store(self, **kwargs):
        kwargs.setdefault("counter_file", os.path.join(self.dir, "hotp.json"))
        return self.totp.KeyStore(**kwargs)

    def test_loads_encrypted_backup(self):
        s = self.store(path=self.enc_path, key_file=self.key_path)
        self.assertTrue(s.encrypted)
        self.assertGreater(len(s), 0)

    def test_codes_match_the_plaintext_backup(self):
        encrypted = self.store(path=self.enc_path, key_file=self.key_path)
        plain = self.store(path=EXAMPLE)
        self.assertEqual([encrypted.label(i) for i in range(len(encrypted))],
                         [plain.label(i) for i in range(len(plain))])
        for i in range(len(plain)):
            self.assertEqual(encrypted.code(i, 1700000010), plain.code(i, 1700000010))

    def test_plaintext_backup_still_works(self):
        s = self.store(path=EXAMPLE)
        self.assertFalse(s.encrypted)

    def test_missing_key_file_explains_itself(self):
        with self.assertRaises(ValueError) as caught:
            self.store(path=self.enc_path)
        self.assertIn("key file", str(caught.exception))

    def test_wrong_key_file_explains_itself(self):
        other = os.path.join(self.dir, "other.bin")
        with open(other, "wb") as f:
            f.write(envelope.new_key())
        with self.assertRaises(ValueError):
            self.store(path=self.enc_path, key_file=other)

    def test_find_config_prefers_the_encrypted_backup(self):
        with open(os.path.join(self.dir, "old.2fas"), "w") as f:
            f.write("{}")
        self.assertEqual(self.totp.find_config(self.dir), self.enc_path)

    def test_find_config_falls_back_to_plaintext(self):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "totp.2fas")
        with open(path, "w") as f:
            f.write("{}")
        self.assertEqual(self.totp.find_config(directory), path)

    def test_is_encrypted_detects_both(self):
        self.assertTrue(self.totp.is_encrypted(self.enc_path))
        self.assertFalse(self.totp.is_encrypted(EXAMPLE))

    def test_garbage_file_is_reported_clearly(self):
        path = os.path.join(self.dir, "junk.2fas")
        with open(path, "wb") as f:
            f.write(b"not json, not an envelope")
        with self.assertRaises(ValueError) as caught:
            self.store(path=path)
        self.assertIn("valid JSON", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
