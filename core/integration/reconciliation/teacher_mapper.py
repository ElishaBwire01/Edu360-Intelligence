"""Conservative teacher identity reconciliation."""

from collections import defaultdict
from typing import Any, Iterable, Mapping

from .conflicts import Conflict, Match
from .defaults import normalized_text, value
from .reports import ReconciliationReport


def reconcile_teachers(
    source_records: Iterable[Mapping[str, Any]],
    target_records: Iterable[Mapping[str, Any]],
) -> ReconciliationReport:
    """Match teachers by staff identifier, then verified email or username."""
    source = list(source_records)
    target = list(target_records)
    report = ReconciliationReport("teachers", len(source), len(target))
    indexes = _indexes(target)

    for record in source:
        source_id = value(record, "id", "pk", "source_id")
        candidates = []
        field = ""
        for candidate_field in ("staff_number", "tsc_number", "email", "username"):
            key = normalized_text(value(record, candidate_field))
            if key:
                candidates = indexes[candidate_field].get(key, [])
                if candidates:
                    field = candidate_field
                    break
        if len(candidates) == 1:
            confidence = {"staff_number": 1.0, "tsc_number": 0.98, "email": 0.9, "username": 0.75}[field]
            report.matches.append(Match(
                source_id,
                _id(candidates[0]),
                field,
                confidence,
                evidence={"identifier": field, "identifier_match": True},
            ))
        elif len(candidates) > 1:
            report.conflicts.append(Conflict(
                source_id,
                "duplicate_target",
                f"{field} maps to multiple target records.",
                tuple(_id(item) for item in candidates),
                confidence=1.0,
                evidence={"identifier": field, "identifier_match": True},
            ))
        else:
            report.conflicts.append(Conflict(
                source_id,
                "unmatched",
                "No stable teacher identity match found; manual review required.",
            ))
    return report


def _indexes(records: list[Mapping[str, Any]]) -> dict[str, dict[str, list[Mapping[str, Any]]]]:
    indexes = {field: defaultdict(list) for field in ("staff_number", "tsc_number", "email", "username")}
    for record in records:
        for field in indexes:
            key = normalized_text(value(record, field))
            if key:
                indexes[field][key].append(record)
    return indexes


def _id(record: Mapping[str, Any]) -> Any:
    return value(record, "id", "pk", "target_id")