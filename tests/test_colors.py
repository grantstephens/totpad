# SPDX-License-Identifier: MIT
"""Tests that an account's colour follows the account, not the key position."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from usage import KEY_COLORS, UsageTracker, color_map, stable_hash  # noqa: E402

LABELS = [
    "Atlassian", "AWS", "CEX.IO", "Cloudflare", "Discord", "Facebook",
    "GitHub", "Gitlab", "Google", "Hetzner", "LinkedIn", "Stripe",
]


def tracker(counts):
    path = os.path.join(tempfile.mkdtemp(), "usage.json")
    with open(path, "w") as f:
        json.dump(counts, f)
    return UsageTracker(path)


class TestStableHash(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(stable_hash("GitHub"), stable_hash("GitHub"))

    def test_known_values(self):
        # FNV-1a 32-bit reference values, so a refactor cannot silently
        # repaint every key.
        self.assertEqual(stable_hash(""), 0x811C9DC5)
        self.assertEqual(stable_hash("a"), 0xE40C292C)
        self.assertEqual(stable_hash("foobar"), 0xBF9CF968)

    def test_differs_between_labels(self):
        self.assertNotEqual(stable_hash("GitHub"), stable_hash("Gitlab"))

    def test_within_32_bits(self):
        for name in LABELS:
            self.assertLess(stable_hash(name), 1 << 32)


class TestColorMap(unittest.TestCase):
    def test_every_label_gets_a_colour(self):
        colors = color_map(LABELS)
        self.assertEqual(len(colors), len(LABELS))

    def test_colours_are_unique(self):
        colors = color_map(LABELS)
        self.assertEqual(len(set(colors.values())), len(colors))

    def test_colours_come_from_the_palette(self):
        for color in color_map(LABELS).values():
            self.assertIn(color, KEY_COLORS)

    def test_deterministic_across_calls(self):
        self.assertEqual(color_map(LABELS), color_map(LABELS))

    def test_independent_of_input_order(self):
        shuffled = list(reversed(LABELS))
        self.assertEqual(color_map(LABELS), color_map(shuffled))

    def test_single_label(self):
        self.assertEqual(len(color_map(["GitHub"])), 1)

    def test_empty(self):
        self.assertEqual(color_map([]), {})

    def test_more_labels_than_palette_is_capped(self):
        many = ["label%02d" % i for i in range(30)]
        colors = color_map(many)
        self.assertEqual(len(colors), len(KEY_COLORS))
        self.assertEqual(len(set(colors.values())), len(KEY_COLORS))


class TestColourFollowsAccount(unittest.TestCase):
    """The behaviour asked for: rank changes must not repaint an account."""

    def test_colour_survives_promotion(self):
        quiet = tracker({})
        busy = tracker({"Stripe": 99, "LinkedIn": 40})

        before = color_map(quiet.shortcuts(LABELS, 12))
        after = color_map(busy.shortcuts(LABELS, 12))
        self.assertEqual(before, after)

    def test_colour_survives_full_reversal(self):
        ascending = tracker({name: i + 1 for i, name in enumerate(LABELS)})
        descending = tracker({name: len(LABELS) - i for i, name in enumerate(LABELS)})

        first = color_map(ascending.shortcuts(LABELS, 12))
        second = color_map(descending.shortcuts(LABELS, 12))
        self.assertEqual(first, second)

    def test_slot_changes_but_colour_does_not(self):
        """GitHub moving from a low slot to slot 0 keeps its colour."""
        quiet = tracker({})
        slots_before = quiet.shortcuts(LABELS, 12)
        color_before = color_map(slots_before)["GitHub"]

        busy = tracker({"GitHub": 100})
        slots_after = busy.shortcuts(LABELS, 12)
        color_after = color_map(slots_after)["GitHub"]

        self.assertNotEqual(slots_before.index("GitHub"), slots_after.index("GitHub"))
        self.assertEqual(slots_after.index("GitHub"), 0)
        self.assertEqual(color_before, color_after)

    def test_every_promotion_step_keeps_colours(self):
        """Bump one account repeatedly; nobody's colour may change."""
        t = tracker({})
        baseline = color_map(t.shortcuts(LABELS, 12))
        for _ in range(25):
            t.bump("Hetzner")
            self.assertEqual(color_map(t.shortcuts(LABELS, 12)), baseline)

    def test_colour_is_stable_for_each_account_individually(self):
        t = tracker({})
        baseline = color_map(t.shortcuts(LABELS, 12))
        for name in LABELS:
            t.bump(name)
            current = color_map(t.shortcuts(LABELS, 12))
            self.assertEqual(current[name], baseline[name], name)


class TestSetMembershipCaveat(unittest.TestCase):
    """A documented limit: colours are stable per set, not across sets."""

    def test_shared_labels_usually_keep_their_colour(self):
        subset = LABELS[:6]
        full = color_map(LABELS)
        partial = color_map(subset)
        kept = sum(1 for name in subset if full[name] == partial[name])
        self.assertGreaterEqual(kept, len(subset) - 2)


if __name__ == "__main__":
    unittest.main()
