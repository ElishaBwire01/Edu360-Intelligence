"""Orchestrate a complete dry-run identity report."""

from typing import Any, Iterable, Mapping

from .reports import ReconciliationReport
from .student_mapper import reconcile_students
from .teacher_mapper import reconcile_teachers


def reconcile_bundle(
    *,
    source_students: Iterable[Mapping[str, Any]],
    target_students: Iterable[Mapping[str, Any]],
    source_teachers: Iterable[Mapping[str, Any]],
    target_teachers: Iterable[Mapping[str, Any]],
) -> dict[str, ReconciliationReport]:
    """Return student and teacher reports without changing either source."""
    return {
        "students": reconcile_students(source_students, target_students),
        "teachers": reconcile_teachers(source_teachers, target_teachers),
    }