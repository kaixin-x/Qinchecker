from __future__ import annotations

from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase

from qinchecker.models.review import Confidence, FieldKey, FieldProposal, ReviewState
from qinchecker.services.decision_store import DecisionStore


class DecisionStoreTests(TestCase):
    def proposal(self) -> FieldProposal:
        return FieldProposal(
            worksheet_name="Sheet1",
            excel_row=2,
            species_name="Populus adenopoda",
            field=FieldKey.COUNTIES,
            original_value="户县",
            suggested_value="鄠邑区",
            confidence=Confidence.HIGH,
            state=ReviewState.AUTO_READY,
            final_value="鄠邑区",
        )

    def test_manual_decision_is_restored_on_same_field(self) -> None:
        with TemporaryDirectory() as directory:
            store = DecisionStore(Path(directory) / "decisions.json")
            original = self.proposal()
            original.confirm_manual_value("鄠邑区、周至区")
            store.save([original])
            refreshed = self.proposal()
            store.restore([refreshed])
            self.assertEqual(refreshed.state, ReviewState.MANUALLY_CONFIRMED)
            self.assertEqual(refreshed.final_value, "鄠邑区、周至区")

    def test_automatic_state_is_not_persisted_as_manual_decision(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.json"
            store = DecisionStore(path)
            store.save([self.proposal()])
            self.assertEqual(path.read_text(encoding="utf-8"), "{}")

    def test_reset_restores_captured_initial_state(self) -> None:
        proposal = self.proposal()
        proposal.capture_baseline()
        proposal.confirm_manual_value("鄠邑区、周至区")
        proposal.reset_to_suggestion()
        self.assertEqual(proposal.state, ReviewState.AUTO_READY)
        self.assertEqual(proposal.final_value, "鄠邑区")

    def test_corrupted_decision_file_does_not_block_review(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.json"
            path.write_text("{incomplete", encoding="utf-8")
            proposal = self.proposal()
            DecisionStore(path).restore([proposal])
            self.assertEqual(proposal.state, ReviewState.AUTO_READY)
