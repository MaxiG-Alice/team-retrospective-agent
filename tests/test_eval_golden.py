"""Golden dataset regression checks for the Team Retrospective Agent."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


class GoldenEvalDatasetTests(unittest.TestCase):
    """Validates golden evaluation dataset integrity and guardrail test cases."""

    def test_evalset_structure_and_cases(self) -> None:
        eval_path = Path(__file__).resolve().parent.parent / "evals" / "retro_eval.evalset.json"
        self.assertTrue(eval_path.exists(), "Golden evalset file must exist.")
        data = json.loads(eval_path.read_text(encoding="utf-8"))
        self.assertEqual(data["eval_set_id"], "retro_golden_evalset")
        ids = {case["eval_id"] for case in data["eval_cases"]}
        self.assertIn("case_01_create_campaign", ids)
        self.assertIn("case_02_anonymity_guardrail_refusal", ids)
        self.assertIn("case_03_release_gate_blocked", ids)


if __name__ == "__main__":
    unittest.main()
