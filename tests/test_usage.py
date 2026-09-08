# SPDX-License-Identifier: MIT
"""Tests for the totpad usage counters and shortcut assignment."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import usage  # noqa: E402
from usage import KEY_COLORS, UsageTracker  # noqa: E402


class TrackerTestCase(unittest.TestCase):
    def tracker(self, counts=None, name="usage.json"):
        path = os.path.join(tempfile.mkdtemp(), name)
        if counts is not None:
            with open(path, "w") as f:
                json.dump(counts, f)
        return UsageTracker(path)


class TestColors(TrackerTestCase):
    def test_one_color_per_key(self):
        self.assertEqual(len(KEY_COLORS), 12)

    def test_colors_are_unique(self):
        self.assertEqual(len(set(KEY_COLORS)), len(KEY_COLORS))

    def test_colors_are_valid_and_visible(self):
        for color in KEY_COLORS:
            self.assertEqual(len(color), 3)
            for channel in color:
                self.assertGreaterEqual(channel, 0)
                self.assertLessEqual(channel, 255)
            self.assertGreater(sum(color), 100, color)  # not near-black


class TestCounting(TrackerTestCase):
    def test_bump_returns_running_total(self):
        t = self.tracker()
        self.assertEqual(t.bump("GitHub"), 1)
        self.assertEqual(t.bump("GitHub"), 2)
        self.assertEqual(t.count("GitHub"), 2)

    def test_unknown_label_is_zero(self):
        self.assertEqual(self.tracker().count("Nope"), 0)

    def test_bump_marks_dirty(self):
        t = self.tracker()
        self.assertFalse(t.dirty)
        t.bump("GitHub")
        self.assertTrue(t.dirty)


class TestRanking(TrackerTestCase):
    LABELS = ["Atlassian", "AWS", "GitHub", "Stripe", "Zebra"]

    def test_most_used_first(self):
        t = self.tracker({"Stripe": 5, "GitHub": 9, "Zebra": 1})
        self.assertEqual(t.ranked(self.LABELS)[:3], ["GitHub", "Stripe", "Zebra"])

    def test_unused_sort_last_alphabetically(self):
        t = self.tracker({"Zebra": 3})
        self.assertEqual(t.ranked(self.LABELS), ["Zebra", "Atlassian", "AWS", "GitHub", "Stripe"])

    def test_ties_break_alphabetically_ignoring_case(self):
        t = self.tracker({"Stripe": 4, "atlassian": 4, "GitHub": 4})
        ranked = t.ranked(["Stripe", "atlassian", "GitHub"])
        self.assertEqual(ranked, ["atlassian", "GitHub", "Stripe"])

    def test_fresh_device_ranks_alphabetically(self):
        t = self.tracker()
        self.assertEqual(t.ranked(self.LABELS), sorted(self.LABELS, key=str.lower))

    def test_ranking_is_stable_across_calls(self):
        t = self.tracker({"GitHub": 2})
        self.assertEqual(t.ranked(self.LABELS), t.ranked(self.LABELS))


class TestShortcuts(TrackerTestCase):
    def test_limited_to_slot_count(self):
        labels = ["k%02d" % i for i in range(30)]
        self.assertEqual(len(self.tracker().shortcuts(labels, 12)), 12)

    def test_fewer_labels_than_slots(self):
        self.assertEqual(self.tracker().shortcuts(["one", "two"], 12), ["one", "two"])

    def test_no_labels(self):
        self.assertEqual(self.tracker().shortcuts([], 12), [])

    def test_slot_zero_is_most_used(self):
        t = self.tracker({"Rare": 1, "Common": 50})
        self.assertEqual(t.shortcuts(["Rare", "Common", "Never"], 12)[0], "Common")

    def test_shortcuts_are_unique(self):
        t = self.tracker({"GitHub": 3, "AWS": 3})
        picks = t.shortcuts(["GitHub", "AWS", "Stripe"], 12)
        self.assertEqual(len(set(picks)), len(picks))

    def test_usage_promotes_a_label(self):
        labels = ["Alpha", "Beta", "Gamma"]
        t = self.tracker()
        self.assertEqual(t.shortcuts(labels, 2), ["Alpha", "Beta"])
        for _ in range(3):
            t.bump("Gamma")
        self.assertEqual(t.shortcuts(labels, 2), ["Gamma", "Alpha"])


class TestPersistence(TrackerTestCase):
    def test_round_trip(self):
        t = self.tracker()
        t.bump("GitHub")
        t.bump("GitHub")
        t.bump("AWS")
        self.assertTrue(t.save())

        reloaded = UsageTracker(t.path)
        self.assertEqual(reloaded.count("GitHub"), 2)
        self.assertEqual(reloaded.count("AWS"), 1)

    def test_save_is_a_noop_when_clean(self):
        t = self.tracker()
        self.assertFalse(t.save())
        t.bump("GitHub")
        self.assertTrue(t.save())
        self.assertFalse(t.save())  # already flushed

    def test_missing_file_starts_empty(self):
        t = self.tracker()
        self.assertEqual(t.counts, {})

    def test_corrupt_file_starts_empty(self):
        path = os.path.join(tempfile.mkdtemp(), "usage.json")
        with open(path, "w") as f:
            f.write("{not json at all")
        t = UsageTracker(path)
        self.assertEqual(t.counts, {})
        t.bump("GitHub")
        self.assertTrue(t.save())  # and can still be written over

    def test_junk_values_ignored(self):
        t = self.tracker({"Good": 3, "Bad": "lots", "Zero": 0, "Negative": -5})
        self.assertEqual(t.counts, {"Good": 3})

    def test_non_dict_file_ignored(self):
        t = self.tracker(["not", "a", "dict"])
        self.assertEqual(t.counts, {})

    def test_read_only_filesystem_degrades_quietly(self):
        t = self.tracker()
        t.bump("GitHub")
        t.path = "/proc/totpad-cannot-write/usage.json"  # guaranteed unwritable
        self.assertFalse(t.save())
        self.assertFalse(t.writable)
        self.assertTrue(t.dirty)  # still counted in memory
        self.assertEqual(t.count("GitHub"), 1)
        self.assertFalse(t.save())  # does not keep retrying


class TestAssignmentWiring(TrackerTestCase):
    """The index mapping code.py builds, exercised without hardware imports."""

    def test_slots_map_to_store_indices(self):
        labels = ["Atlassian", "AWS", "GitHub", "Stripe"]
        t = self.tracker({"Stripe": 7, "AWS": 2})
        index_of = {name: i for i, name in enumerate(labels)}
        shortcut_keys = [index_of[n] for n in t.shortcuts(labels, len(KEY_COLORS))]

        self.assertEqual(shortcut_keys[0], labels.index("Stripe"))
        self.assertEqual(shortcut_keys[1], labels.index("AWS"))
        self.assertEqual(len(set(shortcut_keys)), len(shortcut_keys))
        for key in shortcut_keys:
            self.assertLess(key, len(labels))

    def test_every_slot_has_a_colour(self):
        labels = ["k%02d" % i for i in range(40)]
        picks = self.tracker().shortcuts(labels, len(KEY_COLORS))
        self.assertLessEqual(len(picks), len(KEY_COLORS))


if __name__ == "__main__":
    unittest.main()
