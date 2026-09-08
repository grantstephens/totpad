# SPDX-FileCopyrightText: 2021 Carter Nelson for Adafruit Industries
#
# SPDX-License-Identifier: MIT
#
# totpad - a TOTP authenticator for the Adafruit MacroPad RP2040.
#
# Keys are read once at boot from a 2FAS backup (*.2fas) in the root of
# CIRCUITPY. See totp.py for code generation, usage.py for the shortcut
# assignment, and README.md for usage.

import gc
import time

import board
import rtc
import keypad
import rotaryio
import neopixel

import adafruit_ds3231

import displayio
import terminalio
from adafruit_bitmap_font import bitmap_font
from adafruit_display_text import label
from adafruit_progressbar.horizontalprogressbar import HorizontalProgressBar

import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keyboard_layout_us import KeyboardLayoutUS
from adafruit_hid.keycode import Keycode

from totp import KeyStore, STEAM_ALPHABET
from usage import UsageTracker, KEY_COLORS, color_map

# --| User Config |--------------------------------------------------------
UTC_OFFSET = 0          # time zone offset
USE_12HR = False        # set 12/24 hour format
DISPLAY_TIMEOUT = 60    # screen saver timeout in seconds
NAME_WIDTH = 20         # characters of key name that fit on screen
KNOB_STEP = -1          # set to 1 to swap the knob direction
CONFIG_FILE = None      # explicit path, or None to auto-detect a *.2fas file
USAGE_FILE = "/usage.json"
COUNTER_FILE = "/hotp.json"
KEY_FILE = "/keyfile.bin"   # device key for an encrypted *.2fas.enc backup
LED_BRIGHTNESS = 0.15   # shortcut key brightness, 0 to disable the LEDs
UNSELECTED_DIM = 0.3    # unselected shortcut keys, as a fraction of full colour
FADE_FLOOR = 0.15       # how dim the selected key gets as its code expires
LED_RATE = 0.1          # seconds between LED fade updates
SAVE_INTERVAL = 30      # seconds between usage counter writes at most
SANE_YEAR = 2025        # a clock reading before this is not to be trusted
# -------------------------------------------------------------------------

boot_start = time.monotonic()

# -------------------------------------------------------------------------
#                    K E Y    L O A D I N G
# -------------------------------------------------------------------------
store = KeyStore(
    path=CONFIG_FILE,
    name_width=NAME_WIDTH,
    utc_offset=UTC_OFFSET,
    counter_file=COUNTER_FILE,
    key_file=KEY_FILE,
)
NUM_KEYS = len(store)
print("{} keys from {}{}".format(
    NUM_KEYS, store.path, " (encrypted)" if store.encrypted else " (PLAINTEXT)"))

# Shortcuts are assigned once, from the counts as they were at boot, so the
# keys do not rearrange themselves under your fingers mid-session. Today's
# presses take effect at the next boot.
usage = UsageTracker(USAGE_FILE)
labels = [store.label(i) for i in range(NUM_KEYS)]
index_of = {name: i for i, name in enumerate(labels)}
shortcut_keys = [index_of[name] for name in usage.shortcuts(labels, len(KEY_COLORS))]

# Colours are keyed on the account, not the key position, so moving up the
# rankings takes an account's colour with it.
shortcut_colors = color_map([labels[key] for key in shortcut_keys])
gc.collect()

# -------------------------------------------------------------------------
#                    C L O C K
# -------------------------------------------------------------------------
# Every code depends on the clock, so a stopped oscillator or a dead coin cell
# silently turns every code wrong. The DS3231 latches an oscillator-stop flag
# across power loss, which is read before the driver clears it.
i2c = board.STEMMA_I2C()
ds3231 = adafruit_ds3231.DS3231(i2c)
try:
    clock_lost_power = bool(ds3231.lost_power)
except OSError as err:
    clock_lost_power = True
    print("RTC unreadable:", err)
rtc.set_time_source(ds3231)


def clock_suspect():
    """True when the clock cannot be trusted to produce valid codes."""
    return clock_lost_power or time.localtime().tm_year < SANE_YEAR


# -------------------------------------------------------------------------
#                    D I S P L A Y    S E T U P
# -------------------------------------------------------------------------
display = board.DISPLAY

# Secret Code font by Matthew Welch
# http://www.squaregear.net/fonts/
font = bitmap_font.load_font("/secrcode_28.bdf")
try:
    font.load_glyphs(b"0123456789-" + STEAM_ALPHABET.encode())
except (KeyError, ValueError, OSError):
    font.load_glyphs(b"0123456789-")  # font carries digits only

name = label.Label(terminalio.FONT, text="?" * NAME_WIDTH, color=0xFFFFFF)
name.anchor_point = (0.0, 0.0)
name.anchored_position = (0, 0)

code = label.Label(font, text="------", color=0xFFFFFF)
code.anchor_point = (0.5, 0.0)
code.anchored_position = (display.width // 2, 15)

rtc_date = label.Label(terminalio.FONT, text="2021/01/01")
rtc_date.anchor_point = (0.0, 0.5)
rtc_date.anchored_position = (0, 49)

rtc_time = label.Label(terminalio.FONT, text="12:34:56 AM")
rtc_time.anchor_point = (0.0, 0.5)
rtc_time.anchored_position = (0, 59)

# Fixed 0..30 scale; other periods are rescaled onto it below.
progress_bar = HorizontalProgressBar(
    (68, 46), (55, 17), bar_color=0xFFFFFF, min_value=0, max_value=30
)

splash = displayio.Group()
splash.append(name)
splash.append(code)
splash.append(rtc_date)
splash.append(rtc_time)
splash.append(progress_bar)

display.root_group = splash

# -------------------------------------------------------------------------
#                       H I D    S E T U P
# -------------------------------------------------------------------------
# Some hosts need a moment after enumeration before the first report, so pad
# out the first second of run time. Boot work above usually covers it already.
remaining = 1.0 - (time.monotonic() - boot_start)
if remaining > 0:
    time.sleep(remaining)
keyboard = Keyboard(usb_hid.devices)
keyboard_layout = KeyboardLayoutUS(keyboard)

# -------------------------------------------------------------------------
#                    M A C R O P A D    S E T U P
# -------------------------------------------------------------------------
# Key numbers 0..11 are the grid, top left to bottom right. 12 is the knob.
KEY_PINS = (
    board.KEY1, board.KEY2, board.KEY3,
    board.KEY4, board.KEY5, board.KEY6,
    board.KEY7, board.KEY8, board.KEY9,
    board.KEY10, board.KEY11, board.KEY12,
    board.BUTTON,
)
KNOB_BUTTON = len(KEY_PINS) - 1

keys = keypad.Keys(KEY_PINS, value_when_pressed=False, pull=True)
knob = rotaryio.IncrementalEncoder(board.ROTA, board.ROTB)
pixels = neopixel.NeoPixel(
    board.NEOPIXEL, 12, brightness=LED_BRIGHTNESS, auto_write=False
)


def scaled(color, factor):
    return tuple(min(255, int(c * factor)) for c in color)


def paint_leds(selected, remaining=1.0, lit=True):
    """Colour the assigned keys.

    The selected account's key fades from full colour towards FADE_FLOOR as its
    code approaches expiry, which puts the countdown under the hand that is
    about to press it. Unselected keys sit at a constant dim level.

    Dimming rather than brightening, because the palette already sits at full
    channel values and boosting a saturated colour just clips.
    """
    if not lit or not LED_BRIGHTNESS:
        pixels.fill(0)
        pixels.show()
        return
    fade = FADE_FLOOR + (1.0 - FADE_FLOOR) * remaining
    for slot in range(12):
        if slot < len(shortcut_keys):
            key_index = shortcut_keys[slot]
            color = shortcut_colors.get(labels[key_index], (0, 0, 0))
            color = scaled(color, fade if key_index == selected else UNSELECTED_DIM)
            pixels[slot] = color
        else:
            pixels[slot] = 0
    pixels.show()


# -------------------------------------------------------------------------
#                       M A I N
# -------------------------------------------------------------------------
def type_code(key_index, unix_time):
    """Type the code for a key, count the use, and return the code."""
    otp = store.code(key_index, unix_time)
    keyboard_layout.write(otp)
    keyboard.send(Keycode.ENTER)
    usage.bump(store.label(key_index))
    if store.used(key_index):  # HOTP advances to the next counter
        otp = store.code(key_index, unix_time)
    return otp


def select(key_index, unix_time):
    """Show a key on the display and return its code."""
    otp = store.code(key_index, unix_time)
    name.text = store.label(key_index)
    code.text = otp
    return otp


awake = True
knob_pos = knob.position
# Start on the most used account, which is also shortcut slot 0.
current_key = shortcut_keys[0] if shortcut_keys else 0
totp_code = select(current_key, time.time())

last_second = -1
last_bar = -1
mono = time.monotonic()
wake_up_time = mono
last_save = mono
last_led = mono
second_mono = mono  # when the displayed second last ticked over
seconds_into_window = 0
paint_leds(current_key)
if clock_lost_power:
    print("RTC lost power: codes may be wrong until the clock is set")
gc.collect()

while True:
    mono = time.monotonic()
    event = keys.events.get()
    position = knob.position

    if event or position != knob_pos:
        wake_up_time = mono

        if not awake:
            # The first input only wakes the screen. Typing here would let a
            # press in a bag send a real code to whatever window has focus.
            awake = True
            splash.hidden = False
            last_second = -1
            knob_pos = position
            paint_leds(current_key)
            continue

        if position != knob_pos:
            current_key = (current_key + KNOB_STEP * (position - knob_pos)) % NUM_KEYS
            knob_pos = position
            totp_code = select(current_key, time.time())
            paint_leds(current_key)

        if event and event.pressed:
            if event.key_number == KNOB_BUTTON:
                totp_code = type_code(current_key, time.time())
                code.text = totp_code
            elif event.key_number < len(shortcut_keys):
                # A shortcut key selects its account as well as typing it, so
                # the screen always shows what was just sent.
                current_key = shortcut_keys[event.key_number]
                name.text = store.label(current_key)
                totp_code = type_code(current_key, time.time())
                code.text = totp_code
                # Flash the key that was hit, then settle to its own colour.
                pixels[event.key_number] = (255, 255, 255)
                pixels.show()
                last_second = -1

        if event and event.released and event.key_number < len(shortcut_keys):
            paint_leds(current_key)

    if not awake:
        # Nothing on screen, so only watch the inputs.
        time.sleep(0.05)
        continue

    if mono - wake_up_time > DISPLAY_TIMEOUT:
        awake = False
        splash.hidden = True
        paint_leds(current_key, lit=False)
        usage.save()  # idle is the cheapest moment to touch the flash
        continue

    if usage.dirty and mono - last_save > SAVE_INTERVAL:
        usage.save()
        last_save = mono

    # Fade the selected key between display refreshes. time.time() only has
    # one second resolution, so the sub-second part comes from the monotonic
    # clock since the last tick.
    if mono - last_led > LED_RATE:
        last_led = mono
        if store.time_based(current_key):
            period = store.period(current_key)
            elapsed = seconds_into_window + (mono - second_mono)
            paint_leds(current_key, remaining=max(0.0, 1.0 - elapsed / period))
        else:
            paint_leds(current_key)  # HOTP codes do not expire

    # Redraw at most once per second, and only what changed.
    now = time.time()
    tt = time.localtime(now)
    if tt.tm_sec != last_second:
        last_second = tt.tm_sec
        second_mono = mono

        fresh = store.code(current_key, now)
        if fresh != totp_code:
            totp_code = fresh
            code.text = fresh

        if store.time_based(current_key):
            step = store.period(current_key)
            seconds_into_window = now % step
            bar = seconds_into_window * 30 // step
        else:
            seconds_into_window = 0
            bar = 30  # HOTP codes are valid until used
        if bar != last_bar:
            last_bar = bar
            progress_bar.value = bar

        if clock_suspect():
            rtc_date.text = "!! CLOCK LOST !!"
        else:
            rtc_date.text = "{:4}/{:02}/{:02}".format(tt.tm_year, tt.tm_mon, tt.tm_mday)

        if USE_12HR:
            hour = tt.tm_hour % 12 or 12
            ampm = "AM" if tt.tm_hour < 12 else "PM"
        else:
            hour = tt.tm_hour
            ampm = ""
        rtc_time.text = "{}:{:02}:{:02} {}".format(hour, tt.tm_min, tt.tm_sec, ampm)
