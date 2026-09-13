from django.test import SimpleTestCase
from tempfile import TemporaryDirectory

from .student_mapper import reconcile_students
from .teacher_mapper import reconcile_teachers
from .sqlite_source import read_records, snapshot_manifest
from .adapters import adapt_discipline_students, adapt_students, adapt_teachers
from .records import quality_summary
from .runner import run_sqlite_reconciliation


class ReconciliationTests(SimpleTestCase):
    def test_edugrade_style_student_becomes_neutral_record(self):
        records = adapt_students(
            [{
                "id": 12,
                "admission_number": "EDU-12",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "date_of_birth": "2010-01-02",
                "current_stream_id": 4,
            }],
            source="edugrade",
            school_key="SCHOOL-1",
        )

        record = records[0]
        self.assertEqual(record.full_name, "Ada Lovelace")
        self.assertEqual(record.school_key, "school-1")
        self.assertEqual(record.quality_flags, ())
        self.assertEqual(record.as_mapping()["admission_number"], "edu-12")
        self.assertEqual(record.raw["id"], 12)

    def test_discipline_student_missing_scope_is_reported(self):
        records = adapt_discipline_students(
            [{"id": 3, "admission_number": "D-3", "name": "Student Three"}],
        )

        self.assertEqual(records[0].quality_flags, ("missing_school_scope", "missing_date_of_birth"))
        self.assertEqual(quality_summary(records)["missing_school_scope"], 1)

    def test_teacher_adapter_preserves_identifier_quality(self):
        records = adapt_teachers(
            [{"id": 8, "staff_number": "T-8", "email": "teacher@example.test"}],
            source="edugrade",
            school_key="SCHOOL-1",
        )

        self.assertEqual(records[0].staff_number, "t-8")
        self.assertEqual(records[0].quality_flags, ("missing_name",))

    def test_neutral_records_feed_existing_matcher(self):
        source = adapt_students(
            [{"id": "edu-1", "admission_number": "A-1", "name": "Ada"}],
            source="edugrade",
            school_key="SCHOOL-1",
        )
        target = adapt_students(
            [{"id": "disc-1", "admission_number": "A-1", "name": "Ada"}],
            source="discipline",
            school_key="SCHOOL-1",
        )

        report = reconcile_students(
            [record.as_mapping() for record in source],
            [record.as_mapping() for record in target],
        )

        self.assertEqual(report.matches[0].target_id, "disc-1")

    def test_student_matches_by_school_and_admission_number(self):
        report = reconcile_students(
            [{"id": "academic-1", "school": "North", "admission_number": "A-1", "name": "Ada"}],
            [{"id": 7, "school": "north", "admission_number": "a-1", "name": "Ada Lovelace"}],
        )

        self.assertEqual(report.matches[0].target_id, 7)
        self.assertEqual(report.matches[0].confidence, 1.0)
        self.assertTrue(report.matches[0].evidence["admission_number_match"])
        self.assertEqual(report.matches[0].differences, ("name",))

    def test_report_serializes_auditable_evidence(self):
        report = reconcile_students(
            [{"id": "academic-1", "school": "North", "admission_number": "A-1"}],
            [{"id": 7, "school": "North", "admission_number": "A-1"}],
        )

        serialized = report.as_dict()
        self.assertEqual(serialized["matches"][0]["confidence"], 1.0)
        self.assertTrue(serialized["matches"][0]["evidence"]["school_match"])
        self.assertEqual(serialized["matches"][0]["confidence_kind"], "policy_score")

    def test_sqlite_source_reads_without_write_access(self):
        import sqlite3

        with TemporaryDirectory() as directory:
            database = f"{directory}/source.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("create table records (id integer, name text)")
            connection.execute("insert into records values (1, 'Ada')")
            connection.commit()
            connection.close()

            rows = read_records(database, "select id, name from records")

        self.assertEqual(rows, [{"id": 1, "name": "Ada"}])

    def test_snapshot_manifest_contains_hash_and_integrity(self):
        import sqlite3

        with TemporaryDirectory() as directory:
            database = f"{directory}/source.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("create table records (id integer)")
            connection.commit()
            connection.close()

            manifest = snapshot_manifest(database)

        self.assertEqual(len(manifest["sha256"]), 64)
        self.assertEqual(manifest["integrity_check"], "ok")

    def test_empty_sqlite_export_is_rejected(self):
        with TemporaryDirectory() as directory:
            database = f"{directory}/empty.sqlite3"
            open(database, "wb").close()

            with self.assertRaisesRegex(ValueError, "empty"):
                read_records(database, "select 1")

    def test_sqlite_runner_returns_report_without_approval(self):
        import sqlite3

        with TemporaryDirectory() as directory:
            edugrade_database = f"{directory}/edugrade.sqlite3"
            discipline_database = f"{directory}/discipline.sqlite3"
            self._create_source_database(
                edugrade_database,
                """
                create table students (
                    id integer, admission_number text, first_name text,
                    middle_name text, last_name text, date_of_birth text,
                    current_stream_id integer
                );
                create table teacher_profiles (
                    id integer, staff_number text, tsc_number text,
                    first_name text, last_name text, user_id integer
                );
                create table auth_user (
                    id integer, email text, username text,
                    first_name text, last_name text
                );
                insert into students values (1, 'A-1', 'Ada', null, 'Lovelace', '2010-01-02', 4);
                insert into teacher_profiles values (2, 'T-2', null, 'Grace', 'Hopper', 3);
                insert into auth_user values (3, 'grace@example.test', 'grace', 'Grace', 'Hopper');
                """,
            )
            self._create_source_database(
                discipline_database,
                """
                create table core_student (
                    id integer, admission_number text, name text, stream_id integer
                );
                create table core_teacherprofile (id integer, user_id integer);
                create table auth_user (
                    id integer, email text, username text,
                    first_name text, last_name text
                );
                insert into core_student values (10, 'A-1', 'Ada Lovelace', 4);
                insert into core_teacherprofile values (20, 30);
                insert into auth_user values (30, 'grace@example.test', 'grace', 'Grace', 'Hopper');
                """,
            )

            result = run_sqlite_reconciliation(
                edugrade_database=edugrade_database,
                discipline_database=discipline_database,
                edugrade_school_key="SCHOOL-1",
                discipline_school_key="SCHOOL-1",
            )

        self.assertEqual(result["reports"]["students"]["matched_count"], 1)
        self.assertEqual(result["reports"]["teachers"]["matched_count"], 1)
        self.assertFalse(result["write_performed"])
        self.assertFalse(result["approval_performed"])
        self.assertEqual(result["snapshots"]["edugrade"]["integrity_check"], "ok")

    @staticmethod
    def _create_source_database(database, script):
        import sqlite3

        connection = sqlite3.connect(database)
        connection.executescript(script)
        connection.commit()
        connection.close()

    def test_student_name_without_date_of_birth_is_not_a_match(self):
        report = reconcile_students(
            [{"id": "academic-1", "school": "North", "name": "Ada"}],
            [{"id": 7, "school": "North", "name": "Ada"}],
        )

        self.assertEqual(report.matches, [])
        self.assertEqual(report.unmatched_count, 1)
        self.assertEqual(report.review_conflict_count, 0)

    def test_duplicate_student_target_requires_review(self):
        report = reconcile_students(
            [{"id": "academic-1", "school": "North", "admission_number": "A-1"}],
            [
                {"id": 7, "school": "North", "admission_number": "A-1"},
                {"id": 8, "school": "North", "admission_number": "A-1"},
            ],
        )

        self.assertEqual(report.review_conflict_count, 1)
        self.assertEqual(report.conflicts[0].kind, "duplicate_target")

    def test_teacher_prefers_staff_number_then_email(self):
        report = reconcile_teachers(
            [
                {"id": "t-1", "staff_number": "ST-1", "email": "old@example.test"},
                {"id": "t-2", "email": "teacher@example.test"},
            ],
            [
                {"id": 11, "staff_number": "st-1", "email": "new@example.test"},
                {"id": 12, "email": "TEACHER@example.test"},
            ],
        )

        self.assertEqual([match.target_id for match in report.matches], [11, 12])
        self.assertEqual([match.method for match in report.matches], ["staff_number", "email"])