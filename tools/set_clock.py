#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Set or check the MacroPad's DS3231 against this host's clock, in UTC.

TOTP tolerates very little drift: most services accept one 30 second window
either side, so a minute of error breaks every code. The device stores UTC and
code.py applies UTC_OFFSET, so this writes UTC regardless of the host time zone.

Setting the clock records the moment it happened on the device, so a later
--check can report a drift rate rather than a bare number of seconds. A rate is
what tells you whether the oscillator needs trimming or just resetting.
"""

import argparse
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from push_serial import Device  # noqa: E402


STATE_FILE = "/clock_set.json"


def read_state(device):
    """Return the epoch the clock was last set at, or None."""
    out = device.run(
        "import json\n"
        "try:\n"
        "    print(json.load(open(%r)).get('set_at', 0))\n"
        "except (OSError, ValueError):\n"
        "    print(0)" % STATE_FILE)
    try:
        return int(out) or None
    except ValueError:
        return None


def describe_drift(drift, set_at):
    print("drift: %+d s" % drift)
    if not set_at:
        print("no record of when the clock was last set, so no rate yet")
        return
    elapsed = int(time.time()) - set_at
    days = elapsed / 86400.0
    if elapsed < 3600:
        print("last set %d s ago; leave it a few days for a meaningful rate" % elapsed)
        return
    ppm = drift / elapsed * 1e6
    print("last set %.1f days ago -> %+.2f ppm (%.0f s/year)" % (days, ppm, ppm * 31.536))
    if abs(ppm) > 2:
        print("outside the DS3231's +/-2 ppm spec; the aging offset register could trim this")
    else:
        print("within the DS3231's +/-2 ppm spec; nothing to trim")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-p", "--port", default="/dev/ttyACM0")
    parser.add_argument("--check", action="store_true",
                        help="report the error without changing the clock")
    args = parser.parse_args()

    with Device(args.port) as device:
        if args.check:
            device.run("import board, adafruit_ds3231, time\n"
                       "ds = adafruit_ds3231.DS3231(board.STEMMA_I2C())")
            reported = int(device.run("print(int(time.mktime(ds.datetime)))"))
            flags = device.run("print(ds.lost_power, '%.1f' % ds.temperature)").split()
            print("device UTC epoch %d, host %d" % (reported, int(time.time())))
            describe_drift(reported - int(time.time()), read_state(device))
            print("lost_power: %s, temperature: %s C" % (flags[0], flags[1]))
            device.reload()
            return
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
        device.run("import json\n"
                   "try:\n"
                   "    json.dump({'set_at': %d}, open(%r, 'w'))\n"
                   "except OSError:\n"
                   "    print('note: filesystem read-only, drift rate will not be tracked')"
                   % (int(time.time()), STATE_FILE))
        print("clock set from UTC %04d-%02d-%02d %02d:%02d:%02d" % (
            now.tm_year, now.tm_mon, now.tm_mday, now.tm_hour, now.tm_min, now.tm_sec))
        device.reload()


if __name__ == "__main__":
    main()
