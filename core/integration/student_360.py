"""Shared student context used by academic, discipline, and AI features.

This module deliberately contains no EduGrade imports. The host project owns
the contract, while the academic provider can be attached after the canonical
model migration is approved.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from django.core.exceptions import PermissionDenied


class AcademicProvider(Protocol):
    """Read-only academic data provider for a canonical student."""

    def get_summary(self, student: Any) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class Student360Context:
    """One view-model for a student across all platform domains."""

    identity: Mapping[str, Any]
    academic: Mapping[str, Any] = field(default_factory=dict)
    discipline: Mapping[str, Any] = field(default_factory=dict)
    insights: Mapping[str, Any] = field(default_factory=dict)


class Student360Service:
    """Build a consistent cross-domain student context.

    The service accepts an academic provider instead of importing EduGrade.
    That keeps ownership explicit and prevents duplicate model registries while
    the database reconciliation is being prepared.
    """

    @classmethod
    def build(
        cls,
        student: Any,
        *,
        academic_provider: AcademicProvider | None = None,
        access_checker: Any | None = None,
    ) -> Student360Context:
        if access_checker is not None and not access_checker(student):
            raise PermissionDenied("Student360 access denied")
        discipline = cls._discipline_summary(student)
        academic = (
            dict(academic_provider.get_summary(student))
            if academic_provider is not None
            else {}
        )
        insights = cls._insights(academic, discipline)

        return Student360Context(
            identity=cls._identity(student),
            academic=academic,
            discipline=discipline,
            insights=insights,
        )

    @staticmethod
    def _identity(student: Any) -> Mapping[str, Any]:
        return {
            "id": student.pk,
            "admission_number": student.admission_number,
            "name": student.name,
            "stream_id": student.stream_id,
            "grade_level_id": student.grade_level_id,
            "form": student.form,
            "academic_status": student.academic_status,
        }

    @staticmethod
    def _discipline_summary(student: Any) -> Mapping[str, Any]:
        reports = student.reports.all()
        return {
            "incident_count": reports.count(),
            "risk_score": student.risk_score,
            "risk_level": student.risk_level,
            "risk_trend": student.risk_trend,
            "intervention_count": student.intervention_count,
            "last_incident_date": student.last_incident_date,
        }

    @staticmethod
    def _insights(
        academic: Mapping[str, Any],
        discipline: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Return explainable signals without changing persisted risk data."""
        signals = []
        if discipline.get("risk_level") == "CRITICAL":
            signals.append("critical_discipline_risk")
        if academic.get("trend") == "declining":
            signals.append("declining_academic_trend")
        return {"signals": signals}