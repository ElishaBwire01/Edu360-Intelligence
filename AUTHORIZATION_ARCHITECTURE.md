# Identity and Authorization Architecture

Status: Phase 8 security design baseline, prepared during the earlier identity review. No login, session, permission, or
user data behavior was changed in this phase.

## Current host contract

The host currently uses:

- Django's default `auth.User` model;
- Django session authentication;
- custom login/logout/registration views in `core/views.py`;
- password-reset request and approval workflows;
- `core.TeacherProfile` linked one-to-one to `User`;
- Django groups named `Teacher` and `ClassTeacher`;
- `is_superuser` and `is_staff` checks in views, templates, and AI surfaces;
- rate limiting around selected authentication operations;
- `UserSession` and online-status tracking.

EduGrade also uses Django's default `auth.User`, but its authentication and
permission code lives in a separate project/database and cannot remain a
permanent second identity system.

## Target identity architecture

```text
one auth.User
    |
    +-- one TeacherProfile where applicable
    |      +-- school membership
    |      +-- subject assignments
    |      +-- stream assignments
    |      +-- responsibilities
    |      +-- explicit permissions
    |
    +-- administrator/counselor/discipline roles
    +-- student identity only where a student portal is approved
```

Responsibilities are not duplicate users or profiles. A teacher who is also a
class teacher, discipline officer, examiner, or school manager receives those
responsibilities through assignments and permissions.

## Authorization layers

Every protected operation must pass all applicable layers server-side:

1. **Authentication**: the account is logged in and the session is valid.
2. **Account state**: the account is active, not suspended, and not required
   to complete a blocked security action.
3. **Global permission**: the user has the required Django permission or
   explicit platform role.
4. **School scope**: the object belongs to a school the user is authorized to
   access.
5. **Assignment scope**: teacher access is limited to assigned streams,
   subjects, classes, or approved responsibilities.
6. **Object scope**: the requested student, mark, incident, report, or file is
   individually permitted.
7. **Sensitive-data scope**: discipline, AI, audit, and account data require
   their own explicit permission; frontend visibility is never sufficient.

`is_staff` and `is_superuser` may remain emergency administrative signals, but
they must not be the only authorization rule for ordinary academic or
discipline data.

## Permission vocabulary

The unified permission vocabulary should be explicit and domain-oriented:

```text
students.view_student
students.change_student
teachers.view_teacher
teachers.manage_assignment
school.manage_structure
marks.enter_marks
marks.edit_marks
marks.approve_marks
examinations.manage_examination
grading.process_results
reports.view_report
discipline.view_incident
discipline.create_incident
discipline.manage_intervention
ai.view_insight
ai.request_analysis
audit.view_log
```

The exact permission implementation may use Django permissions, role
assignments, or a service combining both, but every permission must resolve to
an auditable server-side decision.

## Object-level access contract

The future policy service should expose decisions equivalent to:

```text
can_view_student(user, student)
can_edit_student(user, student)
can_enter_marks(user, stream, subject, term)
can_view_incident(user, incident)
can_manage_intervention(user, intervention)
can_generate_report(user, scope)
can_request_ai_context(user, scope)
```

Teacher access should require same-school scope plus an assigned stream,
assigned subject, class-teacher responsibility, or explicit approved access.
Student access, if enabled, must be limited to the student's own records.
Administrators receive broader access only through explicit administrative
permissions and school scope.

## Authentication security requirements

- Keep one login/session system in the host project.
- Preserve Django password hashing and validators.
- Keep login rate limiting and make its proxy/IP trust configuration explicit.
- Invalidate sessions when an account is suspended, deactivated, or has a
  security-critical role change.
- Never store plaintext passwords or reset tokens in ordinary model fields.
- Require secure production cookies and HTTPS settings.
- Keep CSRF protection enabled for every state-changing request.
- Use generic authentication failure messages to reduce account enumeration.
- Audit login, logout, failed login, password reset, approval, suspension, and
  role changes without recording passwords or tokens.

## Current gaps recorded for later implementation

1. Host authorization is distributed across decorators, inline group checks,
   `is_staff`, and `is_superuser` checks rather than one policy service.
2. Host `Student` has no direct school foreign key, so school-isolated object
   authorization cannot be considered complete yet.
3. EduGrade has a separate role/permission model that has not been reconciled.
4. AI/admin-agent surfaces need a dedicated authorization review to guarantee
   context filtering before inference or tool execution.
5. Some sensitive access checks are view-local and require systematic security
   tests before production acceptance.
6. `OnlineStatusMiddleware` performs a profile write on every authenticated
   request; this needs performance and transaction review before scale-up.

These are findings, not silent fixes. The current phase does not alter them.

## Required security test matrix

Before Phase 3 can be declared production-ready, tests must cover:

| Scenario | Expected result |
|---|---|
| Anonymous user opens protected view | Login redirect or 401/403 |
| Inactive user uses an existing session | Access denied and session invalidated |
| Suspended teacher opens a protected view | Access denied |
| Teacher opens another teacher's restricted data | 403 or filtered result |
| Teacher opens an unassigned student's records | 403 or filtered result |
| Student opens another student's records | 403 or 404 |
| User crosses school boundary | 403 or filtered result |
| AI requests unauthorized context | Rejected before provider call |
| Admin-agent mutation without approval | Blocked and audited |
| Password/reset values appear in logs | Must never occur |

## Phase 8 approval gate

This security phase is not production-complete until:

- the EduGrade identity snapshot is reconciled;
- one authentication authority is selected;
- school scope exists for every protected shared entity;
- a server-side policy service is implemented and adopted by sensitive views;
- cross-school and object-level tests pass;
- session invalidation and audit behavior are verified;
- production security settings pass `check --deploy`.

Until then, no `AUTH_USER_MODEL` change, user merge, role migration, or
permission-table deletion is authorized.