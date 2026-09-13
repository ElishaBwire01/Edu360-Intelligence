"""Serializable reconciliation results for review before migration."""

from dataclasses import dataclass, field
from typing import Any

from .conflicts import Conflict, Match


@dataclass
class ReconciliationReport:
    entity: str
    source_count: int
    target_count: int
    matches: list[Match] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def unmatched_count(self) -> int:
        return sum(item.kind == "unmatched" for item in self.conflicts)

    @property
    def review_conflict_count(self) -> int:
        return sum(item.kind != "unmatched" for item in self.conflicts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "source_count": self.source_count,
            "target_count": self.target_count,
            "matched_count": len(self.matches),
            "conflict_count": self.review_conflict_count,
            "unmatched_count": self.unmatched_count,
            "matches": [match.__dict__ for match in self.matches],
            "conflicts": [conflict.__dict__ for conflict in self.conflicts],
        }