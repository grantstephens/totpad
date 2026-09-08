# SPDX-License-Identifier: MIT
"""Usage counters and shortcut key assignment for totpad.

Counters are keyed on the account label rather than its list position, so they
survive adding, removing or reordering entries in the 2FAS backup.

Kept free of hardware imports so it runs on CPython for the test suite.
"""

import json

# Twelve hand-picked, visually distinct colours. A colour belongs to an account
# rather than to a key position, so it travels with the account as its ranking
# changes. Evenly spaced hues put yellow next to chartreuse, which is hard to
# tell apart at the low brightness these LEDs run at.
KEY_COLORS = (
    (255, 0, 0),      # red
    (255, 72, 0),     # orange
    (255, 190, 0),    # amber
    (150, 255, 0),    # lime
    (0, 255, 0),      # green
    (0, 255, 130),    # spring
    (0, 220, 255),    # cyan
    (0, 90, 255),     # azure
    (60, 0, 255),     # blue
    (150, 0, 255),    # violet
    (255, 0, 200),    # magenta
    (255, 255, 255),  # white
)


def stable_hash(text):
    """FNV-1a over the UTF-8 bytes.

    Python's own hash is salted per process on CPython, so it cannot be used to
    derive a colour that has to look the same after every reboot.
    """
    h = 0x811C9DC5
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return h


def color_map(labels, palette=KEY_COLORS):
    """Pick a colour per label, keyed on the label itself.

    An account keeps its colour as it moves up and down the rankings, so the
    colour identifies the account rather than the key position. Each label
    prefers the palette entry its hash lands on, and clashes probe forward.

    Resolution walks the labels alphabetically, not in ranking order, so a
    change in ranking alone never repaints anything. Colours can still shift
    when the set of shortcut accounts changes and a clash resolves differently.

    Labels beyond the size of the palette get no colour, so pass in at most
    ``len(palette)`` of them.
    """
    taken = {}
    for name in sorted(labels, key=lambda l: l.lower()):
        preferred = stable_hash(name) % len(palette)
        for probe in range(len(palette)):
            slot = (preferred + probe) % len(palette)
            if slot not in taken:
                taken[slot] = name
                break
        else:
            break  # palette exhausted
    return {name: palette[slot] for slot, name in taken.items()}


class UsageTracker:
    """Press counts per label, with lazy persistence to a JSON file.

    Writes are deferred: call ``save`` when convenient (on idle, or before
    sleeping) rather than on every press, to spare the flash.
    """

    def __init__(self, path="/usage.json"):
        self.path = path
        self.counts = {}
        self.writable = True
        self.dirty = False
        self._load()

    def _load(self):
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return  # missing or corrupt, start from zero
        if isinstance(data, dict):
            for label, count in data.items():
                if isinstance(count, int) and count > 0:
                    self.counts[label] = count

    def bump(self, label):
        """Record one use of a label and return its new count."""
        self.counts[label] = self.counts.get(label, 0) + 1
        self.dirty = True
        return self.counts[label]

    def count(self, label):
        return self.counts.get(label, 0)

    def save(self):
        """Persist counts if anything changed. Returns True if written.

        The filesystem is read-only to code whenever the USB drive is exposed,
        so a failure here is expected and not worth reporting upward.
        """
        if not self.dirty or not self.writable:
            return False
        try:
            with open(self.path, "w") as f:
                json.dump(self.counts, f)
        except OSError:
            self.writable = False  # drive is mounted on a host; stop retrying
            return False
        self.dirty = False
        return True

    def ranked(self, labels):
        """Order ``labels`` by descending use, then alphabetically.

        Labels never used sort last, alphabetically, so a fresh device still
        offers a full set of shortcuts instead of twelve dark keys.
        """
        return sorted(labels, key=lambda l: (-self.counts.get(l, 0), l.lower()))

    def shortcuts(self, labels, slots):
        """Map the most used labels onto ``slots`` keys.

        Returns a list of labels, shortest of ``slots`` and ``len(labels)``,
        where position 0 is the first physical key.
        """
        return self.ranked(labels)[:slots]
