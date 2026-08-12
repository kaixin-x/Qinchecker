"""保存用户逐字段决定，重跑相同批次时优先恢复人工选择。"""

from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path

from qinchecker.models.review import FieldProposal, ProposalAction, ReviewState


PRESERVED_STATES = {
    ReviewState.ACCEPTED_SOURCE,
    ReviewState.KEPT_ORIGINAL,
    ReviewState.MANUALLY_CONFIRMED,
}


class DecisionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def restore(self, proposals: list[FieldProposal]) -> None:
        if not self.path.exists():
            return
        try:
            decisions = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, JSONDecodeError, TypeError, ValueError):
            return
        for proposal in proposals:
            saved = decisions.get(proposal.key)
            if not saved:
                continue
            try:
                state = ReviewState(saved["state"])
                action = ProposalAction(saved["action"])
            except (KeyError, ValueError):
                continue
            if state not in PRESERVED_STATES:
                continue
            proposal.state = state
            proposal.action = action
            proposal.final_value = saved.get("final_value")

    def save(self, proposals: list[FieldProposal]) -> None:
        payload = {
            proposal.key: {
                "state": proposal.state.value,
                "action": proposal.action.value,
                "final_value": proposal.final_value,
            }
            for proposal in proposals
            if proposal.state in PRESERVED_STATES
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
