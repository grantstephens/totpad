# SPDX-License-Identifier: MIT
#
# Keep CIRCUITPY off the host unless a key is held at power-on, so totp.2fas is
# not readable by anything that the machine plugs into.
#
# Hold the top-left key (KEY1) while plugging in or resetting to mount the
# drive. The serial console and the HID keyboard are unaffected either way.
#
# Recovery, if the drive ever stays hidden: double-tap RESET to reach the
# RP2040 bootloader (RPI-RP2), which can always be written to.

import time

import board
import digitalio
import storage

UNLOCK_KEY = board.KEY1
FLASH_TIME = 0.15  # seconds the status LED stays lit

key = digitalio.DigitalInOut(UNLOCK_KEY)
key.switch_to_input(pull=digitalio.Pull.UP)
time.sleep(0.01)  # let the pull-up settle before the first read
unlocked = not key.value
key.deinit()

if not unlocked:
    storage.disable_usb_drive()

# Green under KEY1 means the drive is mounted, red means it is hidden.
try:
    import neopixel

    pixels = neopixel.NeoPixel(board.NEOPIXEL, 12, brightness=0.2, auto_write=False)
    pixels[0] = (0, 255, 0) if unlocked else (255, 0, 0)
    pixels.show()
    time.sleep(FLASH_TIME)
    pixels.fill(0)
    pixels.show()
    pixels.deinit()
except Exception as err:  # never let the indicator stop the boot
    print("LED status skipped:", err)

print("CIRCUITPY drive", "enabled" if unlocked else "disabled")
