# totpad

A TOTP authenticator for the [Adafruit MacroPad RP2040](https://www.adafruit.com/product/5128).
Turn the knob to pick an account, press it to type the six digit code.

Keys come from a [2FAS](https://2fas.com/) backup export, so the phone app stays
the source of truth. A DS3231 on the STEMMA QT connector keeps the clock honest
between power cycles.

## Layout

| File | Purpose |
| --- | --- |
| `code.py` | Hardware, display and input loop. |
| `totp.py` | Key loading and code generation. No hardware imports, so it is testable on CPython. |
| `usage.py` | Press counters, shortcut ranking, and the per-account colour map. |
| `envelope.py` | Device-bound encryption of the backup: AES-256-CTR then HMAC-SHA256. |
| `hashes.py` | Hash backends shared by the generator and the envelope. |
| `aes.py` | Pure-Python AES, used where there is no `aesio` (the host, the tests). |
| `tools/encrypt_backup.py` | Host side: make a key file, encrypt a backup, verify one. |
| `tools/push_serial.py` | Install over USB serial while the drive is hidden. |
| `tools/set_clock.py` | Set the DS3231 from the host's clock. |
| `Makefile` | `make help` lists everything: test, mpy, install, libs, firmware, flash. |
| `boot.py` | Hides the USB drive unless the top-left key is held at power-on. |
| `example.2fas.example` | A fake backup used by the tests. Contains no real secrets. |
| `tests/` | `make test`  |

## Hardware

- Adafruit MacroPad RP2040, CircuitPython 10.3.0
- DS3231 real time clock on STEMMA QT
- Libraries in `/lib`: `adafruit_ds3231`, `adafruit_bitmap_font`,
  `adafruit_display_text`, `adafruit_progressbar`, `adafruit_hid`, `neopixel`,
  and `adafruit_hashlib` (only reached for SHA512, which the firmware lacks)

`make firmware flash libs install` takes a board from any version to this one.
Firmware and libraries are pinned in the Makefile (`CP_VERSION`,
`BUNDLE_DATE`), because `.mpy` bytecode is tied to the CircuitPython major
version and the three must move together.

## Install

Copy onto the CIRCUITPY volume:

```
/boot.py
/code.py
/totp.mpy            <- compiled by 'make install'
/usage.mpy
/envelope.mpy
/hashes.mpy
/aes.mpy
/secrcode_28.bdf     <- Secret Code font by Matthew Welch
/totp.2fas.enc       <- your 2FAS backup, encrypted to this device
/keyfile.bin         <- the 32 byte device key
/usage.json          <- created by the device, shortcut press counts
/hotp.json           <- created by the device, HOTP counters
/lib/                <- the libraries listed above
```

A plaintext `*.2fas` is still accepted, so nothing breaks if you drop one on;
an encrypted backup is preferred when both are present.

`code.py` and `boot.py` stay as source, since CircuitPython only looks for
those two by name. The other modules ship as `.mpy` to cut boot time;
`make install-src` puts the readable versions on instead, for debugging.

`make install BACKUP=path/to/backup.2fas` does the copy. The backup file is the
only configuration; any single `*.2fas` or `*.2fas.enc` in the root is picked up
automatically.

Because `boot.py` normally hides the drive, `make install-serial` pushes the code
through the USB serial console instead, which works without holding a key at
power-on. `make serial-check` reports firmware, crypto backend, clock and free
memory, and `make set-clock` sets the DS3231 from this host.

**Keep the clock right.** TOTP tolerates about ±30 s, so a minute of DS3231 drift
breaks every code. `make set-clock` fixes it; `make serial-check` shows it.

## Using it

- **Turn the knob** to pick an account. Accounts are listed alphabetically,
  ignoring case, and the code appears immediately.
- **Press the knob** to type the code for the selected account, followed by
  Enter.
- **Press a lit key** to type that account's code directly, without scrolling to
  it. The display switches to it too, so you can see what was sent.
- The screen and the LEDs go dark after 60 s of no input.
- **The first press after that only wakes the screen.** It never types. A device
  in a bag will get pressed, and a code typed into whatever window happens to
  have focus is a code disclosed.

The selected account's key fades from full colour towards dim as its code
approaches expiry, so the countdown sits under the hand that is about to press
it. `FADE_FLOOR` and `LED_RATE` control the floor and the refresh rate.

### Shortcut keys

Every time a code is typed, by key or by knob, that account's counter goes up.
The twelve most used accounts get the twelve keys, most used at the top left.
The selected account's key glows at full brightness while the rest stay dim.

**A colour belongs to the account, not to the key.** It is derived from a hash
of the account label, so GitHub is the same colour whether it sits at KEY1 or
KEY10, and climbing the rankings carries its colour along. Clashes within the
twelve are resolved by probing forward through the palette, walking the accounts
alphabetically rather than by rank, so a change in ranking alone never repaints
anything.

The one case where a colour can change is when the *set* of twelve accounts
changes — a newcomer displacing somebody can make a clash resolve differently.
Re-ranking on its own is safe.

The assignment is worked out **once at boot**, so keys never rearrange
themselves under your fingers mid-session. Today's presses take effect at the
next power cycle. On a fresh device, before any counts exist, the keys are
filled alphabetically so there is something to press.

Counts live in `/usage.json`. CircuitPython can only write to flash when the USB
drive is hidden, which is the normal state, so counts persist. While you have
the drive mounted to edit files, counting still works but is forgotten at the
next reset.

### Clock health

Every code depends on the clock, so a dead coin cell on the DS3231 turns every
code silently wrong. The DS3231 latches an oscillator-stop flag across power
loss, which is read at boot before the driver clears it. That, or a year earlier
than `SANE_YEAR`, replaces the date on screen with `!! CLOCK LOST !!`. Fix it by
running `rtc_setter.py`.

### Token types

Whatever 2FAS exports, within what the hardware can compute:

| Feature | Support |
| --- | --- |
| TOTP | Yes, any period and 6 to 8 digits |
| HOTP | Yes. Counters advance only when a code is typed, and persist in `/hotp.json` |
| Steam guard | Yes, five characters of Steam's own alphabet |
| SHA1, SHA256 | Native, from the firmware |
| SHA512 | Via `adafruit_hashlib`, which is pure Python and slower |
| Groups | Used to tell apart accounts that share a name and have no account field |
| Encrypted backups | No, and it says so plainly rather than failing obscurely |

Entries using an algorithm the build cannot compute are skipped rather than
taking the whole file down with them. HOTP counters are keyed on the label and
kept apart from the backup, so reinstalling a backup cannot rewind them; the
higher of the two values wins.

Encrypted 2FAS backups use PBKDF2-SHA256 with 10,000 iterations, which would
take minutes in pure Python on an RP2040. Export with the password left blank.

Settings live at the top of `code.py`: `UTC_OFFSET`, `USE_12HR`,
`DISPLAY_TIMEOUT`, `NAME_WIDTH`, `KNOB_STEP` (flip to `1` to reverse the knob),
`CONFIG_FILE` to skip auto-detection, `LED_BRIGHTNESS` (`0` turns the LEDs off),
`UNSELECTED_DIM`, `FADE_FLOOR`, `LED_RATE`, `SAVE_INTERVAL`, and `SANE_YEAR`.

Accounts that share a service name get the account appended to the label, so
several Google entries become `Google/alice@example`, `Google/bob@example.c`,
and so on, clipped to fit the 20 character display.

## Updating the keys

1. Export an unencrypted backup from the 2FAS app.
2. Hold the **top-left key (KEY1)** while plugging the MacroPad in. The LED
   under that key flashes green and CIRCUITPY mounts.
3. `make install BACKUP=path/to/export.2fas`, which encrypts it to the device
   key, copies it as `totp.2fas.enc`, deletes any plaintext copy from the drive,
   and reads the result back to prove the device will be able to.
4. Replug without holding the key. The LED flashes red, the drive stays hidden,
   and the filesystem becomes writable to the device again.

Delete the plaintext export from the host when you are done. It is the copy
most likely to end up somewhere you did not intend, such as a sync folder.

Usage counters are keyed on the account label, not its position, so they survive
adding, removing and reordering entries in the backup.

`boot.py` only runs on a hard reset or replug, not on a soft reload. If the
drive ever refuses to appear, double-tap RESET to reach the RP2040 bootloader
(`RPI-RP2`), which is always writable.

## Design notes

This started as Adafruit's MacroPad TOTP example, which decoded the base32
secret, built the two 64 byte HMAC key pads, and ran two pure-Python SHA1
passes for *every* code, behind a hard-coded 0.5 s delay before it would even
begin. Codes took a noticeable moment to appear.

Measured on the hardware, decrypting and loading 39 accounts at boot:

| Step | Time |
| --- | --- |
| Read the file | 13 ms |
| Derive keys | 12 ms |
| Verify HMAC-SHA256 | 33 ms |
| AES-256-CTR (`aesio`) | 80 ms |
| Parse JSON | 158 ms |
| **Total `KeyStore` construction** | **557 ms** |

Generating one code afterwards is about 4 ms.

- Base32 decoding and HMAC pad expansion happen once at load, for all keys.
- Generating a code is then two SHA1 hashes over pre-built buffers.
- The native C `hashlib` is used when available, falling back to
  `adafruit_hashlib`.
- Codes are cached per key per time step, so revisiting a key costs nothing.
- The startup delay is gone, and the first key's code is ready before the main
  loop starts.
- Display updates run once per second, touching only labels whose text changed.

Correctness is checked against the published vectors in RFC 6238 (TOTP, for
SHA1, SHA256 and SHA512) and RFC 4226 (HOTP, the first ten counters), and
separately against Python's `hmac` and `base64` for every key in a backup.

## Security

### Encryption of the backup

The backup on the device is encrypted under a 32 byte key that exists only as
`/keyfile.bin` on the MacroPad and as your host copy of it. The format is
AES-256 in counter mode, then HMAC-SHA256 over the header and ciphertext, with
the tag checked before any plaintext is produced:

    b"TPAD" | version | nonce (16) | ciphertext | tag (32)

Encryption and authentication keys are derived separately from the key file, so
neither is used for two purposes. A fresh random nonce per sealing means
re-encrypting the same export twice produces different files.

Counter mode comes from `aesio`, but only after its behaviour is proven at
import. Doing the counter arithmetic in Python instead cost 2347 ms per boot
against 80 ms for the native call, so the fast path is worth having — provided it
is the same transform. Two checks decide that:

1. The AES-256-CTR known answer test from NIST SP 800-38A.
2. A carry case that vector cannot reach. Its counter block ends `…fcfdfeff`, so
   it never overflows the low 32 bits, meaning an implementation that increments
   only the last four bytes would pass it. The second check uses a nonce ending
   `ffffffff`, where such an implementation diverges on the very next block, and
   compares against the portable code path.

If either check fails, the portable implementation takes over: slower, same
answers, no silent divergence. The portable path is itself pinned to the SP
800-38A vector, and the AES core to all three FIPS-197 vectors.

**What this protects.** A copy of the backup on its own is useless. That covers
the export sitting in a sync folder, in a host backup, in cloud storage, or
grabbed by anything that briefly mounted the drive. This is the realistic leak,
and it is now closed.

**What this does not protect.** Someone holding the MacroPad gets everything.
The key file and the ciphertext live in the same flash, and an RP2040 can be
dumped wholesale over BOOTSEL with `picotool save` regardless of what
CircuitPython does. There is no secure element, no OTP secret storage and no
secure boot on this chip. Encryption that the device performs unattended cannot
survive an attacker who owns the device; treat the MacroPad as a physical key.

Closing that gap needs a secret that is not on the device, meaning a passphrase
typed in at boot. The firmware has native SHA256, so PBKDF2 is not as hopeless
as pure Python would make it, but a passphrase entered on twelve keys and a
knob is a real daily cost. Not implemented.

### Key file handling

`make keygen` writes the key file at `~/.config/totpad/keyfile.bin` with mode
0600 and refuses to overwrite an existing one, because every backup encrypted
under the old key would become unreadable. Back it up where you keep passwords.

If you lose both copies, the encrypted backup is dead — which is survivable,
because 2FAS on your phone is the real backup, not this device.

### Repository hygiene

`.gitignore` refuses `*.2fas`, `*.2fas.enc`, `keyfile.bin`, `tokens.json` and
`secrets.py`, and `make check-secrets` fails on any of them being staged. Only
`*.2fas.example` is allowed, and the examples hold RFC test vectors and dummy
secrets.

## Credits

Based on the [Adafruit MacroPad TOTP
example](https://learn.adafruit.com/macropad-2fa-totp-authenticator) by Carter
Nelson. MIT licensed, as is the original.
