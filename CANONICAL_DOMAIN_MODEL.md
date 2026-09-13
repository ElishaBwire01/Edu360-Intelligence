# Canonical Domain Model

Status: Phase 2 design and ownership gate in the ten-phase roadmap. This document defines the target
shared domain, but it does not select production owners, alter models, create
migrations, or move records. Those decisions require a valid EduGrade snapshot
and an approved reconciliation report.

## Design rule

The final platform has one identity architecture:

```text
User
 |
 +-- TeacherProfile
       |
       +-- assignments
       +-- responsibilities
       +-- permissions

School
 |
 +-- academic years and terms
 +-- grade levels
 +-- streams/classes
 +-- subjects
 +-- students
 +-- teachers
```

There must be one authoritative production record for each student, teacher,
school, stream, and subject. `NormalizedStudentRecord` and
`NormalizedTeacherRecord` are temporary reconciliation records only; they are
not canonical database models.

## Shared identity entities

### User

Owned by the host authentication system after consolidation.

Responsibilities:

- authentication and sessions
- account activation/deactivation
- password reset
- groups and permissions
- audit actor identity

The existing projects both use Django's default `auth.User`. No custom user
model should be introduced during this phase without a separate migration plan.

### Student

One student identity must provide:

- stable admission number
- name and identity fields
- school relationship
- current enrollment
- stream/class relationship
- academic status
- historical enrollment relationships

Academic data and discipline data must reference the same production student.
Discipline risk fields should remain discipline-owned profile data unless the
ownership review proves that extending the shared student is safer.

### TeacherProfile

One teacher identity must provide:

- one linked `User`
- staff/TSC identifiers where available
- school relationship
- approval and active status
- subject assignments
- stream/class assignments
- class-teacher responsibilities
- discipline responsibilities
- object-level permissions

Different responsibilities must be assignments or permissions, never duplicate
teacher accounts or profiles.

### School

One school identity must scope all applicable records:

- students
- teachers
- streams/classes
- grade levels
- academic calendars
- discipline records
- reporting and authorization

The current Discipline student schema does not carry a direct school foreign
key, so school scope must be established before cross-system identity matching
or a school-isolated migration can be approved.

### Academic structure

The shared academic structure is:

```text
School
  +-- AcademicYear
        +-- Term
  +-- Curriculum
        +-- GradeLevel
              +-- Stream
  +-- Subject
```

The exact production ownership between `core.School`/`core.GradeLevel`/
`core.Stream` and EduGrade's `school` models remains undecided until real data
and relationship completeness are measured.

## Specialized domain ownership

These are target ownership boundaries, not yet production ownership decisions.

### Academics

Academic modules own:

- examinations
- examination enrollment/configuration
- marks and submissions
- corrections and approvals
- grading rules and calculated performance
- academic report artifacts

Academic records reference shared students, teachers, subjects, streams, terms,
and examinations through real foreign keys after consolidation.

### Discipline

The discipline domain owns:

- discipline categories
- incidents/reports
- actions and outcomes
- interventions
- behavior history
- discipline-specific risk history

Discipline records reference the shared student and the shared authenticated
teacher/user who reported or managed the record.

### AI and analytics

AI owns no student, teacher, or school identity. It consumes an authorized,
versioned service context assembled from shared domain services.

```text
authorized user
  -> permission check
  -> Student360 context
  -> deterministic metrics
  -> AI explanation/recommendation
  -> provenance and audit record
```

AI indicators are not diagnoses or automatic decisions.

### Reporting

Reporting renders academic, discipline, and Student360 projections. It must
record report type, requesting user, scope, generation time, and relevant data
snapshot/version where applicable.

## Relationship contracts

```text
User 1--1 TeacherProfile
School 1--* Student
School 1--* TeacherProfile
School 1--* Stream
Stream 1--* Student (current enrollment)
TeacherProfile *--* Subject (assignment)
TeacherProfile *--* Stream (assignment)
Student 1--* StudentEnrollment (history)
Student 1--* MarkEntry
Student 1--* DisciplineIncident
Student 1--* Intervention
Student 1--* AcademicReport
```

Historical enrollment must not be overwritten when a student changes streams.
Transfers should be transactional and preserve historical marks, reports, and
discipline records.

## Authorization contract

Every domain query must be scoped by authorization, not template visibility.
A teacher may access a student only through a valid combination of:

- same school
- assigned stream/class
- assigned subject relationship
- class-teacher responsibility
- explicit approved responsibility

Administrators may have broader access according to explicit permissions.
Students may access only their own authorized profile and records.

AI receives only the already-authorized context.

## Candidate ownership matrix

| Entity | Candidate source | Evidence required before approval |
|---|---|---|
| User/authentication | Host Django auth | account overlap and session/role reconciliation |
| Student | `core.Student` or EduGrade `students.Student` | counts, identifier completeness, foreign-key graph, historical coverage |
| TeacherProfile | `core.TeacherProfile` or EduGrade `teachers.TeacherProfile` | staff/TSC/email overlap, assignment coverage, approval history |
| School | `core.School` or EduGrade `SchoolInfo` | school count, settings completeness, dependent record coverage |
| GradeLevel | Host or EduGrade school structure | curriculum/grade relationship completeness |
| Stream | Host or EduGrade school structure | stable code/name mapping and student coverage |
| Subject | EduGrade subject registry or future shared model | subject coverage in marks, assignments, and reports |
| AcademicYear/Term | Host `AcademicTerm` or EduGrade calendar | historical academic records and active-term consistency |
| DisciplineIncident | Host `DisciplineReport` | host is the current only implemented discipline owner |
| Marks/Grading | EduGrade academic modules | academic data completeness and foreign-key integrity |

## Ownership approval gate

No entity becomes production-canonical until its decision record contains:

1. source counts from immutable snapshots;
2. matched, unmatched, duplicate, and manual-review counts;
3. field completeness and data-quality results;
4. dependent foreign-key and historical relationship coverage;
5. documented migration and rollback strategy;
6. validation queries and acceptance criteria;
7. explicit human approval.

The current state has not passed this gate because the local EduGrade export is
zero bytes. Therefore this phase defines the target architecture but makes no
ownership claim.

## Deferred until Phase 3 of the ten-phase roadmap+

- changing `INSTALLED_APPS`;
- renaming or copying EduGrade apps;
- changing `AUTH_USER_MODEL`;
- adding shared foreign keys;
- creating crosswalk tables;
- changing existing migrations;
- migrating or deleting records;
- replacing the current template shell;
- declaring the platform production-ready.