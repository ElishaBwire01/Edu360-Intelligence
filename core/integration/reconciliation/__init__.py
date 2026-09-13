"""Dry-run identity reconciliation for the unified platform."""

from .reports import ReconciliationReport
from .runner import run_sqlite_reconciliation
from .adapters import (
	adapt_discipline_students,
	adapt_discipline_teachers,
	adapt_edugrade_students,
	adapt_edugrade_teachers,
	adapt_students,
	adapt_teachers,
)
from .bundle import reconcile_bundle
from .records import (
	CanonicalStudentRecord,
	CanonicalTeacherRecord,
	NormalizedStudentRecord,
	NormalizedTeacherRecord,
	quality_summary,
)
from .sqlite_source import assert_snapshot_unchanged, read_records, snapshot_manifest
from .student_mapper import reconcile_students
from .teacher_mapper import reconcile_teachers

__all__ = [
	"ReconciliationReport",
	"run_sqlite_reconciliation",
	"CanonicalStudentRecord",
	"CanonicalTeacherRecord",
	"NormalizedStudentRecord",
	"NormalizedTeacherRecord",
	"adapt_students",
	"adapt_teachers",
	"adapt_edugrade_students",
	"adapt_edugrade_teachers",
	"adapt_discipline_students",
	"adapt_discipline_teachers",
	"quality_summary",
	"read_records",
	"snapshot_manifest",
	"assert_snapshot_unchanged",
	"reconcile_bundle",
	"reconcile_students",
	"reconcile_teachers",
]