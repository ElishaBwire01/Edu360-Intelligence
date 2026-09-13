"""Read-only extraction and evidence-report orchestration."""

from pathlib import Path
from typing import Any

from .adapters import adapt_students, adapt_teachers
from .bundle import reconcile_bundle
from .records import quality_summary
from .sqlite_source import assert_snapshot_unchanged, read_records, snapshot_manifest


STUDENT_QUERIES = {
    "edugrade": (
        "SELECT id, admission_number, first_name, middle_name, last_name, "
        "date_of_birth, current_stream_id AS stream_id "
        "FROM students"
    ),
    "discipline": (
        "SELECT id, admission_number, name, stream_id "
        "FROM core_student"
    ),
}

TEACHER_QUERIES = {
    "edugrade": (
        "SELECT teacher.id, teacher.staff_number, teacher.tsc_number, "
        "teacher.first_name, teacher.last_name, user.email, user.username "
        "FROM teacher_profiles AS teacher "
        "LEFT JOIN auth_user AS user ON user.id = teacher.user_id"
    ),
    "discipline": (
        "SELECT teacher.id, user.email, user.username, user.first_name, "
        "user.last_name FROM core_teacherprofile AS teacher "
        "LEFT JOIN auth_user AS user ON user.id = teacher.user_id"
    ),
}


def run_sqlite_reconciliation(
    *,
    edugrade_database: str | Path,
    discipline_database: str | Path,
    edugrade_school_key: Any = None,
    discipline_school_key: Any = None,
) -> dict[str, Any]:
    """Extract both sources read-only and return an evidence-only report.

    This function performs no writes and never approves a match. The source
    database must be a valid, non-empty SQLite export.
    """
    edugrade_manifest = snapshot_manifest(edugrade_database)
    discipline_manifest = snapshot_manifest(discipline_database)
    source_students = read_records(edugrade_database, STUDENT_QUERIES["edugrade"])
    target_students = read_records(discipline_database, STUDENT_QUERIES["discipline"])
    source_teachers = read_records(edugrade_database, TEACHER_QUERIES["edugrade"])
    target_teachers = read_records(discipline_database, TEACHER_QUERIES["discipline"])
    assert_snapshot_unchanged(edugrade_database, edugrade_manifest)
    assert_snapshot_unchanged(discipline_database, discipline_manifest)

    normalized_source_students = adapt_students(
        source_students, source="edugrade", school_key=edugrade_school_key
    )
    normalized_target_students = adapt_students(
        target_students, source="discipline", school_key=discipline_school_key
    )
    normalized_source_teachers = adapt_teachers(
        source_teachers, source="edugrade", school_key=edugrade_school_key
    )
    normalized_target_teachers = adapt_teachers(
        target_teachers, source="discipline", school_key=discipline_school_key
    )

    reports = reconcile_bundle(
        source_students=[item.as_mapping() for item in normalized_source_students],
        target_students=[item.as_mapping() for item in normalized_target_students],
        source_teachers=[item.as_mapping() for item in normalized_source_teachers],
        target_teachers=[item.as_mapping() for item in normalized_target_teachers],
    )
    return {
        "reports": {entity: report.as_dict() for entity, report in reports.items()},
        "snapshots": {
            "edugrade": edugrade_manifest,
            "discipline": discipline_manifest,
        },
        "quality": {
            "edugrade_students": quality_summary(normalized_source_students),
            "discipline_students": quality_summary(normalized_target_students),
            "edugrade_teachers": quality_summary(normalized_source_teachers),
            "discipline_teachers": quality_summary(normalized_target_teachers),
        },
        "write_performed": False,
        "approval_performed": False,
    }