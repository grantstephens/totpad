#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Encrypt a 2FAS backup for one MacroPad, and manage its key file.

The key file is what binds the backup to the device. Generate it once, install
it on the MacroPad, and keep the host copy somewhere you would keep a password.
Lose both copies and the encrypted backup is unrecoverable -- which is fine,
because 2FAS on your phone is the real backup.

    tools/encrypt_backup.py --new-key                     # once
    tools/encrypt_backup.py backup.2fas -o totp.2fas.enc  # each export
    tools/encrypt_backup.py --verify totp.2fas.enc        # check a file
"""

import argparse
import json
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import envelope  # noqa: E402

DEFAULT_KEY = os.path.expanduser("~/.config/totpad/keyfile.bin")


def generate(path, force=False):
    if os.path.exists(path) and not force:
        raise SystemExit(
            "Key file already exists: {}\n"
            "Refusing to overwrite it. Every backup encrypted under the old key "
            "becomes unreadable.\nPass --force if that is really what you want.".format(path)
        )
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    key = envelope.new_key()
    with open(path, "wb") as f:
        f.write(key)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    print("Wrote {} bytes to {} (mode 0600)".format(len(key), path))
    print("Install it on the MacroPad as /keyfile.bin, and back it up somewhere safe.")


def encrypt(source, target, key_path):
    key = envelope.load_key(key_path)
    with open(source, "rb") as f:
        plaintext = f.read()

    try:
        parsed = json.loads(plaintext)
    except ValueError:
        raise SystemExit("{} is not valid JSON; is it already encrypted?".format(source))
    if "services" not in parsed:
        raise SystemExit("{} has no 'services' key; not a 2FAS backup".format(source))

    blob = envelope.seal(key, plaintext)

    # Never ship a file that the device cannot read back.
    recovered = envelope.unseal(key, blob)
    if recovered != plaintext:
        raise SystemExit("Round trip failed; refusing to write a broken file")

    with open(target, "wb") as f:
        f.write(blob)
    print("{} services, {} bytes -> {} ({} bytes, backend {})".format(
        len(parsed["services"]), len(plaintext), target, len(blob), envelope.BACKEND))


def verify(target, key_path):
    key = envelope.load_key(key_path)
    with open(target, "rb") as f:
        blob = f.read()
    plaintext = envelope.unseal(key, blob)
    parsed = json.loads(plaintext)
    print("{}: authentic, {} services, schema {}".format(
        target, len(parsed.get("services", [])), parsed.get("schemaVersion")))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", nargs="?", help="plaintext .2fas backup to encrypt")
    parser.add_argument("-o", "--output", help="where to write the encrypted backup")
    parser.add_argument("-k", "--key", default=DEFAULT_KEY, help="key file (default: %(default)s)")
    parser.add_argument("--new-key", action="store_true", help="generate a key file")
    parser.add_argument("--force", action="store_true", help="overwrite an existing key file")
    parser.add_argument("--verify", metavar="FILE", help="check an encrypted backup")
    args = parser.parse_args()

    if args.new_key:
        generate(args.key, args.force)
        return
    if args.verify:
        verify(args.verify, args.key)
        return
    if not args.source:
        parser.error("give a backup to encrypt, or --new-key, or --verify")

    target = args.output or (args.source + ".enc")
    encrypt(args.source, target, args.key)


if __name__ == "__main__":
    main()
