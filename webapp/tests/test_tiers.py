"""Tests for tier resolution and the tier view model."""

import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp.core.schedule_input import ScheduleInput, Nurse  # noqa: E402
from webapp.core.tiers import enforce_max_nurses, resolve_tier, tier_view  # noqa: E402


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


def _classed_ward(tier, rn, pn, na=0):
    nurses = [Nurse(name=f"R{i}", staff_class="rn") for i in range(rn)]
    nurses += [Nurse(name=f"P{i}", staff_class="pn") for i in range(pn)]
    nurses += [Nurse(name=f"A{i}", staff_class="na") for i in range(na)]
    return ScheduleInput.from_grid(
        ward_meta={"ward_name": "W", "tier": tier},
        settings={"num_days": 7, "weekends": [6, 7]},
        nurses=nurses,
    )


class CombinedNurseCapTest(unittest.TestCase):
    """The nurse ceiling is ONE figure across all staff groups. DataImporter only
    sees one group's file at a time, so this guard is the only thing stopping a
    ward from getting a separate allowance per group."""

    def test_combined_tier_cap_blocks_split(self):
        si = _classed_ward("free", rn=5, pn=5)      # free cap is 8
        # Each half alone would pass the per-file cap - that is the whole point.
        self.assertLessEqual(len(si.active_nurses("rn")), 8)
        self.assertLessEqual(len(si.active_nurses("pn_na")), 8)
        with self.assertRaises(ValueError) as cm:
            enforce_max_nurses(si)
        self.assertIn("10", str(cm.exception))
        self.assertIn("8", str(cm.exception))

        v = tier_view(si)
        self.assertEqual(v["by_group"], {"rn": 5, "pn_na": 5})
        self.assertEqual(v["nurse_count"], 10)
        self.assertTrue(v["over_max"])

    def test_nurse_assistants_count_toward_the_cap(self):
        si = _classed_ward("free", rn=4, pn=3, na=2)   # 9 > 8
        with self.assertRaises(ValueError):
            enforce_max_nurses(si)
        v = tier_view(si)
        self.assertEqual(v["by_group"], {"rn": 4, "pn_na": 5})   # PN + NA together
        self.assertEqual(v["nurse_count"], 9)
        self.assertTrue(v["over_max"])

    def test_exactly_at_cap_is_allowed(self):
        si = _classed_ward("free", rn=4, pn=2, na=2)   # 8 == cap
        enforce_max_nurses(si)                         # must not raise
        v = tier_view(si)
        self.assertEqual(v["nurse_count"], 8)
        self.assertFalse(v["over_max"])

    def test_higher_tier_accepts_the_same_roster(self):
        si = _classed_ward("ward", rn=5, pn=5)         # ward cap is 15
        enforce_max_nurses(si)
        self.assertFalse(tier_view(si)["over_max"])

    def test_inactive_nurses_do_not_count(self):
        si = _classed_ward("free", rn=5, pn=5)
        for n in si.nurses[:2]:                        # 10 -> 8 active
            n.active = False
        enforce_max_nurses(si)
        v = tier_view(si)
        self.assertEqual(v["nurse_count"], 8)
        self.assertFalse(v["over_max"])
        self.assertEqual(v["by_group"], {"rn": 3, "pn_na": 5})

    def test_unclassed_ward_reports_empty_groups(self):
        v = tier_view(_ward("free", 3))
        self.assertEqual(v["by_group"], {"rn": 0, "pn_na": 0})
        self.assertEqual(v["nurse_count"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
