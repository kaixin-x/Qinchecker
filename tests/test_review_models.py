from __future__ import annotations

import unittest
from qinchecker.models import (
    Confidence,
    FieldKey,
    FieldProposal,
    ProposalAction,
    ReviewState,
)


class FieldProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.proposal = FieldProposal(
            worksheet_name="Sheet1",
            excel_row=2,
            species_name="Ginkgo biloba",
            field=FieldKey.COUNTIES,
            original_value="洋县",
            suggested_value="洋县、佛坪",
            confidence=Confidence.HIGH,
            source_name="iPlant 物种县级分布",
            source_url="https://www.iplant.cn/info/Ginkgo%20biloba",
        )

    def test_high_confidence_can_be_auto_ready(self) -> None:
        self.proposal.set_auto_ready()
        self.assertEqual(self.proposal.final_value, "洋县、佛坪")
        self.assertEqual(self.proposal.state, ReviewState.AUTO_READY)

    def test_manual_decision_overrides_suggestion(self) -> None:
        self.proposal.confirm_manual_value("洋县、佛坪、周至")
        self.assertEqual(self.proposal.state, ReviewState.MANUALLY_CONFIRMED)
        self.assertEqual(self.proposal.action, ProposalAction.MANUAL_EDIT)
        self.assertTrue(self.proposal.has_change)

    def test_keep_original_does_not_count_as_change(self) -> None:
        self.proposal.keep_original()
        self.assertEqual(self.proposal.state, ReviewState.KEPT_ORIGINAL)
        self.assertFalse(self.proposal.has_change)

    def test_non_high_confidence_cannot_be_auto_ready(self) -> None:
        self.proposal.confidence = Confidence.MEDIUM
        with self.assertRaises(ValueError):
            self.proposal.set_auto_ready()




if __name__ == "__main__":
    unittest.main()
