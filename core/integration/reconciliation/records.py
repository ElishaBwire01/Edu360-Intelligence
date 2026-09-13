"""Normalized records shared by source adapters and reconciliation rules."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from .defaults import normalized_date, normalized_text, value


@dataclass(frozen=True)
class NormalizedStudentRecord:
    source: str
    source_id: Any
    admission_number: str | None
    full_name: str | None
    date_of_birth: str | None
    school_key: str | None
    stream_key: str | None
    raw: Mapping[str, Any] = field(default_factory=dict)
    quality_flags: tuple[str, ...] = ()

    def as_mapping(self) -> Mapping[str, Any]:
        return {
            "id": self.source_id,
            "source": self.source,
            "admission_number": self.admission_number,
            "name": self.full_name,
            "date_of_birth": self.date_of_birth,
            "school_id": self.school_key,
            "stream_id": self.stream_key,
            "quality_flags": self.quality_flags,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class NormalizedTeacherRecord:
    source: str
    source_id: Any
    staff_number: str | None
    tsc_number: str | None
    email: str | None
    username: str | None
    full_name: str | None
    school_key: str | None
    raw: Mapping[str, Any] = field(default_factory=dict)
    quality_flags: tuple[str, ...] = ()

    def as_mapping(self) -> Mapping[str, Any]:
        return {
            "id": self.source_id,
            "source": self.source,
            "staff_number": self.staff_number,
            "tsc_number": self.tsc_number,
            "email": self.email,
            "username": self.username,
            "name": self.full_name,
            "school_id": self.school_key,
            "quality_flags": self.quality_flags,
            "raw": self.raw,
        }


def quality_summary(records: list[NormalizedStudentRecord | NormalizedTeacherRecord]) -> dict[str, int]:
    """Count explicit data gaps without treating them as identity decisions."""
    summary: dict[str, int] = {}
    for record in records:
        for flag in record.quality_flags:
            summary[flag] = summary.get(flag, 0) + 1
    return summary


def _clean(raw: Any) -> str | None:
    text = normalized_text(raw)
    return text or None


def _display(raw: Any) -> str | None:
    if raw in (None, ""):
        return None
    return " ".join(str(raw).split())


def student_from_mapping(
    record: Mapping[str, Any],
    *,
    source: str,
    school_key: Any = None,
) -> NormalizedStudentRecord:
    first_name = value(record, "first_name", "given_name")
    last_name = value(record, "last_name", "family_name")
    full_name = value(record, "name", "full_name") or " ".join(
        part for part in (first_name, last_name) if part
    )
    school = school_key if school_key not in (None, "") else value(record, "school_id", "school", "school_code")
    admission = _clean(value(record, "admission_number", "admission", "student_number"))
    name = _display(full_name)
    date_of_birth = normalized_date(value(record, "date_of_birth", "dob")) or None
    flags = []
    if not school:
        flags.append("missing_school_scope")
    if not admission:
        flags.append("missing_admission_number")
    if not date_of_birth:
        flags.append("missing_date_of_birth")
    if not name:
        flags.append("missing_name")
    return NormalizedStudentRecord(
        source=source,
        source_id=value(record, "id", "pk", "source_id"),
        admission_number=admission,
        full_name=name,
        date_of_birth=date_of_birth,
        school_key=_clean(school),
        stream_key=_clean(value(record, "stream_id", "current_stream_id", "stream", "stream_code")),
        raw=dict(record),
        quality_flags=tuple(flags),
    )


def teacher_from_mapping(
    record: Mapping[str, Any],
    *,
    source: str,
    school_key: Any = None,
) -> NormalizedTeacherRecord:
    school = school_key if school_key not in (None, "") else value(record, "school_id", "school", "school_code")
    name = _display(value(record, "name", "full_name") or " ".join(
        part for part in (value(record, "first_name"), value(record, "last_name")) if part
    ))
    fields = {
        "staff_number": _clean(value(record, "staff_number", "employee_number")),
        "tsc_number": _clean(value(record, "tsc_number")),
        "email": _clean(value(record, "email")),
        "username": _clean(value(record, "username")),
    }
    flags = []
    if not school:
        flags.append("missing_school_scope")
    if not any(fields.values()):
        flags.append("missing_teacher_identifier")
    if not name:
        flags.append("missing_name")
    return NormalizedTeacherRecord(
        source=source,
        source_id=value(record, "id", "pk", "source_id"),
        full_name=name,
        school_key=_clean(school),
        raw=dict(record),
        quality_flags=tuple(flags),
        **fields,
    )


# Compatibility aliases for callers created before the terminology correction.
CanonicalStudentRecord = NormalizedStudentRecord
CanonicalTeacherRecord = NormalizedTeacherRecord