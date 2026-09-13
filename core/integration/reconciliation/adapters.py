"""Source adapters that produce neutral reconciliation records."""

from typing import Any, Iterable, Mapping

from .records import (
    NormalizedStudentRecord,
    NormalizedTeacherRecord,
    student_from_mapping,
    teacher_from_mapping,
)


def adapt_students(
    records: Iterable[Mapping[str, Any]],
    *,
    source: str,
    school_key: Any = None,
) -> list[NormalizedStudentRecord]:
    return [
        student_from_mapping(record, source=source, school_key=school_key)
        for record in records
    ]


def adapt_teachers(
    records: Iterable[Mapping[str, Any]],
    *,
    source: str,
    school_key: Any = None,
) -> list[NormalizedTeacherRecord]:
    return [
        teacher_from_mapping(record, source=source, school_key=school_key)
        for record in records
    ]


def adapt_edugrade_students(
    records: Iterable[Mapping[str, Any]],
    *,
    school_key: Any = None,
) -> list[NormalizedStudentRecord]:
    return adapt_students(records, source="edugrade", school_key=school_key)


def adapt_discipline_students(
    records: Iterable[Mapping[str, Any]],
    *,
    school_key: Any = None,
) -> list[NormalizedStudentRecord]:
    return adapt_students(records, source="discipline", school_key=school_key)


def adapt_edugrade_teachers(
    records: Iterable[Mapping[str, Any]],
    *,
    school_key: Any = None,
) -> list[NormalizedTeacherRecord]:
    return adapt_teachers(records, source="edugrade", school_key=school_key)


def adapt_discipline_teachers(
    records: Iterable[Mapping[str, Any]],
    *,
    school_key: Any = None,
) -> list[NormalizedTeacherRecord]:
    return adapt_teachers(records, source="discipline", school_key=school_key)