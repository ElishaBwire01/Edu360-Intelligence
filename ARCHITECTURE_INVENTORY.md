# Architecture Inventory

Status: Phase 1 inventory completed. This document is read-only analysis; no
database, migration, authentication, or runtime behavior was changed while
producing it.

## 1. Project boundaries

| Area | Host: `disciplinev12` | Source: `-EduGrade` |
|---|---|---|
| Django settings | `disciplinary_program/settings.py` | `edugrade/settings.py` |
| Root URLs | `disciplinary_program/urls.py` | `edugrade/urls.py` |
| WSGI/ASGI | `disciplinary_program/wsgi.py`, `asgi.py` | `edugrade/wsgi.py`, `asgi.py` |
| Database strategy | Environment-driven SQLite/PostgreSQL via `dj_database_url` | Hard-coded SQLite path in settings |
| Primary app shell | `templates/base.html` | `-EduGrade/templates/base.html` |
| Main custom app | `core` | `core`, `school`, `students`, `teachers`, `examinations`, `marks`, `grading`, `reports`, `authentication`, `users` |
| Authentication | Host `core` views and Django `auth.User` | Django auth views and Django `auth.User` |

The host remains the only safe runtime owner. EduGrade is currently a separate
Django project, not an installed host module.

## 2. Host application inventory

Installed custom app: `core`.

Important host surfaces:

- Models: `core/models.py` contains `School`, `GradeLevel`, `AcademicTerm`,
  `Stream`, `TeacherProfile`, `Student`, `DisciplineCategory`,
  `DisciplineReport`, `Notification`, `RoleRequest`, `PasswordReset`, and
  session tracking.
- URLs: `core/urls.py` exposes login, dashboards, student management, user
  management, AI APIs, reports, notifications, and admin-agent endpoints.
- Views: `core/views.py`, `views_chat.py`, and `admin_agent.py`.
- Forms: `core/forms.py`.
- Middleware: error logging, online status, and request logging in
  `core/middleware.py`.
- Background tasks: `core/tasks.py` uses Celery for risk updates, email, and
  report work. The report task currently references `Stream` without an
  import, which is an inventory finding requiring a later focused fix.
- AI: `core/ai_chat.py`, `core/ai_providers.py`, `core/agent_pipeline.py`,
  `core/admin_agent.py`, and `core/admin_agent_queries.py`.
- Management commands: initialization, category seeding, cleanup, error
  reporting, and the read-only `reconcile_sources` command.
- Templates: one host `templates/base.html` plus discipline, dashboard, AI,
  profile, and admin templates.
- Migrations: host `core` migrations exist through the currently applied
  permission/notification changes.

## 3. EduGrade application inventory

Custom apps declared in `-EduGrade/edugrade/settings.py`:

- `authentication`
- `core`
- `users`
- `school`
- `students`
- `teachers`
- `examinations`
- `marks`
- `grading`
- `reports`

Important domain models:

- `school`: `AcademicYear`, `Term`, `Curriculum`, `GradeLevel`, `Stream`,
  `Subject`, `SchoolInfo`.
- `students`: academic `Student` and `StudentHistory`.
- `teachers`: academic `TeacherProfile`, `TeacherAssignment`,
  `TeacherRequest`, `ClassTeacher`, and class-teacher request models.
- `examinations`: examinations and examination-class/subject configuration.
- `marks`: mark entries, submissions, and corrections.
- `grading`: KCSE/CBC grading and performance models.
- `reports`: academic report templates, generated reports, and comments.
- `core`: EduGrade roles, permissions, audit logs, notifications, warnings,
  and suspensions.

EduGrade has its own URL tree for authentication, students, teachers,
examinations, marks, grading, reports, and school setup. Its templates inherit
from a second `base.html` and therefore cannot be copied into the host without
template-shell and URL namespace work.

## 4. Duplicate identity and ownership matrix

| Entity | Host owner | EduGrade owner | Current state | Decision status |
|---|---|---|---|---|
| User | Django `auth.User` | Django `auth.User` | Same conceptual auth table, separate databases | Not yet unified |
| Student | `core.Student` | `students.Student` | Different fields and relationships | Pending reconciliation |
| Teacher | `core.TeacherProfile` | `teachers.TeacherProfile` | Different profile/assignment structures | Pending reconciliation |
| School | `core.School` | `school.SchoolInfo` | Different school configuration models | Pending ownership review |
| Grade level | `core.GradeLevel` | `school.GradeLevel` | Different parent relationships | Pending ownership review |
| Stream | `core.Stream` | `school.Stream` | Different keys and academic relationships | Pending ownership review |
| Subject | No equivalent domain model | `school.Subject` | EduGrade-only academic entity | Pending host integration |
| Academic calendar | `core.AcademicTerm` | `AcademicYear` + `Term` | Different calendar models | Pending ownership review |
| Notifications | `core.Notification` | EduGrade `core.Notification` | Duplicate app label/model concept | Pending consolidation |
| Permissions | Django groups plus host role requests | EduGrade permission assignments | Different authorization surfaces | Pending authorization design |
| Reports | `core.DisciplineReport` | Academic generated reports | Different domain records | Keep domain separation, unify context |

The existing reconciliation layer is the approved boundary for this conflict.
No canonical production owner has been declared yet.

## 5. URL and template inventory

Host root URL configuration includes `core.urls` at `/` and Django admin at
`/admin/`. EduGrade root URLs include separate top-level routes for
`students/`, `teachers/`, `examinations/`, `marks/`, `grading/`, `reports/`,
`school/`, and `auth/`.

Both systems use generic names such as `dashboard`, `list`, `detail`, and
`create`. A host merge requires explicit namespaces before route inclusion.

Both systems have a `base.html`, separate navigation, separate static CSS,
and different login/profile links. The host shell is the target unified shell;
EduGrade templates are not yet connected.

## 6. Authentication and authorization inventory

- Both projects use Django's default `auth.User`; neither currently sets a
  custom `AUTH_USER_MODEL`.
- Host authentication includes custom login, logout, registration, role
  requests, approval/suspension, password-reset request workflows, and session
  tracking.
- EduGrade has `authentication` views plus role and permission models in its
  `core` app.
- Host templates and several AI surfaces use `is_staff`/`is_superuser` as
  broad gates. Assignment/object-level authorization is not yet a unified
  architecture.
- Both projects have one-login behavior only within their own project; no
  cross-project session can be retained as a permanent design.

## 7. AI, agents, and external services

Host AI/provider surfaces include OpenAI, OpenRouter, Groq, and Gemini routing
through environment variables. The admin agent can inspect database/schema
information and has controlled write pathways in its pipeline. AI context
authorization and provenance remain a later production-hardening phase.

The reconciliation layer is intentionally separate from the AI layer and does
not give AI access to source databases.

## 8. Environment and deployment inventory

Host settings read environment values for `SECRET_KEY`, `DEBUG`, allowed
hosts, CSRF origins, `DATABASE_URL`, email, Supabase values, Vercel values,
and AI provider keys. The host also contains `.env`, `.env.example`, Vercel
configuration, deployment audit files, and static/media directories.

EduGrade settings contain a development secret key, `DEBUG = True`, a
hard-coded local database path, and local-only allowed hosts. EduGrade's
settings must not be merged into production settings as-is.

The host settings currently include development fallbacks such as an insecure
development secret and permissive development host/CORS behavior. These are
inventory findings for the later security/production phase, not changes made in
this freeze.

## 9. Database and migration inventory

- Host local database: `db.sqlite3`, with `core_*` tables and applied Django
  migrations.
- EduGrade contains a discovered SQLite export path, but the current file is
  zero bytes and therefore not a usable source snapshot.
- Both projects have independent migration histories for overlapping app
  labels. Directly adding EduGrade apps to host `INSTALLED_APPS` would cause
  model/table and import conflicts.
- No crosswalk, data migration, canonical ownership migration, or destructive
  schema operation has been performed.

## 10. Security-sensitive findings

1. EduGrade has a hard-coded insecure secret and `DEBUG = True`.
2. Host development settings intentionally fall back to an insecure secret and
   permissive hosts/CORS; production must fail closed through environment
   configuration.
3. Repository scripts and text artifacts include references to credentials or
   generated credentials; they require secret scanning and cleanup policy
   before production release.
4. `init_setup.py` and `setup_admin.py` contain hard-coded bootstrap
   credentials in source-controlled scripts. These must not be used for
   production administration.
5. AI/admin-agent data access and mutation paths require a dedicated
   authorization and audit review before production deployment.
6. The current task inventory includes Unix shell scripts on a Windows
   workspace; deployment/build compatibility requires a later CI review.

## 11. Already completed

- Host-owned `Student360Service` boundary.
- Read-only reconciliation engine.
- `NormalizedStudentRecord` and `NormalizedTeacherRecord` adapters.
- Field-level evidence and policy-score semantics.
- Snapshot hashing, SQLite integrity checks, and pre/post stability checks.
- Separate data-quality and identity-result reporting.
- No source database writes, approvals, crosswalks, or migrations.

## 12. Phase 1 exit criteria

Phase 1 is complete and is tracked in the ten-phase roadmap. See
`PRODUCTION_ROADMAP.md` for the current phase status.