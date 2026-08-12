"""可恢复核对会话的模型。持久化服务将在后续阶段实现。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from .review import FieldProposal, ReviewState


@dataclass(slots=True)
class SessionMetadata:
    input_file_name: str
    worksheet_name: str
    start_excel_row: int
    requested_count: int | None
    session_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass(slots=True)
class ReviewSession:
    metadata: SessionMetadata
    proposals: list[FieldProposal] = field(default_factory=list)

    def proposal_counts(self) -> dict[ReviewState, int]:
        counts = {state: 0 for state in ReviewState}
        for proposal in self.proposals:
            counts[proposal.state] += 1
        return counts

    def proposals_for_row(self, excel_row: int) -> list[FieldProposal]:
        return [item for item in self.proposals if item.excel_row == excel_row]

    def pending_proposals(self) -> list[FieldProposal]:
        return [
            item
            for item in self.proposals
            if item.state in {ReviewState.PENDING_REVIEW, ReviewState.FAILED}
        ]
