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
| `boot.py` | Hides the USB drive unless the top-left key is held at power-on. |
| `example.2fas.example` | A fake backup used by the tests. Contains no real secrets. |
| `tests/` | `make test`  |

## Hardware

- Adafruit MacroPad RP2040, CircuitPython 9.x
- DS3231 real time clock on STEMMA QT
- Libraries in `/lib`: `adafruit_ds3231`, `adafruit_bitmap_font`,
  `adafruit_display_text`, `adafruit_progressbar`, `adafruit_hid`, and
  `adafruit_hashlib` (only needed if the build has no native `hashlib`)

## Install

Copy onto the CIRCUITPY volume:

```
/boot.py
/code.py
/totp.py
/secrcode_28.bdf     <- Secret Code font by Matthew Welch
/<anything>.2fas     <- your 2FAS backup
/lib/                <- the libraries listed above
```

`make install BACKUP=path/to/backup.2fas` does the copy. The
`.2fas` file is the only configuration; any single `*.2fas` in the root is
picked up automatically.

## Using it

- **Turn the knob** to pick an account. Accounts are listed alphabetically,
  ignoring case, and the code appears immediately.
- **Press the knob** to type the code followed by Enter.
- The screen blanks after 60 s of no input and wakes on any input.

Settings live at the top of `code.py`: `UTC_OFFSET`, `USE_12HR`,
`DISPLAY_TIMEOUT`, `NAME_WIDTH`, `KNOB_STEP` (flip to `1` to reverse the knob),
and `CONFIG_FILE` to skip auto-detection.

Accounts that share a service name get the account appended to the label, so
several Google entries become `Google/alice@example`, `Google/bob@example.c`,
and so on, clipped to fit the 20 character display.

## Updating the keys

1. Export an unencrypted backup from the 2FAS app.
2. Hold the **top-left key (KEY1)** while plugging the MacroPad in. The LED
   under that key flashes green and CIRCUITPY mounts.
3. Replace the `.2fas` file, keeping the extension.
4. Replug without holding the key. The LED flashes red and the drive stays
   hidden.

`boot.py` only runs on a hard reset or replug, not on a soft reload. If the
drive ever refuses to appear, double-tap RESET to reach the RP2040 bootloader
(`RPI-RP2`), which is always writable.

## Design notes

This started as Adafruit's MacroPad TOTP example, which decoded the base32
secret, built the two 64 byte HMAC key pads, and ran two pure-Python SHA1
passes for *every* code, behind a hard-coded 0.5 s delay before it would even
begin. Codes took a noticeable moment to appear.

- Base32 decoding and HMAC pad expansion happen once at load, for all keys.
- Generating a code is then two SHA1 hashes over pre-built buffers.
- The native C `hashlib` is used when available, falling back to
  `adafruit_hashlib`.
- Codes are cached per key per time step, so revisiting a key costs nothing.
- The startup delay is gone, and the first key's code is ready before the main
  loop starts.
- Display updates run once per second, touching only labels whose text changed.

Correctness is checked against RFC 6238's published vectors and, separately,
against Python's `hmac` and `base64` for every key in a backup.

## Security

A `.2fas` backup holds your TOTP secrets in plain text, and CircuitPython
offers no secure element. `boot.py` keeps the file off any host you plug into,
but anything running on the device can read it, and the RP2040 bootloader can
dump the flash. **Treat the MacroPad as a physical key**, and keep the 2FAS
backup on your phone as the recovery path.

The `.gitignore` here refuses `*.2fas`, `tokens.json`, and `secrets.py` so a
real backup cannot be committed by accident. Only `*.2fas.example` is allowed.

## Credits

Based on the [Adafruit MacroPad TOTP
example](https://learn.adafruit.com/macropad-2fa-totp-authenticator) by Carter
Nelson. MIT licensed, as is the original.
