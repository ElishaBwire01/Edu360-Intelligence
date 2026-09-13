"""Conservative student identity reconciliation."""

from collections import defaultdict
from typing import Any, Iterable, Mapping

from .conflicts import Conflict, Match
from .defaults import normalized_date, normalized_text, value
from .reports import ReconciliationReport


def reconcile_students(
    source_records: Iterable[Mapping[str, Any]],
    target_records: Iterable[Mapping[str, Any]],
) -> ReconciliationReport:
    """Match source students to host students without writing data.

    Matching order is admission number within school, then name plus date of
    birth within school. Name-only matches are always reported as unmatched.
    """
    source = list(source_records)
    target = list(target_records)
    report = ReconciliationReport("students", len(source), len(target))
    indexes = _indexes(target)

    for record in source:
        source_id = value(record, "id", "pk", "source_id")
        school = normalized_text(value(record, "school_id", "school", "school_code"))
        admission = normalized_text(value(record, "admission_number", "admission", "student_number"))
        name = normalized_text(value(record, "name", "full_name"))
        date_of_birth = normalized_date(value(record, "date_of_birth", "dob"))

        candidates = indexes["admission"].get((school, admission), []) if admission else []
        if len(candidates) == 1:
            report.matches.append(_match(source_id, candidates[0], "school_admission_number", 1.0, record))
            continue
        if len(candidates) > 1:
            report.conflicts.append(Conflict(
                source_id,
                "duplicate_target",
                "Admission number maps to multiple target records.",
                tuple(_id(item) for item in candidates),
                confidence=1.0,
                evidence={"school_match": bool(school), "admission_number_match": True},
            ))
            continue

        candidates = indexes["identity"].get((school, name, date_of_birth), []) if name and date_of_birth else []
        if len(candidates) == 1:
            report.matches.append(_match(source_id, candidates[0], "school_name_date_of_birth", 0.72, record))
        elif len(candidates) > 1:
            report.conflicts.append(Conflict(
                source_id,
                "ambiguous_identity",
                "Name and date of birth match multiple target records.",
                tuple(_id(item) for item in candidates),
                confidence=0.72,
                evidence={"school_match": bool(school), "name_match": True, "date_of_birth_match": True},
            ))
        else:
            report.conflicts.append(Conflict(
                source_id,
                "unmatched",
                "No stable identity match found; manual review required.",
                evidence={"school_present": bool(school), "admission_number_present": bool(admission), "name_present": bool(name), "date_of_birth_present": bool(date_of_birth)},
            ))

    return report


def _indexes(records: list[Mapping[str, Any]]) -> dict[str, dict[tuple[str, ...], list[Mapping[str, Any]]]]:
    indexes = {"admission": defaultdict(list), "identity": defaultdict(list)}
    for record in records:
        school = normalized_text(value(record, "school_id", "school", "school_code"))
        admission = normalized_text(value(record, "admission_number", "admission", "student_number"))
        name = normalized_text(value(record, "name", "full_name"))
        date_of_birth = normalized_date(value(record, "date_of_birth", "dob"))
        if school and admission:
            indexes["admission"][(school, admission)].append(record)
        if school and name and date_of_birth:
            indexes["identity"][(school, name, date_of_birth)].append(record)
    return indexes


def _id(record: Mapping[str, Any]) -> Any:
    return value(record, "id", "pk", "target_id")


def _match(source_id: Any, target: Mapping[str, Any], method: str, confidence: float, source: Mapping[str, Any]) -> Match:
    differences = []
    source_name = normalized_text(value(source, "name", "full_name"))
    target_name = normalized_text(value(target, "name", "full_name"))
    if source_name != target_name:
        differences.append("name")
    return Match(
        source_id,
        _id(target),
        method,
        confidence,
        evidence={
            "school_match": True,
            "admission_number_match": method == "school_admission_number",
            "name_match": bool(source_name and source_name == target_name),
            "date_of_birth_match": method == "school_name_date_of_birth",
        },
        differences=tuple(differences),
    )