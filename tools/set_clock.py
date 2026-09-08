#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Set the MacroPad's DS3231 from this host's clock, in UTC.

TOTP tolerates very little drift: most services accept one 30 second window
either side, so a minute of error breaks every code. The device stores UTC and
code.py applies UTC_OFFSET, so this writes UTC regardless of the host time zone.
"""

import argparse
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from push_serial import Device  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-p", "--port", default="/dev/ttyACM0")
    args = parser.parse_args()

    with Device(args.port) as device:
        device.run("import board, adafruit_ds3231, rtc, time\n"
                   "ds = adafruit_ds3231.DS3231(board.STEMMA_I2C())")
        time.sleep(1 - (time.time() % 1) + 0.01)  # land on a second boundary
        now = time.gmtime()
        device.run(
            "ds.datetime = time.struct_time(({}, {}, {}, {}, {}, {}, {}, {}, -1))\n"
            "rtc.set_time_source(ds)".format(
                now.tm_year, now.tm_mon, now.tm_mday, now.tm_hour,
                now.tm_min, now.tm_sec, now.tm_wday, now.tm_yday))
        reported = int(device.run("import time; print(time.time())"))
        drift = reported - int(time.time())
        print("device epoch %d, host epoch %d, drift %+d s" % (reported, int(time.time()), drift))
        if abs(drift) > 2:
            raise SystemExit("drift is still %+d s; something is wrong" % drift)
        print("clock set from UTC %04d-%02d-%02d %02d:%02d:%02d" % (
            now.tm_year, now.tm_mon, now.tm_mday, now.tm_hour, now.tm_min, now.tm_sec))
        device.reload()


if __name__ == "__main__":
    main()
