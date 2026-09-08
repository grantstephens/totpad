# SPDX-FileCopyrightText: 2021 Carter Nelson for Adafruit Industries
#
# SPDX-License-Identifier: MIT
#
# totpad - a TOTP authenticator for the Adafruit MacroPad RP2040.
#
# Keys are read once at boot from a 2FAS backup (*.2fas) in the root of
# CIRCUITPY. See totp.py for the code generation, and README.md for usage.

import gc
import time

import board
import rtc
import keypad
import rotaryio

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

# --| User Config |--------------------------------------------------------
UTC_OFFSET = 0         # time zone offset
USE_12HR = False       # set 12/24 hour format
DISPLAY_TIMEOUT = 60   # screen saver timeout in seconds
NAME_WIDTH = 20        # characters of key name that fit on screen
KNOB_STEP = -1         # set to 1 to swap the knob direction
CONFIG_FILE = None     # explicit path, or None to auto-detect a *.2fas file
# -------------------------------------------------------------------------

boot_start = time.monotonic()

# -------------------------------------------------------------------------
#                    K E Y    L O A D I N G
# -------------------------------------------------------------------------
store = KeyStore(path=CONFIG_FILE, name_width=NAME_WIDTH, utc_offset=UTC_OFFSET)
NUM_KEYS = len(store)
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
keys = keypad.Keys((board.BUTTON,), value_when_pressed=False, pull=True)
knob = rotaryio.IncrementalEncoder(board.ROTA, board.ROTB)

# -------------------------------------------------------------------------
#                       M A I N
# -------------------------------------------------------------------------
awake = True
knob_pos = knob.position
current_key = 0
totp_code = store.code(0, time.time())

name.text = store.label(0)
code.text = totp_code
last_second = -1
last_bar = -1
wake_up_time = time.monotonic()
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

        if position != knob_pos:
            current_key = (current_key + KNOB_STEP * (position - knob_pos)) % NUM_KEYS
            knob_pos = position
            totp_code = store.code(current_key, time.time())
            name.text = store.label(current_key)
            code.text = totp_code

        if event and event.pressed:
            keyboard_layout.write(totp_code)
            keyboard.send(Keycode.ENTER)

    if not awake:
        # Nothing on screen, so only watch the inputs.
        time.sleep(0.05)
        continue

    if mono - wake_up_time > DISPLAY_TIMEOUT:
        awake = False
        splash.hidden = True
        continue

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
