import gc, os, sys, time
import envelope, totp
gc.collect()
print("CircuitPython:", sys.implementation.version)
print("crypto backend:", envelope.BACKEND, "| verified fast path:", envelope.FAST_CTR)
print("hash backends:", sorted(totp.HASHERS))
try:
    import board, adafruit_ds3231, rtc
    ds = adafruit_ds3231.DS3231(board.STEMMA_I2C())
    # Read the DS3231 itself. The internal RTC resets to 2000-01-01 on every
    # soft reboot, so it says nothing about the battery-backed clock.
    tt = ds.datetime
    rtc.set_time_source(ds)
    print("clock: %04d-%02d-%02d %02d:%02d:%02d UTC, epoch %d, lost_power %s" % (
        tt.tm_year, tt.tm_mon, tt.tm_mday, tt.tm_hour, tt.tm_min, tt.tm_sec,
        time.time(), ds.lost_power))
    print("temperature: %.1f C" % ds.temperature)
except Exception as err:
    print("clock unreadable:", err)
for name in ("/totp.2fas.enc", "/keyfile.bin", "/usage.json", "/hotp.json"):
    try:
        print("%-16s %d bytes" % (name, os.stat(name)[6]))
    except OSError:
        print("%-16s absent" % name)
print("free memory:", gc.mem_free())
