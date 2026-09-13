# AI Intelligence and Safety Architecture

Status: Phase 7 foundation design. This document defines the safe boundary for
future AI work; it does not change providers, prompts, permissions, or stored
student data.

## Current implementation surfaces

The host currently contains:

- `core/ai_providers.py` for environment-driven provider/model routing;
- `core/ai_chat.py` for chat and controlled data operations;
- `core/agent_pipeline.py` for intent, planning, risk, execution, verification,
  and audit flow;
- `core/admin_agent.py` and `core/admin_agent_queries.py` for admin-oriented
  context and tools;
- AI dashboards and chat templates under `templates/`.

These surfaces are useful foundations, but production AI integration remains
dependent on the unified identity, school scope, and Student360 data contract.

## Required AI data flow

```text
authenticated user
        |
        v
server-side authorization
        |
        v
authorized scope selector
        |
        v
Student360 / Teacher / School context
        |
        v
deterministic calculations and evidence
        |
        v
AI provider abstraction
        |
        v
response validation and safety checks
        |
        v
audited response
```

The language model must never receive unrestricted ORM objects, arbitrary raw
database access, or an unfiltered school-wide dataset.

## Context contract

Every AI request should carry a structured context containing:

- requesting user ID and role;
- authorized school and object scope;
- context version or snapshot identifier;
- identity references, not unnecessary private fields;
- deterministic academic metrics;
- discipline indicators the user is permitted to see;
- source/evidence references;
- missing-data flags;
- requested operation and output format.

The context builder must reject unauthorized scope before any provider call.
Missing information must remain missing; it must not be filled with model
guesswork.

## Deterministic analytics boundary

Numbers must be calculated by application code or database queries, then
explained by the model:

```text
marks/database -> Python calculation -> evidence -> AI explanation
```

The model must not calculate or invent official averages, rankings, risk scores,
student counts, or attendance totals as an authoritative result.

AI outputs may describe school-data indicators such as declining performance or
repeated incidents. They must not present ordinary school data as a medical,
psychological, criminal, or other high-stakes diagnosis.

## Provider abstraction

The existing provider routing should remain behind a stable interface with
operations equivalent to:

```text
generate(context, request)
analyze(context, request)
summarize(context, request)
structured_output(context, request, schema)
```

Provider selection, model name, latency, success/failure, and validation status
must be recorded without logging API keys, passwords, or unnecessary student
details.

## Explainability and provenance

Important recommendations must include:

- recommendation text;
- evidence items used;
- deterministic metric values where appropriate;
- context/snapshot identifier;
- provider/model;
- generation timestamp;
- validation status;
- uncertainty or missing-data statement.

Example safe wording:

```text
The available school data shows a decline in Mathematics performance across
three recorded assessments and repeated discipline incidents. This may warrant
appropriate human review.
```

The system must not claim facts absent from the authorized context.

## Admin-agent boundary

Read operations may be automatically executed only within the authenticated
administrator's authorized scope. Any mutation must remain behind explicit
approval, risk assessment, verification, and audit logging.

The agent must not use a prompt or model output as an authorization decision.

## Minimum AI test gate

Before production AI acceptance, test:

- unauthorized student/school context rejection;
- inactive or suspended account rejection;
- numerical correctness against database fixtures;
- missing-data refusal and uncertainty language;
- evidence inclusion in recommendations;
- provider failure and fallback behavior;
- prompt-injection resistance;
- sensitive-data leakage prevention;
- mutation approval and audit behavior;
- no medical, psychological, or criminal conclusions from ordinary school data.

## Deferred until identity integration

- final Student360 academic provider;
- school-scoped AI selectors;
- production AI provenance storage;
- unified teacher and student permissions;
- cross-domain risk calculation;
- final AI dashboards and reports.

This phase is preparation only. It does not bypass the Phase 3 snapshot gate,
Phase 4 ownership approval, or Phase 5 migration controls.