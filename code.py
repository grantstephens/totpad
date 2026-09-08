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

from totp import KeyStore
from usage import UsageTracker, KEY_COLORS

# --| User Config |--------------------------------------------------------
UTC_OFFSET = 0          # time zone offset
USE_12HR = False        # set 12/24 hour format
DISPLAY_TIMEOUT = 60    # screen saver timeout in seconds
NAME_WIDTH = 20         # characters of key name that fit on screen
KNOB_STEP = -1          # set to 1 to swap the knob direction
CONFIG_FILE = None      # explicit path, or None to auto-detect a *.2fas file
USAGE_FILE = "/usage.json"
LED_BRIGHTNESS = 0.15   # shortcut key brightness, 0 to disable the LEDs
UNSELECTED_DIM = 0.3    # unselected shortcut keys, as a fraction of full colour
SAVE_INTERVAL = 30      # seconds between usage counter writes at most
# -------------------------------------------------------------------------

boot_start = time.monotonic()

# -------------------------------------------------------------------------
#                    K E Y    L O A D I N G
# -------------------------------------------------------------------------
store = KeyStore(path=CONFIG_FILE, name_width=NAME_WIDTH, utc_offset=UTC_OFFSET)
NUM_KEYS = len(store)

# Shortcuts are assigned once, from the counts as they were at boot, so the
# keys do not rearrange themselves under your fingers mid-session. Today's
# presses take effect at the next boot.
usage = UsageTracker(USAGE_FILE)
labels = [store.label(i) for i in range(NUM_KEYS)]
index_of = {name: i for i, name in enumerate(labels)}
shortcut_keys = [index_of[name] for name in usage.shortcuts(labels, len(KEY_COLORS))]
shortcut_slot = {key: slot for slot, key in enumerate(shortcut_keys)}
gc.collect()

# set board to use the DS3231 as its RTC
i2c = board.STEMMA_I2C()
rtc.set_time_source(adafruit_ds3231.DS3231(i2c))

# -------------------------------------------------------------------------
#                    D I S P L A Y    S E T U P
# -------------------------------------------------------------------------
display = board.DISPLAY

# Secret Code font by Matthew Welch
# http://www.squaregear.net/fonts/
font = bitmap_font.load_font("/secrcode_28.bdf")
font.load_glyphs(b"0123456789-")

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

# Fixed 0..30 scale; odd periods are rescaled onto it below.
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


def paint_leds(selected, lit=True):
    """Colour the assigned keys, dimming the ones that are not selected.

    Dimming rather than brightening, because the palette already sits at full
    channel values and boosting a saturated colour just clips.
    """
    if not lit or not LED_BRIGHTNESS:
        pixels.fill(0)
        pixels.show()
        return
    for slot in range(12):
        if slot < len(shortcut_keys):
            color = KEY_COLORS[slot]
            if shortcut_keys[slot] != selected:
                color = scaled(color, UNSELECTED_DIM)
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
    return otp


awake = True
knob_pos = knob.position
# Start on the most used account, which is also shortcut slot 0.
current_key = shortcut_keys[0] if shortcut_keys else 0
totp_code = store.code(current_key, time.time())

name.text = store.label(current_key)
code.text = totp_code
last_second = -1
last_bar = -1
now_mono = time.monotonic()
wake_up_time = now_mono
last_save = now_mono
paint_leds(current_key)
gc.collect()

while True:
    mono = time.monotonic()
    event = keys.events.get()
    position = knob.position

    if event or position != knob_pos:
        wake_up_time = mono
        if not awake:
            awake = True
            splash.hidden = False
            last_second = -1
            paint_leds(current_key)

        if position != knob_pos:
            current_key = (current_key + KNOB_STEP * (position - knob_pos)) % NUM_KEYS
            knob_pos = position
            totp_code = store.code(current_key, time.time())
            name.text = store.label(current_key)
            code.text = totp_code
            paint_leds(current_key)

        if event and event.pressed:
            if event.key_number == KNOB_BUTTON:
                totp_code = type_code(current_key, time.time())
            elif event.key_number < len(shortcut_keys):
                # A shortcut key selects its account as well as typing it, so
                # the screen always shows what was just sent.
                current_key = shortcut_keys[event.key_number]
                totp_code = type_code(current_key, time.time())
                name.text = store.label(current_key)
                code.text = totp_code
                # Flash the key that was hit, then settle to its own colour.
                pixels[event.key_number] = (255, 255, 255)
                pixels.show()
                last_second = -1
            else:
                continue  # unassigned key, nothing to type

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

    # Redraw at most once per second, and only what changed.
    now = time.time()
    tt = time.localtime(now)
    if tt.tm_sec != last_second:
        last_second = tt.tm_sec

        fresh = store.code(current_key, now)
        if fresh != totp_code:
            totp_code = fresh
            code.text = fresh

        step = store.period(current_key)
        bar = (now % step) * 30 // step
        if bar != last_bar:
            last_bar = bar
            progress_bar.value = bar

        if USE_12HR:
            hour = tt.tm_hour % 12 or 12
            ampm = "AM" if tt.tm_hour < 12 else "PM"
        else:
            hour = tt.tm_hour
            ampm = ""
        rtc_date.text = "{:4}/{:02}/{:02}".format(tt.tm_year, tt.tm_mon, tt.tm_mday)
        rtc_time.text = "{}:{:02}:{:02} {}".format(hour, tt.tm_min, tt.tm_sec, ampm)
