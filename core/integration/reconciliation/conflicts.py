"""Conflict types emitted by reconciliation; none of these writes data."""

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Match:
    source_id: Any
    target_id: Any
    method: str
    confidence: float
    evidence: Mapping[str, Any] = field(default_factory=dict)
    differences: tuple[str, ...] = ()
    confidence_kind: str = "policy_score"


@dataclass(frozen=True)
class Conflict:
    source_id: Any
    kind: str
    message: str
    candidates: tuple[Any, ...] = ()
    confidence: float = 0.0
    confidence_kind: str = "policy_score"
    evidence: Mapping[str, Any] = field(default_factory=dict)
    details: Mapping[str, Any] = field(default_factory=dict)