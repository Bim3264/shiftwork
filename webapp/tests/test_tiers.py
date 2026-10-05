"""Tests for tier resolution and the tier view model."""

import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp.core.schedule_input import ScheduleInput, Nurse  # noqa: E402
from webapp.core.tiers import resolve_tier, tier_view  # noqa: E402


class ResolveTierTest(unittest.TestCase):

    def test_declared_tier_in_file(self):
        self.assertEqual(resolve_tier({"tier": "ward"}), ("ward", "declared in file"))

    def test_missing_defaults_to_free(self):
        self.assertEqual(resolve_tier({}), ("free", "default"))

    def test_unknown_declared_tier_defaults_to_free(self):
        self.assertEqual(resolve_tier({"tier": "platinum"}), ("free", "default"))

    def test_recognised_license_key_wins(self):
        # FREE-TRIAL is the seeded key mapping to 'free'.
        self.assertEqual(
            resolve_tier({"license_key": "FREE-TRIAL", "tier": "ward"}),
            ("free", "license key"),
        )


def _ward(tier, n_nurses):
    return ScheduleInput.from_grid(
        ward_meta={"ward_name": "W", "tier": tier},
        settings={"num_days": 7, "weekends": [6, 7]},
        nurses=[Nurse(name=f"N{i}") for i in range(n_nurses)],
    )


class TierViewTest(unittest.TestCase):

    def test_free_tier_gates_meetings_and_caps_nurses(self):
        v = tier_view(_ward("free", 3))
        self.assertEqual(v["name"], "free")
        self.assertFalse(v["meetings_enabled"])
        self.assertEqual(v["max_nurses"], 8)
        # The meetings option is flagged unavailable.
        meetings = next(o for o in v["options"] if "Meeting" in o["label"])
        self.assertFalse(meetings["available"])

    def test_ward_tier_enables_meetings(self):
        v = tier_view(_ward("ward", 3))
        self.assertEqual(v["name"], "ward")
        self.assertTrue(v["meetings_enabled"])
        self.assertEqual(v["max_nurses"], 15)
        meetings = next(o for o in v["options"] if "Meeting" in o["label"])
        self.assertTrue(meetings["available"])

    def test_over_nurse_limit_is_flagged(self):
        v = tier_view(_ward("free", 9))   # free cap is 8
        self.assertTrue(v["over_max"])

    def test_within_limit_not_flagged(self):
        v = tier_view(_ward("free", 8))
        self.assertFalse(v["over_max"])

    def test_rule_toggles_available_on_all_tiers(self):
        v = tier_view(_ward("free", 3))
        toggles = [o for o in v["options"] if "transition" in o["label"]]
        self.assertTrue(toggles and all(o["available"] for o in toggles))


if __name__ == "__main__":
    unittest.main(verbosity=2)
