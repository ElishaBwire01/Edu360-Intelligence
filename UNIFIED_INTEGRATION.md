# Unified EduGrade + Discipline Connection

This is the non-destructive connection plan for making `disciplinev12` the
single Django host. It does not merge or delete either database yet.

All real-data reconciliation must run against immutable database snapshots,
not live application files. The runner records each snapshot's SHA-256,
size, modification time, and SQLite integrity result, then verifies the hash
again after extraction. If either source changes during the run, the report is
rejected.

## The bundle connection

`core.integration.student_360.Student360Service` is the first shared boundary.
It gives every future dashboard, report, and AI workflow one context:

```text
canonical Student
    |
    +-- identity
    +-- academic provider
    +-- discipline summary
    +-- explainable insights
```

Academic code must provide read-only data through `AcademicProvider` rather
than importing a second `Student` model into Discipline views. After data
reconciliation, the provider becomes an adapter over the canonical academic
models and can be removed once those models are moved into the host project.

## Current conflict matrix

| Entity | Discipline owner | EduGrade owner | Decision | Risk |
|---|---|---|---|---|
| User | Django `auth.User` | Django `auth.User` | Keep one host auth table | Low |
| Student | `core.Student` with discipline risk fields | `students.Student` with richer academic identity | Canonicalize to one model after record matching; keep discipline fields in a profile or extend the chosen model | Critical |
| Teacher | `core.TeacherProfile` | `teachers.TeacherProfile` | Prefer EduGrade assignment model, migrate Discipline approval/online fields into it | Critical |
| School | `core.School` | `school.SchoolInfo` | Choose one school settings owner and migrate the other as history/config data | High |
| Grade | `core.GradeLevel` | `school.GradeLevel` | EduGrade model is richer; map Discipline grade references | High |
| Stream | `core.Stream` | `school.Stream` | EduGrade model is richer; preserve Discipline class references through mapping | Critical |
| Subject | No canonical Discipline subject | `school.Subject` | EduGrade owns it | Medium |
| Academic year/term | `core.AcademicTerm` | `school.AcademicYear` and `school.Term` | EduGrade owns the academic calendar | High |
| Notifications | `core.Notification` | `core.Notification` in EduGrade | Consolidate after host app rename; do not keep both tables as a permanent feature | Medium |
| Permissions | Django groups plus Discipline role requests | EduGrade role/permission tables | One permission service; preserve existing approval records during migration | High |
| Reports | `core.DisciplineReport` | `reports.GeneratedReport` | Keep separate domain records, unify rendering and student context | Medium |

## Why direct app copying is unsafe

Both projects contain top-level packages named `core`, `students`, `teachers`,
and `school`. Their models also use overlapping table names and reverse
relations. Adding EduGrade to `INSTALLED_APPS` without renaming and migrating
would silently bind imports to the wrong app or fail during model loading.

The host must therefore be assembled in these stages:

1. Keep Discipline as the only settings/WSGI/ASGI host.
2. Introduce the shared service contract and test it.
3. Copy EduGrade domain apps under explicit host-owned names, for example
   `academics_students`, `academics_school`, and `academics_marks`, while
   rewriting their internal imports as one controlled operation.
4. Build identity mapping tables before changing foreign keys.
5. Migrate matched students, teachers, streams, schools, and academic data.
6. Switch the provider from the old academic records to the canonical records.
7. Consolidate templates under Discipline's single `templates/base.html`.
8. Retire duplicate models only after foreign-key and integration tests pass.

## Ownership after consolidation

```text
core / shared identity: User, Student, Teacher, School, Stream, Subject
academics: examinations, marks, grading, academic reports
discipline: incidents, interventions, behavior, discipline risk history
ai: consumes authorized Student360Context; owns no student identity
reports: renders shared contexts; owns report artifacts and publication state
```

## Required migration maps

Before schema changes, generate and review:

```text
EduGrade admission_number -> Discipline admission_number -> canonical student
EduGrade staff/TSC/email  -> Discipline user/profile    -> canonical teacher
EduGrade stream code      -> Discipline stream          -> canonical stream
EduGrade school/academic  -> Discipline school/term      -> canonical school/calendar
```

Matching priority is stable identifiers first, then school plus identifier,
then name plus date of birth only for manual review. Ambiguous records must
never be merged automatically.

Each result is auditable. A match includes a numeric confidence score and
field-level evidence, for example:

```json
{
    "source_id": "EDU-1042",
    "target_id": "DISC-782",
    "method": "school_admission_number",
    "confidence": 1.0,
    "evidence": {
        "school_match": true,
        "admission_number_match": true,
        "name_match": false,
        "date_of_birth_match": false
    }
}
```

The `1.0` score means the stable admission key matched in a school scope; it
does not mean every descriptive field is identical. A school/name/date-of-birth
match is intentionally scored lower at `0.72` and remains reviewable by policy.
Teacher scores are `1.0` for staff number, `0.98` for TSC number, `0.9` for
email, and `0.75` for username. These are policy scores, not calibrated
probabilities. Reports label them as `confidence_kind: policy_score`.

## Quality versus identity results

The report keeps source-data quality separate from identity decisions:

```text
DATA QUALITY
missing_date_of_birth
missing_admission_number
missing_school_scope

IDENTITY RECONCILIATION
matched
unmatched
ambiguous_identity
duplicate_target
```

Missing DOB is a data-quality issue; it does not automatically mean that two
records conflict. Identity conflicts require duplicate or ambiguous candidates
and remain manual-review outcomes.

## Read-only real-data execution

`read_records()` in `core/integration/reconciliation/sqlite_source.py` opens a
SQLite export using `mode=ro` and exposes no write operation. The caller must
provide schema-specific `SELECT` statements that normalize both projects into
the same mapping fields, then pass the results to `reconcile_bundle()`.

The neutral boundary is `NormalizedStudentRecord` and
`NormalizedTeacherRecord` in `core/integration/reconciliation/records.py`.
Use `adapt_edugrade_students()` and `adapt_discipline_students()` (and their
teacher equivalents) to produce those records. The reconciliation engine only
consumes `record.as_mapping()` and therefore has no knowledge of either source
schema.

```python
from core.integration.reconciliation import read_records, reconcile_bundle

academic_students = read_records(
        edugrade_db,
        "SELECT id, admission_number, first_name || ' ' || last_name AS name, "
        "current_stream_id AS stream_id, date_of_birth FROM students",
)
discipline_students = read_records(
        discipline_db,
        "SELECT id, admission_number, name, stream_id FROM core_student",
)

    academic_records = adapt_edugrade_students(
        academic_students,
        school_key="SCHOOL-001",
    )
    discipline_records = adapt_discipline_students(
        discipline_students,
    )
```

    For a complete read-only run, use the host management command after obtaining
    a valid, non-empty EduGrade export:

    ```text
    python manage.py reconcile_sources \
        --edugrade-db path/to/edugrade.sqlite3 \
        --discipline-db db.sqlite3 \
        --edugrade-school-key SCHOOL-001 \
        --discipline-school-key SCHOOL-001 \
        --output reconciliation-report.json
    ```

    The command writes only the report file. It does not open either source in
    write mode, approve matches, create crosswalks, or modify Django data.

    The current local EduGrade export is zero bytes, so it is intentionally
    rejected with an explicit error until a valid database backup/export is
    provided. No real two-source counts should be claimed from that file.

This is deliberately an adapter step, not a migration step. The current local
schemas do not provide equivalent school scope or date-of-birth fields for all
student records. Those missing fields must be reported and resolved before
school-scoped admission matching is enabled. The matcher will not guess across
schools or treat names as identity.

## Reconciliation layer

The dry-run package lives under
`core/integration/reconciliation/`. It accepts plain record mappings, so it
can later be fed by Django querysets, CSV exports, or a read-only EduGrade
database adapter without coupling the host models to EduGrade imports.

```python
from core.integration.reconciliation import reconcile_students

report = reconcile_students(
    source_records=edugrade_students,
    target_records=discipline_students,
)

print(report.as_dict())
```

Student matching rules:

1. School plus admission number: high-confidence match.
2. School plus name plus date of birth: medium-confidence match.
3. Name alone: never a match; it becomes an unresolved record.
4. Multiple target candidates: manual-review conflict.

Teacher matching uses staff number or TSC number first, then email, then
username. Duplicate identifiers are conflicts, never automatic merges.

`unmatched_count` means no stable identity was found. `conflict_count` means a
record needs a human decision because the source is ambiguous or the target
contains duplicates. Neither result performs a create, update, delete, or
migration.

## Canonical model decision gate

The reconciliation report is the input to canonical model selection. Do not
hard-code EduGrade or Discipline as canonical before the report is reviewed.
The winner must preserve the most complete identity and relationship history,
and the migration must provide an explicit mapping for every losing record.

Only after student, teacher, school, and stream reports are reviewed should a
schema migration be designed. The current `Student360Service` remains the
stable consumer-facing contract while that decision is pending.

## Definition of a successful bundle

The integration is complete only when a single authenticated user can open one
student profile and see academic performance, discipline history, reports, and
authorized AI insights from one database and one canonical student record.