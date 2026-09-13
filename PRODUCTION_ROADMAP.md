# Unified Platform Production Roadmap

This roadmap compresses the larger production-readiness specification into ten
major phases. Completed infrastructure is preserved; unfinished phases remain
blocked where the required evidence or approval is unavailable.

## Status summary

| Phase | Major phase | Status |
|---:|---|---|
| 1 | Architecture freeze and inventory | Complete |
| 2 | Canonical domain-model design | Complete |
| 3 | Real-data reconciliation and evidence review | Blocked: valid EduGrade snapshot required |
| 4 | Canonical ownership and crosswalk approval | Blocked by Phase 3 |
| 5 | Unified model implementation and identity migration | Blocked by Phase 4 |
| 6 | Academic, Discipline, and Student360 integration | Partially prepared; implementation follows Phase 5 |
| 7 | AI intelligence and AI safety layer | Safety foundation documented; production integration follows Phase 6 |
| 8 | Security, authorization, audit, and data protection | Architecture documented; health and Student360 gates added; hardening remains |
| 9 | Production infrastructure, PostgreSQL, backups, monitoring, and deployment | Audit complete; remediation not started |
| 10 | Full testing, performance, disaster recovery, and production acceptance | Blocked by existing AI regression and remaining acceptance work |

## Phase 1: Architecture freeze and inventory

Completed in [ARCHITECTURE_INVENTORY.md](ARCHITECTURE_INVENTORY.md).

Covered applications, models, URLs, templates, authentication, permissions,
AI, background tasks, environment settings, migrations, deployment surfaces,
duplicates, and security findings.

## Phase 2: Canonical domain-model design

Completed in [CANONICAL_DOMAIN_MODEL.md](CANONICAL_DOMAIN_MODEL.md).

Defined shared identity entities, academic and discipline boundaries,
relationship contracts, authorization requirements, and ownership approval
gates without falsely declaring a production owner.

## Phase 3: Real-data reconciliation and evidence review

Current phase. The read-only reconciliation foundation is implemented:

- immutable snapshot metadata and hash stability checks;
- SQLite integrity validation;
- normalized source records and adapters;
- field-level evidence;
- policy-score semantics;
- data-quality versus identity-conflict reporting;
- read-only CLI and tests.

Remaining work requires a valid, non-empty EduGrade snapshot:

1. verify snapshot and schema;
2. extract both sources read-only;
3. normalize students, teachers, schools, streams, and relevant relationships;
4. generate machine-readable and human-readable reports;
5. review matched, unmatched, duplicate, ambiguous, and quality records.

No migration or approval occurs in this phase.

## Phase 4: Canonical ownership and crosswalk approval

Use the reviewed evidence to approve ownership per entity, not by assumption.
Create an approved deterministic crosswalk containing source system, source ID,
candidate canonical ID, evidence, policy score, reviewer, approval state, and
migration state.

No crosswalk or ownership decision is valid without human approval and a
rollback strategy.

## Phase 5: Unified model implementation and identity migration

Bring approved academic modules into the host project, unify foreign keys,
preserve historical records, and migrate identities transactionally.

Required controls:

- backup and immutable source snapshots;
- dry-run migration;
- idempotency and duplicate protection;
- orphan and foreign-key validation;
- rollback plan;
- post-migration source-versus-target report.

## Phase 6: Academic, Discipline, and Student360 integration

Make the system behave as one platform over the unified models:

```text
Student
  +-- academics, examinations, marks, grading, reports
  +-- discipline, interventions, behavior history
  +-- Student360 projection and authorized insights
```

Connect teacher assignments, class scope, academic performance, discipline
history, reports, dashboards, and unified templates through shared services.

## Phase 7: AI intelligence and AI safety layer

Build AI on authorized, verified Student360 contexts. Deterministic metrics
must be calculated outside the language model. AI outputs require evidence,
uncertainty language, provenance, validation, and audit records.

The AI must not diagnose, invent facts, bypass permissions, or access raw
databases without a filtered context.

The safety boundary is documented in
[AI_SAFETY_ARCHITECTURE.md](AI_SAFETY_ARCHITECTURE.md). Production AI remains
blocked on unified identity, authorization scope, and Student360 data.

## Phase 8: Security, authorization, audit, and data protection

Implement the server-side policy service, school/object-level authorization,
session invalidation, audit trails, secure file access, secret management,
CSRF/XSS protections, AI data isolation, and security test coverage.

The design baseline is [AUTHORIZATION_ARCHITECTURE.md](AUTHORIZATION_ARCHITECTURE.md).
The current independent foundations include `/health/`, a Student360 access
check hook, and `validate_production_config`; they do not replace the final
authorization and production-security work.

## Phase 9: Production infrastructure and deployment

Move the validated production system toward PostgreSQL and reproducible
deployment with appropriate connection management, backups, restore testing,
HTTPS, static/media storage, logging, monitoring, health checks, and workers
only where justified.

## Phase 10: Full testing and production acceptance

Run integration, migration, authorization, AI-safety, performance, backup/
restore, deployment, and failure-recovery tests. The final acceptance sequence
must include:

```text
python manage.py check --deploy
python manage.py test
python manage.py makemigrations --check
```

Do not declare production readiness while critical defects, unresolved
identity conflicts, unverified backups, or missing authorization tests remain.

The latest full-suite run found one existing AI truthfulness test failure:
`test_at_risk_question_returns_ranked_live_records` received mode
`unified_facts` instead of the expected `local_db`. The focused hardening tests
and Django checks pass, but the complete suite is not currently green.

## Current blocker and next action

The local EduGrade database export is zero bytes. Therefore Phase 3 cannot
produce real counts or evidence yet. The next action is to obtain and verify a
valid immutable EduGrade snapshot, then run `reconcile_sources` against that
snapshot and the matching Discipline snapshot.

The deployment audit is documented in
[DEPLOYMENT_READINESS.md](DEPLOYMENT_READINESS.md). It identified six
environment-sensitive Django warnings; development settings were intentionally
left unchanged.