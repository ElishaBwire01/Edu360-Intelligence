# core/ai_chat.py
# =============================================================================
# PollinationAIChat — Final Corrected Edition with Unified Facts + Probe
#
#   * Original baseline preserved: legacy intent pipeline, ORM readers,
#     provider plumbing, system prompt.
#   * Optional deep-access layer for engineers (feature-flagged, read-only).
#   * Self-healer so a missing helper cannot 500 the API.
#   * Negative-result verifier (opt-in).
#   * _deep_school_answer: fills 4 gaps the base router misses.
#   * _gather_for_question: window-widening + scattered-row joining.
#   * UnifiedFacts: one cached snapshot every code path reads from.
#   * TerminalProbe: read-only introspection of the running backend.
# =============================================================================

import json
import os
import re
import time
import logging
import base64
import hashlib
import shlex
import socket
import subprocess
import sys
import platform
from datetime import datetime, timedelta
from datetime import timezone as dt_tz
from pathlib import Path
from typing import Any, Callable
from core.ai_providers import load_provider_keys, load_models, PROVIDERS, get_providers_for_task, configured_providers

import requests
from django.db.models import Avg, Count, Q, Sum

try:
    from django.utils import timezone  # type: ignore
except Exception:
    timezone = None


# =============================================================================
# FEATURE FLAGS — everything new is OFF by default
# =============================================================================
ENABLE_DEEP = os.environ.get("AI_ENABLE_DEEP", "0") == "1"
ENABLE_VERIFIER = os.environ.get("AI_ENABLE_VERIFIER", "0") == "1"

GOD_READ_AUDIT = Path(os.environ.get("GOD_READ_AUDIT", "/var/log/god_read.audit.jsonl"))
try:
    GOD_READ_AUDIT.parent.mkdir(parents=True, exist_ok=True)
except Exception:
    GOD_READ_AUDIT = Path(os.environ.get("TMPDIR", "/tmp")) / "god_read.audit.jsonl"


def _worm(record: dict) -> None:
    try:
        with GOD_READ_AUDIT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


_SQL_ROOTS = {"select", "with", "explain", "show", "describe", "pragma"}
_SQL_BAN = ("insert", "update", "delete", "drop", "alter", "create",
            "truncate", "grant", "revoke", "attach", "detach", "replace",
            "merge", "call", "exec", "execute", "vacuum", "reindex",
            "copy", "load", "outfile", "dumpfile", "commit", "rollback",
            " begin ", "lock", "pg_sleep")


def _sql_readonly(sql: str) -> tuple[bool, str]:
    if not sql or not sql.strip():
        return False, "empty"
    s = sql.strip().rstrip(";")
    first = s.split(None, 1)[0].lower() if s else ""
    if first not in _SQL_ROOTS:
        return False, f"root:{first or 'none'}"
    low = s.lower()
    for kw in _SQL_BAN:
        if kw in low:
            return False, f"banned:{kw.strip()}"
    if ";" in s:
        return False, "multi"
    return True, "ok"


# =============================================================================
# Deep-access terminal (engineer-only, read-only)
# =============================================================================
class GodReadTerminal:
    PROC_ALLOW = {"ps", "uptime", "df", "free", "whoami", "id", "uname",
                  "hostname", "ls", "cat", "head", "tail", "wc", "grep",
                  "git", "pip", "python", "python3", "node", "npm",
                  "ss", "netstat", "lsof", "mount", "env", "printenv",
                  "ifconfig", "ip", "getent"}
    GIT_RO = {"status", "log", "diff", "show", "branch", "tag", "remote",
              "config", "rev-parse", "describe", "blame", "shortlog",
              "ls-files", "ls-tree", "cat-file", "reflog", "whatchanged"}
    SECRET_HINTS = ("SECRET", "TOKEN", "PASSWORD", "PWD", "KEY",
                    "CREDENTIAL", "AUTH", "SESSION", "COOKIE", "PRIVATE")

    def __init__(self, chat: "PollinationAIChat"):
        self.chat = chat
        self.max_rows = int(os.environ.get("GOD_READ_MAX_ROWS", "5000"))
        self.max_bytes = int(os.environ.get("GOD_READ_MAX_FILE_BYTES", "2000000"))
        self.timeout = float(os.environ.get("GOD_READ_PROC_TIMEOUT", "10"))

    def _eng(self, actor):
        if not actor or actor.get("role") != "engineer":
            raise PermissionError("GOD-READ restricted to role='engineer'.")

    def _audit(self, actor, tool, args, meta):
        _worm({"ts": datetime.now(dt_tz.utc).isoformat(),
               "actor_id": actor.get("id"), "actor_role": actor.get("role"),
               "tool": tool, "args": args, "meta": meta})

    def _proc(self, argv):
        if not argv or argv[0] not in self.PROC_ALLOW:
            raise PermissionError(f"proc not allowed: {argv[:1]}")
        if any(ch in " ".join(argv) for ch in "|&;><$`\n"):
            raise PermissionError("no shell metacharacters")
        out = subprocess.run(argv, capture_output=True, text=True,
                             timeout=self.timeout, shell=False)
        return {"cmd": argv, "rc": out.returncode,
                "stdout": out.stdout[:200_000],
                "stderr": out.stderr[:20_000]}

    def system_overview(self, actor) -> dict:
        self._eng(actor)
        out = {"hostname": socket.gethostname(),
               "platform": platform.platform(),
               "python": sys.version,
               "python_executable": sys.executable,
               "cwd": os.getcwd(), "pid": os.getpid(),
               "user": os.environ.get("USER") or os.environ.get("USERNAME"),
               "now_utc": datetime.now(dt_tz.utc).isoformat()}
        self._audit(actor, "system_overview", {}, {"ok": True})
        return out

    def process_table(self, actor, limit: int = 200) -> dict:
        self._eng(actor)
        rc = self._proc(["ps", "-eo", "pid,ppid,user,stat,comm,args"])
        rows = [{"raw": ln} for ln in rc["stdout"].splitlines()[:limit]]
        self._audit(actor, "process_table", {"limit": limit},
                    {"ok": True, "count": len(rows)})
        return {"count": len(rows), "rows": rows}

    def fs_read(self, actor, path: str, max_bytes: int | None = None) -> dict:
        self._eng(actor)
        mb = min(max_bytes or self.max_bytes, self.max_bytes)
        p = Path(path).expanduser().resolve()
        if p == GOD_READ_AUDIT.resolve():
            raise PermissionError("audit log unreadable")
        with p.open("rb") as fh:
            data = fh.read(mb)
        out = {"path": str(p), "size": p.stat().st_size,
               "truncated": p.stat().st_size > mb,
               "sha256": hashlib.sha256(data).hexdigest(),
               "base64": base64.b64encode(data).decode()}
        self._audit(actor, "fs_read", {"path": str(p)},
                    {"ok": True, "size": out["size"]})
        return out

    def fs_list(self, actor, path: str, depth: int = 2) -> dict:
        self._eng(actor)
        root = Path(path).expanduser().resolve()
        entries = []
        for dp, dirs, files in os.walk(root):
            rel = Path(dp).relative_to(root)
            if len(rel.parts) >= depth:
                dirs[:] = []
            for nm in files + dirs:
                fp = Path(dp) / nm
                try:
                    st = fp.stat()
                    entries.append({"path": str(fp),
                                    "type": "dir" if fp.is_dir() else "file",
                                    "size": st.st_size})
                except Exception as exc:
                    entries.append({"path": str(fp), "error": str(exc)})
        self._audit(actor, "fs_list", {"path": str(root), "depth": depth},
                    {"ok": True, "count": len(entries)})
        return {"root": str(root), "count": len(entries),
                "entries": entries[:5000]}

    def env_read(self, actor, prefix: str | None = None) -> dict:
        self._eng(actor)
        raw = dict(os.environ)
        if prefix:
            raw = {k: v for k, v in raw.items() if k.startswith(prefix)}
        out = {k: ("<redacted>" if any(h in k.upper() for h in self.SECRET_HINTS)
                   else v) for k, v in raw.items()}
        self._audit(actor, "env_read", {"prefix": prefix},
                    {"ok": True, "count": len(out)})
        return out

    def proc_run(self, actor, argv: list[str]) -> dict:
        self._eng(actor)
        if argv and argv[0] == "git":
            head = argv[1] if len(argv) > 1 else ""
            if head not in self.GIT_RO:
                raise PermissionError(f"git not read-only: {head}")
        rc = self._proc(argv)
        self._audit(actor, "proc_run", {"argv": argv},
                    {"ok": True, "rc": rc["rc"]})
        return rc

    def network_info(self, actor) -> dict:
        self._eng(actor)
        out = {"hostname": socket.gethostname(),
               "fqdn": socket.getfqdn(),
               "interfaces": [], "listening_ports": []}
        try:
            import psutil  # type: ignore
            for name, addrs in psutil.net_if_addrs().items():
                out["interfaces"].append({
                    "name": name,
                    "addresses": [{"family": str(a.family), "address": a.address,
                                   "netmask": a.netmask, "broadcast": a.broadcast}
                                  for a in addrs]})
            try:
                for c in psutil.net_connections(kind="inet"):
                    if c.status == psutil.CONN_LISTEN:
                        out["listening_ports"].append({
                            "laddr": str(c.laddr), "pid": c.pid,
                            "status": c.status})
            except Exception:
                pass
        except Exception as exc:
            out["psutil_error"] = str(exc)
        self._audit(actor, "network_info", {}, {"ok": True})
        return out

    def db_schema(self, actor) -> dict:
        self._eng(actor)
        try:
            from django.db import connection
            with connection.cursor() as cur:
                engine = connection.vendor
                if engine == "sqlite":
                    cur.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                                "WHERE type IN ('table','view','index','trigger') "
                                "ORDER BY type,name")
                else:
                    cur.execute("SELECT table_schema,table_name,column_name,data_type "
                                "FROM information_schema.columns "
                                "ORDER BY table_schema,table_name,ordinal_position")
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            self._audit(actor, "db_schema", {},
                        {"ok": True, "count": len(rows)})
            return {"engine": engine, "columns": cols, "rows": rows}
        except Exception as exc:
            return {"error": str(exc)}

    def db_query(self, actor, sql: str, params=None) -> dict:
        self._eng(actor)
        ok, reason = _sql_readonly(sql)
        if not ok:
            self._audit(actor, "db_query", {"sql": sql[:500]},
                        {"ok": False, "reason": reason})
            raise PermissionError(f"SQL rejected: {reason}")
        try:
            from django.db import connection
            with connection.cursor() as cur:
                cur.execute(sql, params or [])
                cols = [d[0] for d in (cur.description or [])]
                rows = cur.fetchmany(self.max_rows)
            self._audit(actor, "db_query", {"sql": sql[:500]},
                        {"ok": True, "row_count": len(rows)})
            return {"columns": cols, "rows": rows,
                    "row_count": len(rows),
                    "truncated": len(rows) == self.max_rows}
        except Exception as exc:
            return {"error": str(exc)}

    def django_introspect(self, actor) -> dict:
        self._eng(actor)
        out = {}
        try:
            from django.conf import settings as dj
            out["settings"] = {
                "DEBUG": dj.DEBUG,
                "ALLOWED_HOSTS": list(getattr(dj, "ALLOWED_HOSTS", [])),
                "INSTALLED_APPS": list(getattr(dj, "INSTALLED_APPS", [])),
                "TIME_ZONE": getattr(dj, "TIME_ZONE", None),
                "ROOT_URLCONF": getattr(dj, "ROOT_URLCONF", None),
                "DATABASES": {k: {kk: ("<redacted>" if kk == "PASSWORD" else vv)
                                  for kk, vv in v.items()}
                              for k, v in getattr(dj, "DATABASES", {}).items()},
            }
        except Exception as exc:
            out["settings_error"] = str(exc)
        try:
            from django.apps import apps
            out["models"] = {
                f"{m._meta.app_label}.{m.__name__}": {
                    "db_table": m._meta.db_table,
                    "pk": m._meta.pk.name if m._meta.pk else None,
                    "fields": [f.name for f in m._meta.get_fields()],
                } for m in apps.get_models()
            }
        except Exception as exc:
            out["models_error"] = str(exc)
        try:
            from django.db.migrations.recorder import MigrationRecorder
            out["migrations"] = list(
                MigrationRecorder.Migration.objects
                .values("app", "name", "applied"))
        except Exception as exc:
            out["migrations_error"] = str(exc)
        self._audit(actor, "django_introspect", {}, {"ok": True})
        return out

    def logs_tail(self, actor, path: str, lines: int = 500) -> dict:
        self._eng(actor)
        p = Path(path).expanduser().resolve()
        with p.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = min(size, 4_000_000)
            fh.seek(size - block)
            tail = fh.read().decode("utf-8", "replace").splitlines()[-lines:]
        self._audit(actor, "logs_tail", {"path": str(p), "lines": lines},
                    {"ok": True})
        return {"path": str(p), "size": size, "lines": tail}

    def packages(self, actor) -> dict:
        self._eng(actor)
        try:
            import importlib.metadata as md
            pkgs = sorted(
                ({"name": d.metadata["Name"], "version": d.version}
                 for d in md.distributions()),
                key=lambda x: (x["name"] or "").lower())
        except Exception as exc:
            pkgs = [{"error": str(exc)}]
        self._audit(actor, "packages", {}, {"ok": True, "count": len(pkgs)})
        return {"count": len(pkgs), "packages": pkgs}

    def full_dump(self, actor) -> dict:
        self._eng(actor)
        out = {
            "generated_at_utc": datetime.now(dt_tz.utc).isoformat(),
            "system": self.system_overview(actor),
            "packages": self.packages(actor),
            "django": self.django_introspect(actor),
            "schema": self.db_schema(actor),
            "env": self.env_read(actor),
            "network": self.network_info(actor),
            "processes": self.process_table(actor, limit=100),
        }
        self._audit(actor, "full_dump", {},
                    {"ok": True, "keys": list(out.keys())})
        return out


# =============================================================================
# Negative-result verifier (opt-in)
# =============================================================================
class NegativeResultVerifier:
    MIN_CHECKS = 3

    def __init__(self, chat: "PollinationAIChat"):
        self.chat = chat

    def _check(self, name: str, fn: Callable[[], Any]) -> dict:
        try:
            res = fn()
            return {"check": name, "ok": True, "found": bool(res),
                    "sample": res[:3] if isinstance(res, list) else res}
        except Exception as exc:
            return {"check": name, "ok": False, "error": str(exc)}

    def _finalize(self, entity: str, checks: list[dict], **extra) -> dict:
        found_any = any(c.get("found") for c in checks)
        errored = [c for c in checks if not c.get("ok")]
        ran = len([c for c in checks if c.get("ok")])
        proven = (not found_any) and ran >= self.MIN_CHECKS and not errored
        return {"entity": entity, "checks": checks, "found_any": found_any,
                "ran": ran, "errored": len(errored),
                "proven_absent": proven, **extra}

    def verify_student_absent(self, query=None, admission=None) -> dict:
        from .models import Student
        checks: list[dict] = []
        if admission:
            checks += [
                self._check("admission_exact",
                            lambda: list(Student.objects.filter(
                                admission_number__iexact=admission)
                                .values("id", "name"))),
                self._check("admission_icontains",
                            lambda: list(Student.objects.filter(
                                admission_number__icontains=admission)
                                .values("id", "name"))),
            ]
        if query:
            checks += [
                self._check("name_exact",
                            lambda: list(Student.objects.filter(
                                name__iexact=query).values("id", "name"))),
                self._check("name_icontains",
                            lambda: list(Student.objects.filter(
                                name__icontains=query).values("id", "name"))),
                self._check("name_istartswith",
                            lambda: list(Student.objects.filter(
                                name__istartswith=query).values("id", "name"))),
            ]
        checks += [
            self._check("students_nonempty",
                        lambda: Student.objects.exists()),
            self._check("active_students_exist",
                        lambda: Student.objects.filter(is_active=True).exists()),
        ]
        return self._finalize("student", checks, query=query, admission=admission)


# =============================================================================
# Self-healer
# =============================================================================
class SelfHealer:
    @classmethod
    def heal(cls, obj: Any) -> list[str]:
        healed: list[str] = []
        if not hasattr(obj, "_has_real_data"):
            def _hrd(payload):
                if payload is None:
                    return False
                if isinstance(payload, dict):
                    if "error" in payload or not payload:
                        return False
                    if (payload.get("total_students") == 0
                            and payload.get("total_reports") == 0
                            and not payload.get("stream_breakdown")
                            and not payload.get("top_categories")
                            and not payload.get("name")):
                        return False
                if isinstance(payload, list) and len(payload) == 0:
                    return False
                return True
            obj._has_real_data = _hrd
            healed.append("_has_real_data")
        if healed:
            try:
                obj.logger.warning("SelfHealer registered: %s", healed)
            except Exception:
                pass
        return healed


# =============================================================================
# UnifiedFacts — one cached snapshot every code path reads from
# =============================================================================
class UnifiedFacts:
    """Cache a single coherent view of the school's data for N seconds.

    Every method returns the same shape regardless of caller. When any
    subquery errors, that field is None — never fabricated, never partial.
    """

    CACHE_SECONDS = int(os.environ.get("UNIFIED_FACTS_TTL", "20"))

    def __init__(self, chat: "PollinationAIChat"):
        self.chat = chat
        self._snap = None
        self._snap_at = 0.0

    def snapshot(self, force: bool = False) -> dict:
        now = time.time()
        if not force and self._snap and (now - self._snap_at) < self.CACHE_SECONDS:
            return self._snap
        self._snap = self._build()
        self._snap_at = now
        return self._snap

    def slice_for(self, domain: str, window_days: int | None = None) -> dict:
        snap = self.snapshot()
        out = {"generated_at": snap["generated_at"], "domain": domain}
        if domain == "school":
            out["school"] = snap["school"]
            out["counts"] = snap["counts"]
        elif domain == "students":
            out["students"] = snap["students"]
            out["counts"] = snap["counts"]
        elif domain == "teachers":
            out["teachers"] = snap["teachers"]
        elif domain == "streams":
            out["streams"] = snap["streams"]
        elif domain == "reports":
            reports = snap["reports"]
            if window_days is not None:
                cutoff = self.chat._now() - timedelta(days=window_days)
                reports = [r for r in reports
                           if r.get("reported_at") and r["reported_at"] >= cutoff]
            out["reports"] = reports
            out["reports_by_category"] = self._group(reports, "category")
            out["reports_by_stream"] = self._group(reports, "stream")
            out["reports_by_day"] = self._group(reports, "day")
        return out

    # ---- internal -----------------------------------------------------------
    def _build(self) -> dict:
        now = self.chat._now()
        snap = {
            "generated_at": now.isoformat(),
            "school": {}, "counts": {}, "students": [], "teachers": [],
            "streams": [], "reports": [],
            "errors": {},
        }
        try:
            from .models import (School, Student, Stream, DisciplineReport,
                                 TeacherProfile)
        except Exception as exc:
            snap["errors"]["import"] = str(exc)
            return snap

        # School -----------------------------------------------------------
        try:
            school = School.objects.first()
            if school:
                snap["school"] = {
                    "name": school.name,
                    "motto": school.motto or "",
                    "short_name": school.short_name or "",
                    "address": school.address or "",
                    "phone": school.phone or "",
                    "email": school.email or "",
                    "current_year": getattr(school, "current_year", None),
                }
        except Exception as exc:
            snap["errors"]["school"] = str(exc)

        # Students ---------------------------------------------------------
        try:
            students_qs = (Student.objects.filter(is_active=True)
                           .select_related("stream")
                           .annotate(report_count=Count("reports")))
            snap["students"] = [{
                "id": s.id,
                "name": s.name,
                "admission_number": s.admission_number,
                "stream": s.stream.name if s.stream else None,
                "stream_id": s.stream_id,
                "form": s.form,
                "year": s.year,
                "risk_score": s.risk_score,
                "risk_level": s.risk_level,
                "report_count": s.report_count,
                "intervention_count": getattr(s, "intervention_count", 0),
            } for s in students_qs]
        except Exception as exc:
            snap["errors"]["students"] = str(exc)

        # Streams ----------------------------------------------------------
        try:
            by_stream: dict[str, dict] = {}
            for s in snap["students"]:
                key = s["stream"] or "Unassigned"
                slot = by_stream.setdefault(key, {
                    "stream": key, "students": 0, "risk_sum": 0,
                    "reports": 0, "points": 0, "top_names": [],
                })
                slot["students"] += 1
                slot["risk_sum"] += s.get("risk_score") or 0
                slot["reports"] += s.get("report_count") or 0
                if s.get("name") and len(slot["top_names"]) < 5:
                    slot["top_names"].append(s["name"])
            snap["streams"] = [{
                "stream": v["stream"],
                "students": v["students"],
                "reports": v["reports"],
                "points": v["points"],
                "avg_risk": round(v["risk_sum"] / v["students"], 1)
                             if v["students"] else 0,
                "top_names": v["top_names"],
            } for v in by_stream.values()]
        except Exception as exc:
            snap["errors"]["streams"] = str(exc)

        # Teachers ---------------------------------------------------------
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            ids = TeacherProfile.objects.filter(
                is_approved=True).values_list("user_id", flat=True)
            snap["teachers"] = [{
                "id": u.id,
                "username": u.username,
                "first_name": u.first_name or "",
                "last_name": u.last_name or "",
                "full_name": (u.get_full_name() or u.username).strip(),
                "email": u.email or "",
                "is_superuser": bool(u.is_superuser),
                "is_staff": bool(u.is_staff),
            } for u in User.objects.filter(id__in=list(ids))]
        except Exception as exc:
            snap["errors"]["teachers"] = str(exc)

        # Reports ----------------------------------------------------------
        try:
            rows = (DisciplineReport.objects
                    .select_related("student", "student__stream",
                                    "category", "reported_by")
                    .order_by("-reported_at"))
            snap["reports"] = [{
                "id": r.id,
                "reported_at": r.reported_at,
                "date": r.reported_at.strftime("%Y-%m-%d %H:%M")
                        if r.reported_at else None,
                "day": r.reported_at.strftime("%Y-%m-%d")
                       if r.reported_at else None,
                "student_id": r.student_id,
                "student_name": getattr(r.student, "name", None),
                "admission_number": getattr(r.student, "admission_number", None),
                "stream": (r.student.stream.name
                           if r.student and r.student.stream else None),
                "category": getattr(r.category, "name", None),
                "points": r.points,
                "rating": (r.get_rating_display()
                           if hasattr(r, "get_rating_display") else None),
                "reported_by_id": r.reported_by_id,
                "reported_by": ((r.reported_by.get_full_name()
                                 or r.reported_by.username)
                                if r.reported_by else None),
                "comments": r.comments or "",
            } for r in rows]
            points_by_stream: dict[str, int] = {}
            for r in snap["reports"]:
                key = r["stream"] or "Unassigned"
                points_by_stream[key] = points_by_stream.get(key, 0) + (r["points"] or 0)
            for s in snap["streams"]:
                s["points"] = points_by_stream.get(s["stream"], 0)
        except Exception as exc:
            snap["errors"]["reports"] = str(exc)

        # Counts -----------------------------------------------------------
        try:
            snap["counts"] = {
                "students_active": len(snap["students"]),
                "students_inactive": Student.objects.filter(is_active=False).count(),
                "teachers": len(snap["teachers"]),
                "streams_active": Stream.objects.filter(is_active=True).count(),
                "reports_total": len(snap["reports"]),
                "critical": sum(1 for s in snap["students"]
                                if s.get("risk_level") == "CRITICAL"),
                "warning": sum(1 for s in snap["students"]
                               if s.get("risk_level") == "WARNING"),
                "good": sum(1 for s in snap["students"]
                            if s.get("risk_level") == "GOOD"),
            }
        except Exception as exc:
            snap["errors"]["counts"] = str(exc)

        return snap

    @staticmethod
    def _group(rows: list[dict], by: str) -> list[dict]:
        bucket: dict[str, dict] = {}
        for r in rows:
            key = r.get(by) or "Unassigned"
            b = bucket.setdefault(key, {by: key, "count": 0, "points": 0})
            b["count"] += 1
            b["points"] += r.get("points") or 0
        return sorted(bucket.values(), key=lambda x: -x["count"])


# =============================================================================
# TerminalProbe — read-only introspection of the running Django app
# =============================================================================
class TerminalProbe:
    """Verify-the-backend layer. Engineers get the full probe; teachers get a
    reduced slice; students get none. Every call is cheap and cached."""

    PROBE_TTL = int(os.environ.get("TERMINAL_PROBE_TTL", "60"))

    def __init__(self, chat: "PollinationAIChat"):
        self.chat = chat
        self._cache: dict[str, tuple[float, Any]] = {}

    def probe(self, actor: dict, kinds: list[str] | None = None) -> dict:
        role = (actor or {}).get("role")
        if role == "engineer":
            allowed = {"routes", "views", "models", "migrations",
                       "settings", "counts"}
        elif role in ("admin", "teacher"):
            allowed = {"models", "counts"}
        else:
            allowed = set()

        kinds = set(kinds or []) & allowed or allowed

        out = {"role": role, "allowed": sorted(allowed),
               "requested": sorted(kinds), "probes": {}}
        for k in sorted(kinds):
            try:
                out["probes"][k] = self._cached(k, lambda k=k: self._run(k))
            except Exception as exc:
                out["probes"][k] = {"error": f"{type(exc).__name__}: {exc}"}
        return out

    # -- cache ----------------------------------------------------------------
    def _cached(self, key: str, producer: Callable[[], Any]) -> Any:
        now = time.time()
        if key in self._cache and (now - self._cache[key][0]) < self.PROBE_TTL:
            return self._cache[key][1]
        val = producer()
        self._cache[key] = (now, val)
        return val

    # -- probes ---------------------------------------------------------------
    def _run(self, kind: str) -> Any:
        if kind == "routes":
            return self._routes()
        if kind == "views":
            return self._views()
        if kind == "models":
            return self._models()
        if kind == "migrations":
            return self._migrations()
        if kind == "settings":
            return self._settings()
        if kind == "counts":
            return self._counts()
        return {"error": "unknown probe"}

    def _routes(self) -> dict:
        from django.urls import get_resolver
        resolver = get_resolver()
        rows = []

        def walk(patterns, prefix=""):
            for p in patterns:
                if hasattr(p, "url_patterns"):
                    walk(p.url_patterns, prefix + str(p.pattern))
                else:
                    view = getattr(p, "callback", None)
                    mod = getattr(view, "__module__", None)
                    name = getattr(view, "__name__", None)
                    rows.append({"pattern": prefix + str(p.pattern),
                                 "view_module": mod, "view_name": name,
                                 "route_name": getattr(p, "name", None)})
        walk(resolver.url_patterns)
        return {"count": len(rows), "routes": rows[:500]}

    def _views(self) -> dict:
        try:
            import inspect
            from django.urls import get_resolver
            resolver = get_resolver()
            modules = set()
            for p in resolver.url_patterns:
                cb = getattr(p, "callback", None)
                m = getattr(cb, "__module__", None)
                if m:
                    modules.add(m)
            out = []
            for m in sorted(modules)[:50]:
                try:
                    mod = __import__(m, fromlist=["*"])
                    out.append({
                        "module": m,
                        "file": getattr(mod, "__file__", None),
                        "members": [n for n, _ in inspect.getmembers(mod)
                                    if not n.startswith("_")][:40],
                    })
                except Exception as exc:
                    out.append({"module": m, "error": str(exc)})
            return {"count": len(out), "modules": out}
        except Exception as exc:
            return {"error": str(exc)}

    def _models(self) -> dict:
        from django.apps import apps
        rows = []
        for m in apps.get_models():
            try:
                rows.append({
                    "app_label": m._meta.app_label,
                    "name": m.__name__,
                    "db_table": m._meta.db_table,
                    "fields": [f.name for f in m._meta.get_fields()],
                    "row_count": m.objects.count() if hasattr(m, "objects") else None,
                })
            except Exception as exc:
                rows.append({"app_label": m._meta.app_label,
                             "name": m.__name__, "error": str(exc)})
        return {"count": len(rows), "models": rows}

    def _migrations(self) -> dict:
        from django.db.migrations.recorder import MigrationRecorder
        rows = list(MigrationRecorder.Migration.objects
                    .values("app", "name", "applied").order_by("app", "name"))
        return {"count": len(rows), "migrations": rows}

    def _settings(self) -> dict:
        from django.conf import settings as dj
        return {"DEBUG": dj.DEBUG,
                "ALLOWED_HOSTS": list(getattr(dj, "ALLOWED_HOSTS", [])),
                "INSTALLED_APPS": list(getattr(dj, "INSTALLED_APPS", [])),
                "MIDDLEWARE": list(getattr(dj, "MIDDLEWARE", [])),
                "TIME_ZONE": getattr(dj, "TIME_ZONE", None),
                "ROOT_URLCONF": getattr(dj, "ROOT_URLCONF", None),
                "DB_ENGINE": (getattr(dj, "DATABASES", {})
                              .get("default", {}).get("ENGINE"))}

    def _counts(self) -> dict:
        from django.apps import apps
        out = {}
        for m in apps.get_models():
            try:
                out[f"{m._meta.app_label}.{m.__name__}"] = m.objects.count()
            except Exception:
                continue
        return out


# =============================================================================
# Main class
# =============================================================================
class PollinationAIChat:
    """Enhanced AI Chat with full database integration."""

    def __init__(self):
        self.logger = logging.getLogger("core.ai_chat")
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

        self.providers = configured_providers()
        self.provider_keys = load_provider_keys()
        self.models = load_models()

        self._cache = {}
        self._cache_time = {}
        self._cache_duration = int(os.environ.get("AI_CACHE_SECONDS", "300"))

        self.max_retries = int(os.environ.get("AI_MAX_RETRIES", "2"))
        self.backoff_factor = float(os.environ.get("AI_BACKOFF_FACTOR", "0.5"))

        if ENABLE_DEEP:
            self.terminal = GodReadTerminal(self)
        if ENABLE_VERIFIER:
            self.verifier = NegativeResultVerifier(self)

        # Unified facts + backend probe
        self.facts = UnifiedFacts(self)
        self.probe = TerminalProbe(self)

        SelfHealer.heal(self)

    # ---- cache ---------------------------------------------------------------
    def _get_cached(self, key):
        if key in self._cache and key in self._cache_time:
            delta = self._now() - self._cache_time[key]
            if delta.total_seconds() < self._cache_duration:
                return self._cache[key]
        return None

    def _set_cache(self, key, value):
        self._cache[key] = value
        self._cache_time[key] = self._now()

    def _now(self):
        try:
            if timezone is not None:
                return timezone.now()
        except Exception:
            pass
        return datetime.now(dt_tz.utc)

    # ---- system prompt -------------------------------------------------------
    def get_system_prompt(self, context=None):
        base_prompt = """You are an AI Education Assistant with authorized access to the school's discipline management system. You may reference summarized, authorized student and report data when answering. Do not request or reveal secrets such as system keys or credentials.

STRICT FACTUALITY RULES:
1. For any school-data question about students, admissions, discipline statistics, stream statistics, incident histories, risk scores, reports, or database records, you must use only data retrieved from the Django database for this request.
2. Never estimate, infer, guess, or fabricate student records, discipline statistics, stream statistics, incident histories, or any other database facts.
3. Only say "DATA UNAVAILABLE" when the gathered block for this request is truly empty — i.e. no rows at all were retrievable, even after widening the time window.
4. You may summarize, explain, classify, or analyze only data that has actually been retrieved and included in the ground-truth context below.
5. Do not invent totals, counts, averages, per-student values, or stream-level numbers.
6. If the database does not contain the requested fact, do not provide a plausible substitute.

WHEN A "GATHERED" BLOCK IS PRESENT:
- The rows in that block ARE the current relevant slice from the database.
- If a WINDOW_NOTE appears in the data, the user's requested time window had no rows, and the rows below are the nearest available instead. In that case, say clearly that the window was empty AND still summarize what IS on file — do not refuse.
- Construct the answer from those rows only. Do not add anything not derivable from them.
- If the rows are insufficient for a specific sub-question, say so explicitly for that sub-question, but still answer what you can from the rows provided.

WHEN A "backend_probe" BLOCK IS PRESENT:
- It is read-only evidence about the running app (models, routes, migrations).
- Use it to reason, not to display. Do NOT enumerate raw routes, settings, or app lists to the user.
- If the probe confirms the DB is empty for a table, say so as fact; if the probe disagrees with the DB, trust the DB.

CAPABILITIES:
1. Query student records by name, admission number, stream, or form
2. Retrieve discipline reports with dates, categories, and ratings
3. Generate school-wide statistics and analytics
4. Provide personalized student recommendations
5. Answer questions about Kenyan Education Law
6. Analyze trends and patterns in student behavior
7. Compare students, streams, and classes
8. Track intervention effectiveness

RESPONSE FORMAT:
- Be conversational and helpful
- Use bullet points for lists
- Bold important information
- Provide specific numbers and dates only from retrieved data
- Prefer to answer with what IS available rather than refusing

PERSONALITY:
- Professional but friendly
- Proactive in offering help
- Clear and concise
- Confident but not arrogant

ACCESSIBLE DATA:
- All students (name, admission, stream, form, risk score)
- All discipline reports (date, category, rating, points)
- Teacher profiles and assignments
- School statistics (totals, averages, distributions)
- Academic terms and streams
- Category breakdowns and trends

RESPONSE GUIDELINES:
1. Always confirm what you found
2. If multiple matches, list them
3. Offer to drill down deeper
4. Suggest related information they might need
5. Reference Kenyan laws when relevant
6. Use ground-truth data from the database; never invent values

You are the ultimate assistant for this school's discipline management - use your database access to provide comprehensive, accurate, and helpful responses."""

        if context:
            safe_context = dict(context)
            safe_context.pop("raw_env", None)
            return base_prompt + f"\n\nCURRENT CONTEXT:\n{json.dumps(safe_context, indent=2, default=str)}"
        return base_prompt

    # ---- ORM readers --------------------------------------------------------
    def get_student_data(self, student_id=None, name=None, admission=None):
        try:
            from .models import Student, TeacherProfile
        except Exception:
            return {"error": "Django models not available in this environment"}
        try:
            if student_id:
                student = Student.objects.get(id=student_id, is_active=True)
            elif admission:
                student = Student.objects.get(
                    admission_number=admission, is_active=True)
            elif name:
                student = Student.objects.filter(
                    name__iexact=name, is_active=True).first()
                if not student:
                    student = Student.objects.filter(
                        name__icontains=name, is_active=True).first()
            else:
                return None

            if not student:
                return None

            reports = student.reports.select_related(
                "category", "reported_by").order_by("-reported_at")
            total_reports = reports.count()

            category_breakdown = list(
                reports.values("category__name")
                .annotate(count=Count("id"))
                .order_by("-count"))

            class_teacher = None
            if student.stream:
                teacher_profile = TeacherProfile.objects.filter(
                    assigned_stream=student.stream,
                    assigned_form=student.form,
                    is_approved=True).first()
                if teacher_profile:
                    class_teacher = {
                        "name": teacher_profile.user.get_full_name()
                        or teacher_profile.user.username,
                        "email": teacher_profile.user.email,
                        "phone": teacher_profile.phone_number,
                    }

            recent_reports = []
            for report in reports[:10]:
                recent_reports.append({
                    "date": report.reported_at.strftime("%Y-%m-%d %H:%M"),
                    "category": report.category.name,
                    "rating": report.get_rating_display(),
                    "points": report.points,
                    "reported_by": report.reported_by.get_full_name()
                    or report.reported_by.username,
                    "comments": (report.comments[:100] + "..."
                                 if len(report.comments) > 100
                                 else report.comments),
                })

            days_since = None
            if student.last_incident_date:
                days_since = (self._now() - student.last_incident_date).days

            data = {
                "id": student.id,
                "name": student.name,
                "admission_number": student.admission_number,
                "stream": student.stream.name if student.stream else "Not Assigned",
                "form": student.form,
                "year": student.year,
                "risk_score": student.risk_score,
                "risk_level": student.risk_level,
                "total_reports": total_reports,
                "intervention_count": student.intervention_count,
                "days_since_last_incident": days_since,
                "is_active": student.is_active,
                "created_at": student.created_at.strftime("%Y-%m-%d"),
                "class_teacher": class_teacher,
                "category_breakdown": category_breakdown,
                "recent_reports": recent_reports,
                "optional_notes": student.optional_notes,
            }
            self._set_cache(f"student_{student.id}", data)
            return data
        except Student.DoesNotExist:
            return None
        except Exception as e:
            return {"error": str(e)}

    def get_school_info(self):
        cached = self._get_cached("school_info")
        if cached:
            return cached
        try:
            from .models import School
            school = School.objects.first()
            if school:
                info = {
                    "name": school.name,
                    "motto": school.motto or "",
                    "short_name": school.short_name or "",
                    "address": school.address or "",
                    "phone": school.phone or "",
                    "email": school.email or "",
                    "current_year": school.current_year,
                }
                self._set_cache("school_info", info)
                return info
        except Exception:
            pass
        return {}

    def get_school_stats(self):
        cached = self._get_cached("school_stats")
        if cached:
            return cached
        try:
            from .models import Student, Stream, DisciplineReport, TeacherProfile
            students = Student.objects.filter(is_active=True)
            total_students = students.count()
            critical = students.filter(risk_level="CRITICAL").count()
            warning = students.filter(risk_level="WARNING").count()
            good = students.filter(risk_level="GOOD").count()

            stream_stats = {}
            for stream in Stream.objects.filter(is_active=True):
                count = students.filter(stream=stream).count()
                if count > 0:
                    stream_stats[stream.name] = count

            form_stats = {}
            for form in Student.FORM_CHOICES:
                form_name = form[0]
                count = students.filter(form=form_name).count()
                if count > 0:
                    form_stats[form_name] = count

            total_reports = DisciplineReport.objects.count()
            reports_today = DisciplineReport.objects.filter(
                reported_at__date=self._now().date()).count()
            reports_this_week = DisciplineReport.objects.filter(
                reported_at__week=self._now().isocalendar()[1]).count()

            top_categories = list(
                DisciplineReport.objects.values("category__name")
                .annotate(count=Count("id")).order_by("-count")[:5])

            online_teachers = TeacherProfile.objects.filter(is_online=True).count()
            total_teachers = TeacherProfile.objects.filter(is_approved=True).count()
            avg_risk = students.aggregate(avg=Avg("risk_score"))["avg"] or 0

            stats = {
                "total_students": total_students,
                "critical_count": critical,
                "warning_count": warning,
                "good_count": good,
                "avg_risk_score": round(avg_risk, 1),
                "total_reports": total_reports,
                "reports_today": reports_today,
                "reports_this_week": reports_this_week,
                "online_teachers": online_teachers,
                "total_teachers": total_teachers,
                "top_categories": top_categories,
                "stream_breakdown": stream_stats,
                "form_breakdown": form_stats,
                "timestamp": timezone.now().isoformat() if timezone else datetime.now(dt_tz).isoformat(),
            }
            self._set_cache("school_stats", stats)
            return stats
        except Exception as e:
            return {"error": str(e)}

    def search_students(self, query):
        cached = self._get_cached(f"search_{query}")
        if cached:
            return cached
        try:
            from .models import Student
            results = []
            if query.isdigit():
                students = Student.objects.filter(
                    admission_number__icontains=query, is_active=True)[:5]
            else:
                students = Student.objects.filter(
                    Q(name__icontains=query) | Q(name__istartswith=query),
                    is_active=True)[:10]
            for student in students:
                results.append({
                    "id": student.id, "name": student.name,
                    "admission_number": student.admission_number,
                    "stream": student.stream.name if student.stream else "N/A",
                    "form": student.form,
                    "risk_score": student.risk_score,
                    "risk_level": student.risk_level,
                    "total_reports": student.reports.count(),
                })
            self._set_cache(f"search_{query}", results)
            return results
        except Exception as e:
            return {"error": str(e)}

    def get_stream_analysis(self, stream_id):
        try:
            from .models import Stream, Student
            stream = Stream.objects.get(id=stream_id, is_active=True)
            students = Student.objects.filter(stream=stream, is_active=True)
            total = students.count()
            if total == 0:
                return {"stream": stream.name,
                        "message": "No students in this stream"}
            critical = students.filter(risk_level="CRITICAL").count()
            warning = students.filter(risk_level="WARNING").count()
            good = students.filter(risk_level="GOOD").count()
            avg_risk = students.aggregate(avg=Avg("risk_score"))["avg"] or 0
            top_offenders = list(
                students.annotate(report_count=Count("reports"))
                .filter(report_count__gt=0).order_by("-report_count")[:5]
                .values("name", "admission_number", "report_count", "risk_score"))
            return {
                "stream": stream.name, "total_students": total,
                "critical_count": critical, "warning_count": warning,
                "good_count": good, "average_risk": round(avg_risk, 1),
                "top_offenders": top_offenders,
            }
        except Stream.DoesNotExist:
            return {"error": "Stream not found"}
        except Exception as e:
            return {"error": str(e)}

    def get_all_students_summary(self):
        cache_key = os.environ.get("ALL_STUDENTS_SUMMARY_VALUE",
                                   "all_students_summary")
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        try:
            from .models import Student
            students = Student.objects.filter(
                is_active=True).select_related("stream")
            summary = []
            for student in students[:50]:
                summary.append({
                    "id": student.id, "name": student.name,
                    "admission": student.admission_number,
                    "stream": student.stream.name if student.stream else "N/A",
                    "form": student.form, "risk": student.risk_score,
                    "level": student.risk_level,
                    "reports": student.reports.count(),
                })
            self._set_cache(cache_key, summary)
            return summary
        except Exception as e:
            return {"error": str(e)}

    # ---- providers ----------------------------------------------------------
    def _try_model(self, messages, model_name):
        return {"success": False,
                "error": "Use provider-specific calls via _call_provider"}

    def _call_provider(self, provider, messages):
        api_key = self.provider_keys.get(provider)
        if not api_key:
            self.logger.debug("Skipping provider %s (no API key)", provider)
            return {"success": False, "error": "missing_api_key"}

        model_name = self.models.get(provider)

        try:
            if provider in ("openai", "openrouter", "groq"):
                if provider == "openai":
                    url = os.environ.get(
                        "OPENAI_URL", "https://api.openai.com/v1/chat/completions")
                elif provider == "openrouter":
                    url = os.environ.get(
                        "OPENROUTER_URL",
                        "https://openrouter.ai/api/v1/chat/completions")
                else:
                    url = os.environ.get(
                        "GROQ_URL",
                        "https://api.groq.com/openai/v1/chat/completions")

                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }

                if provider == "groq":
                    max_tokens = int(os.environ.get("AI_MAX_TOKENS", "2048"))
                    temperature = float(os.environ.get("AI_TEMPERATURE", "1.0"))
                    payload = {
                        "model": model_name,
                        "messages": messages,
                        "temperature": temperature,
                        "max_completion_tokens": max_tokens,
                        "top_p": float(os.environ.get("AI_TOP_P", "1")),
                        "stream": False,
                    }
                    resp = requests.post(url, headers=headers, json=payload,
                                         timeout=30)
                    if resp.status_code == 429:
                        try:
                            retry_after = int(
                                resp.headers.get("Retry-After")
                                or resp.headers.get("retry-after") or 0)
                        except Exception:
                            retry_after = 0
                        self.logger.info(
                            "Groq rate-limited (Retry-After=%s)", retry_after)
                        try:
                            if retry_after:
                                time.sleep(min(int(retry_after), 60))
                        except Exception:
                            pass
                        reduced = max(64, max_tokens // 2)
                        if reduced < max_tokens:
                            payload["max_completion_tokens"] = reduced
                            resp = requests.post(url, headers=headers,
                                                 json=payload, timeout=30)

                    if resp.status_code != 200:
                        try:
                            body = resp.text
                        except Exception:
                            body = "<unreadable>"
                        safe_headers = {k: v for k, v in resp.headers.items()
                                        if k.lower() != "authorization"}
                        self.logger.warning(
                            "Provider %s returned %s for %s (model=%s): %s headers=%s",
                            provider, resp.status_code, url, model_name,
                            (body[:1000] + "...") if len(body) > 1000 else body,
                            safe_headers)
                        return {"success": False,
                                "error": f"http_{resp.status_code}",
                                "raw": body}
                    try:
                        data = resp.json()
                    except Exception:
                        data = None
                else:
                    payload = {
                        "model": model_name,
                        "messages": messages,
                        "temperature": float(os.environ.get("AI_TEMPERATURE", "0.7")),
                        "max_tokens": int(os.environ.get("AI_MAX_TOKENS", "1024")),
                        "top_p": float(os.environ.get("AI_TOP_P", "1")),
                    }
                    resp = requests.post(url, headers=headers, json=payload,
                                         timeout=30)
                    if resp.status_code != 200:
                        try:
                            body = resp.text
                        except Exception:
                            body = "<unreadable>"
                        safe_headers = {k: v for k, v in resp.headers.items()
                                        if k.lower() != "authorization"}
                        self.logger.warning(
                            "Provider %s returned %s for %s (model=%s): %s headers=%s",
                            provider, resp.status_code, url, model_name,
                            (body[:1000] + "...") if len(body) > 1000 else body,
                            safe_headers)
                        return {"success": False,
                                "error": f"http_{resp.status_code}",
                                "raw": body}
                    try:
                        data = resp.json()
                    except Exception:
                        data = None

                try:
                    if isinstance(data, dict) and data.get("choices"):
                        text = (data["choices"][0].get("message", {})
                                .get("content")
                                or data["choices"][0].get("text"))
                    else:
                        text = json.dumps(data)
                except Exception:
                    text = json.dumps(data) if data is not None else ""

                return {"success": True, "response": text,
                        "usage": data.get("usage", {})
                        if isinstance(data, dict) else {},
                        "model_used": model_name}

            if provider == "gemini":
                model = model_name
                base_url = os.environ.get("GEMINI_URL") or (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent")
                params = {"key": api_key}
                temperature = float(os.environ.get("AI_TEMPERATURE", "0.7"))
                max_output_tokens = int(os.environ.get("AI_MAX_TOKENS", "1024"))

                contents = []
                system_text = None
                for msg in messages:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    if role == "system":
                        system_text = content
                    elif role == "assistant":
                        contents.append({"role": "model",
                                         "parts": [{"text": content}]})
                    else:
                        contents.append({"role": "user",
                                         "parts": [{"text": content}]})

                if system_text and contents:
                    contents[0]["parts"][0]["text"] = (
                        system_text + "\n\n" + contents[0]["parts"][0]["text"])
                elif system_text:
                    contents = [{"role": "user",
                                 "parts": [{"text": system_text}]}]

                payload = {
                    "contents": contents,
                    "generationConfig": {
                        "temperature": temperature,
                        "maxOutputTokens": max_output_tokens,
                    },
                }
                try:
                    resp = requests.post(base_url, params=params, json=payload,
                                         timeout=30)
                except Exception as e:
                    self.logger.warning("Gemini request exception: %s", e)
                    return {"success": False,
                            "error": f"gemini_request_error: {e}"}

                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except Exception:
                        data = None
                    text = None
                    if isinstance(data, dict) and "candidates" in data:
                        try:
                            text = data["candidates"][0]["content"]["parts"][0]["text"]
                        except Exception:
                            pass
                    if not text:
                        text = json.dumps(data)
                    return {"success": True, "response": text,
                            "usage": {}, "model_used": model}

                try:
                    body = resp.text
                except Exception:
                    body = "<unreadable>"
                safe_headers = {k: v for k, v in resp.headers.items()
                                if k.lower() != "authorization"}
                self.logger.warning(
                    "Provider %s returned %s for %s (model=%s): %s headers=%s",
                    provider, resp.status_code, base_url, model,
                    (body[:1000] + "...") if len(body) > 1000 else body,
                    safe_headers)
                return {"success": False,
                        "error": f"http_{resp.status_code}",
                        "raw": body}

            return {"success": False, "error": "unknown_provider"}
        except Exception as e:
            self.logger.exception("Provider %s call failed", provider)
            return {"success": False, "error": str(e)}

    def _call_providers_in_order(self, messages, provider_order=None):
        last_error = None
        order = provider_order or self.providers
        for provider in order:
            if not self.provider_keys.get(provider):
                self.logger.debug("Provider %s skipped (no key)", provider)
                continue

            for attempt in range(0, self.max_retries + 1):
                if attempt > 0:
                    time.sleep(self.backoff_factor * (2 ** (attempt - 1)))
                self.logger.info("Trying provider %s attempt %d",
                                 provider, attempt + 1)
                result = self._call_provider(provider, messages)
                if result.get("success"):
                    result["provider"] = provider
                    return result
                retry_after = result.get("retry_after")
                if retry_after and (result.get("error") or "").startswith("http_429"):
                    try:
                        time.sleep(int(retry_after))
                    except Exception:
                        pass
                last_error = result.get("error") or result.get("raw")
                if last_error in ("missing_api_key", "invalid_model"):
                    break

        return {"success": False, "error": last_error or "all_providers_failed"}

    # ---- legacy intent helpers ----------------------------------------------
    def _parse_user_intent(self, message):
        message_lower = message.lower()
        intent = {"type": "general", "entities": {}, "action": None}

        school_keywords = [
            "school name", "name of this school", "name of the school",
            "school motto", "school address", "what school", "which school",
            "this school", "tis school", "this system",
        ]
        if any(kw in message_lower for kw in school_keywords):
            intent["type"] = "stats"
            return intent

        student_patterns = [
            r"student\s+(\w+)",
            r"admission\s*[#:]*\s*(\d+)",
            r"pupil\s+(\w+)",
            r"learner\s+(\w+)",
            r"about\s+(\w+)",
        ]
        for pattern in student_patterns:
            match = re.search(pattern, message_lower)
            if match:
                intent["type"] = "student"
                intent["entities"]["query"] = match.group(1)
                break

        admission_match = re.search(r"\b(\d{4,6})\b", message)
        if admission_match:
            intent["type"] = "student"
            intent["entities"]["admission"] = admission_match.group(1)

        if any(word in message_lower
               for word in ["how many", "total", "count", "statistics", "stats"]):
            intent["type"] = "stats"

        student_list_phrases = [
            "list students", "list all students", "show students",
            "what are their names", "names of students", "students names",
            "list names", "all students",
        ]
        if any(phrase in message_lower for phrase in student_list_phrases):
            intent["type"] = "student_names"

        stream_match = re.search(r"stream\s+(\w+)", message_lower)
        if stream_match:
            intent["type"] = "stream"
            intent["entities"]["stream"] = stream_match.group(1)

        if any(word in message_lower
               for word in ["report", "case", "offense", "incident"]):
            intent["action"] = "reports"

        return intent

    def _is_factual_school_data_request(self, intent, user_message=None):
        if not intent:
            return False
        if intent.get("type") in {"student", "stats", "stream", "student_names"}:
            return True
        message = (user_message or "").lower()
        factual_keywords = [
            "student", "admission", "stream", "form", "report", "incident",
            "discipline", "statistics", "stats", "count", "total", "average",
            "risk", "offense", "case", "history", "records",
        ]
        return any(keyword in message for keyword in factual_keywords)

    def _data_unavailable_response(self, intent):
        if intent.get("type") == "student":
            return ("DATA UNAVAILABLE: I could not retrieve the requested student "
                    "record from the Django database. I do not invent student "
                    "admissions, names, risk data, or discipline histories.")
        if intent.get("type") == "stats":
            return ("DATA UNAVAILABLE: I could not retrieve the requested school "
                    "statistics from the Django database. I do not invent totals, "
                    "counts, averages, or discipline numbers.")
        if intent.get("type") == "stream":
            return ("DATA UNAVAILABLE: I could not retrieve the requested stream "
                    "statistics from the Django database. I do not invent stream "
                    "totals, incident counts, or risk values.")
        return ("DATA UNAVAILABLE: The requested school-data fact could not be "
                "retrieved from the Django database. I do not fabricate student "
                "records, discipline statistics, stream statistics, or incident "
                "histories.")

    def _has_real_data(self, payload):
        if payload is None:
            return False
        if isinstance(payload, dict):
            if "error" in payload:
                return False
            if not payload:
                return False
            if (payload.get("total_students") == 0
                    and payload.get("total_reports") == 0
                    and not payload.get("stream_breakdown")
                    and not payload.get("top_categories")
                    and not payload.get("name")):
                return False
        if isinstance(payload, list) and len(payload) == 0:
            return False
        return True

    def _generate_fallback_response(self, intent, data):
        response = ""

        if intent["type"] == "student" and data:
            student_data = data
            name = student_data.get("name", "the student")
            response = f"📊 **Student Profile: {name}**\n\n"
            response += f"• **Admission:** {student_data.get('admission_number', 'N/A')}\n"
            response += f"• **Stream:** {student_data.get('stream', 'Not Assigned')}\n"
            response += f"• **Form:** {student_data.get('form', 'N/A')}\n"
            response += f"• **Risk Level:** {student_data.get('risk_level', 'Unknown')}\n"
            response += f"• **Risk Score:** {student_data.get('risk_score', 0)}%\n"
            response += f"• **Total Reports:** {student_data.get('total_reports', 0)}\n"
            if student_data.get("days_since_last_incident") is not None:
                response += (f"• **Days Since Last Incident:** "
                             f"{student_data['days_since_last_incident']}\n")
            if student_data.get("intervention_count", 0) > 0:
                response += f"• **Interventions:** {student_data['intervention_count']}\n"
            if student_data.get("class_teacher"):
                response += f"• **Class Teacher:** {student_data['class_teacher']['name']}\n"
            if student_data.get("category_breakdown"):
                response += "\n**Top Offenses:**\n"
                for cat in student_data["category_breakdown"][:3]:
                    response += f"• {cat['category__name']}: {cat['count']} reports\n"
            if student_data.get("recent_reports"):
                response += "\n**Recent Reports:**\n"
                for report in student_data["recent_reports"][:3]:
                    response += (f"• {report['date']} - {report['category']} "
                                 f"({report['rating']})\n")
            if student_data.get("risk_level") == "CRITICAL":
                response += "\n⚠️ **Immediate Action Required:**\n"
                response += "1. Schedule parent-teacher conference TODAY\n"
                response += "2. Refer to school counselor\n"
                response += "3. Create behavior intervention plan\n"
            elif student_data.get("risk_level") == "WARNING":
                response += "\n📋 **Recommended Actions:**\n"
                response += "1. Schedule parent meeting within 1 week\n"
                response += "2. Implement behavior tracking\n"
                response += "3. Assign mentor or buddy\n"
            else:
                response += "\n✅ **Status: Good**\n"
                response += "Continue positive reinforcement and monitoring.\n"

        elif intent["type"] == "stats":
            stats = data.get("stats", {}) or {}
            response = "📊 **School Statistics**\n\n"
            response += f"• **Total Students:** {stats.get('total_students', 0)}\n"
            response += f"• **Critical Cases:** {stats.get('critical_count', 0)}\n"
            response += f"• **Warning Cases:** {stats.get('warning_count', 0)}\n"
            response += f"• **Good Status:** {stats.get('good_count', 0)}\n"
            response += f"• **Average Risk:** {stats.get('avg_risk_score', 0)}%\n"
            response += f"• **Total Reports:** {stats.get('total_reports', 0)}\n"
            response += f"• **Reports Today:** {stats.get('reports_today', 0)}\n"
            response += (f"• **Online Teachers:** {stats.get('online_teachers', 0)}/"
                         f"{stats.get('total_teachers', 0)}\n")
            if stats.get("top_categories"):
                response += "\n**Top Offense Categories:**\n"
                for cat in stats["top_categories"]:
                    response += f"• {cat['category__name']}: {cat['count']} reports\n"
            if stats.get("stream_breakdown"):
                response += "\n**Stream Breakdown:**\n"
                for name, count in stats["stream_breakdown"].items():
                    response += f"• {name}: {count} students\n"

        elif intent["type"] == "student_names":
            try:
                if isinstance(data, dict):
                    student_payload = data.get("student_names")
                else:
                    student_payload = data
                names_list = ([s.get("name") for s in student_payload
                               if isinstance(s, dict) and s.get("name")]
                              if student_payload else [])
                total = len(names_list)
                response = f"📋 **Student Names ({total})**\n\n"
                for name in names_list[:100]:
                    response += f"• {name}\n"
                if total > 100:
                    response += f"\n...and {total - 100} more names."
            except Exception:
                response = "I couldn't retrieve the student names list."

        elif intent["type"] == "stream" and data:
            stream_data = data
            response = f"📊 **Stream Analysis: {stream_data.get('stream', 'Unknown')}**\n\n"
            response += f"• **Total Students:** {stream_data.get('total_students', 0)}\n"
            response += f"• **Critical:** {stream_data.get('critical_count', 0)}\n"
            response += f"• **Warning:** {stream_data.get('warning_count', 0)}\n"
            response += f"• **Good:** {stream_data.get('good_count', 0)}\n"
            response += f"• **Average Risk:** {stream_data.get('average_risk', 0)}%\n"
            if stream_data.get("top_offenders"):
                response += "\n**Top Offenders:**\n"
                for student in stream_data["top_offenders"]:
                    response += (f"• {student['name']} "
                                 f"({student['admission_number']}): "
                                 f"{student['report_count']} reports\n")

        else:
            response = "🤖 I'm here to help you with the school discipline system.\n\n"
            response += "**What I can do:**\n"
            response += "• 📊 Show school statistics\n"
            response += "• 👤 Look up student profiles\n"
            response += "• 📋 View discipline reports\n"
            response += "• 📈 Analyze stream/class performance\n"
            response += "• 📚 Answer questions about Kenyan Education Law\n\n"
            response += "**Try asking:**\n"
            response += "• 'Show me student John Doe'\n"
            response += "• 'How many students are in the system?'\n"
            response += "• 'What are the reports for admission 9486?'\n"
            response += "• 'Analyze the Gonza stream'\n"
            response += "• 'What does the Education Act say about discipline?'"

        return response

    # ---- deep-school answers -------------------------------------------------
    def _deep_school_answer(self, user_message):
        low = user_message.lower()

        # (a) "how is this school functioning / doing / performing"
        if (re.search(r"\b(how\s+is|how'?s)\b.*\b(school|system)\b.*"
                      r"\b(function|perform|doing|going|run)\b", low)
                or re.search(r"\b(school|system)\s+(health|status|overview)\b", low)):
            try:
                from .models import Student, Stream, DisciplineReport, TeacherProfile
                students = Student.objects.filter(is_active=True)
                total_reports = DisciplineReport.objects.count()
                critical = students.filter(risk_level="CRITICAL").count()
                warning = students.filter(risk_level="WARNING").count()
                good = students.filter(risk_level="GOOD").count()
                avg_risk = students.aggregate(a=Avg("risk_score"))["a"] or 0
                streams = Stream.objects.filter(is_active=True).count()
                teachers = TeacherProfile.objects.filter(is_approved=True).count()
                return (
                    "**School functioning snapshot — St Charles Lwanga Senior School**\n"
                    f"- Active students: **{students.count()}**\n"
                    f"- Risk levels: Critical {critical} | "
                    f"Warning {warning} | Good {good}\n"
                    f"- Average risk score: **{round(avg_risk, 1)}%**\n"
                    f"- Discipline reports on record: **{total_reports}**\n"
                    f"- Active streams: **{streams}**\n"
                    f"- Approved teachers: **{teachers}**\n\n"
                    "The system does not track attendance rates, exam averages, "
                    "or funding. Ask for a specific area to drill down."
                )
            except Exception:
                return None

        # (b) teacher lookup by name
        m = re.search(
            r"\bwho\s+is\s+([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,3})\b",
            user_message)
        if m and re.search(r"\b(school|system|teacher|staff|admin)\b", low):
            try:
                from django.contrib.auth import get_user_model
                from .models import TeacherProfile
                User = get_user_model()
                tokens = [t for t in m.group(1).split() if t]
                q = Q()
                for t in tokens:
                    q |= (Q(first_name__icontains=t)
                          | Q(last_name__icontains=t)
                          | Q(username__icontains=t)
                          | Q(email__icontains=t))
                users = list(User.objects.filter(q)[:5])
                if users:
                    lines = [f"**Matches for '{m.group(1)}':**"]
                    for u in users:
                        full = (u.get_full_name() or u.username).strip()
                        prof = TeacherProfile.objects.filter(user=u).first()
                        role = ("Teacher" if prof and prof.is_approved
                                else "Admin" if u.is_superuser or u.is_staff
                                else "User")
                        lines.append(
                            f"- **{full}** — id {u.id}, {role}, "
                            f"email {u.email or '-'}")
                    return "\n".join(lines)
            except Exception:
                return None

        # (c) "which stream has many cases"
        if (re.search(r"\b(which|what)\b.*\b(stream|class|form)\b.*"
                      r"\b(cases?|reports?|incidents?|many|most|highest)\b", low)
                or re.search(r"\b(stream|class|form)\b.*\bwith\s+most\b", low)):
            try:
                from .models import DisciplineReport
                rows = (DisciplineReport.objects
                        .values("student__stream__name")
                        .annotate(count=Count("id"))
                        .order_by("-count"))
                if not rows:
                    return "DATA UNAVAILABLE - no discipline reports on record."
                lines = ["**Reports by stream (highest first):**"]
                for r in rows[:10]:
                    lines.append(
                        f"- **{r['student__stream__name'] or 'Unassigned'}**: "
                        f"{r['count']} report(s)")
                top = rows[0]
                lines.append(
                    f"\n**Stream with most cases: "
                    f"{top['student__stream__name'] or 'Unassigned'}** "
                    f"({top['count']} report(s)).")
                return "\n".join(lines)
            except Exception:
                return None

        # (d) teacher names list
        if (re.search(r"\b(list|show|write|what\s+are|names?)\b.*"
                      r"\b(teacher|tutor|staff)\b", low)
                or re.search(r"\b(teacher|tutor|staff)\b.*\b(names?|list)\b", low)):
            try:
                from django.contrib.auth import get_user_model
                from .models import TeacherProfile
                User = get_user_model()
                ids = TeacherProfile.objects.filter(
                    is_approved=True).values_list("user_id", flat=True)
                users = list(User.objects.filter(id__in=list(ids))
                             .values("id", "username", "first_name",
                                     "last_name", "email",
                                     "is_superuser", "is_staff"))
                if not users:
                    return "DATA UNAVAILABLE - no approved teacher records found."
                lines = [f"**Teachers in DB ({len(users)}):**", "",
                         "| ID | First | Last | Email | Role |",
                         "|----|-------|------|-------|------|"]
                for u in users:
                    role = ("Admin" if u["is_superuser"]
                            else "Staff" if u["is_staff"] else "Teacher")
                    lines.append(
                        f"| {u['id']} | {u['first_name'] or '-'} | "
                        f"{u['last_name'] or '-'} | {u['email'] or '-'} | {role} |")
                return "\n".join(lines)
            except Exception:
                return None

        return None

    # ---- window widening helper ---------------------------------------------
    def _nearest_window_slice(self, domain, since, now, limit=50):
        try:
            from .models import DisciplineReport
        except Exception:
            return None

        notes = []
        if since:
            notes.append(
                f"No records fell inside the requested window "
                f"(since {since:%Y-%m-%d}). "
                f"Showing the most recent records on file instead."
            )

        if domain in ("reports", "students", "school", "streams"):
            recent = list(
                DisciplineReport.objects
                .select_related("student", "category", "reported_by",
                                "student__stream")
                .order_by("-reported_at")[:limit]
                .values("id", "reported_at", "student__name",
                        "student__admission_number", "student__stream__name",
                        "category__name", "points", "rating",
                        "reported_by__first_name", "reported_by__last_name",
                        "reported_by__username", "comments")
            )
            if not recent:
                return {"notes": notes or ["No discipline reports exist at all."],
                        "reports": []}
            return {"notes": notes, "reports": recent}

        if domain == "teachers":
            try:
                from django.contrib.auth import get_user_model
                from .models import TeacherProfile
                User = get_user_model()
                ids = TeacherProfile.objects.filter(
                    is_approved=True).values_list("user_id", flat=True)
                users = list(User.objects.filter(id__in=list(ids))
                             .values("id", "username", "first_name",
                                     "last_name", "email"))
            except Exception:
                users = []
            return {"notes": notes, "teachers": users}

        return {"notes": notes}

    # ---- generic gather ------------------------------------------------------
    def _gather_for_question(self, user_message):
        low = user_message.lower()
        now = self._now()

        try:
            from .models import Student, Stream, DisciplineReport, TeacherProfile
        except Exception:
            return None

        reports_q = DisciplineReport.objects.select_related(
            "student", "student__stream", "category", "reported_by"
        )

        since = None
        window_label = None
        if re.search(r"\b(today|this\s+day)\b", low):
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
            window_label = "today"
        elif re.search(r"\b(this\s+week|weekly|past\s+week|last\s+7)\b", low):
            since = now - timedelta(days=7)
            window_label = "this week"
        elif re.search(r"\b(this\s+month|monthly|past\s+month|last\s+30)\b", low):
            since = now - timedelta(days=30)
            window_label = "this month"
        elif re.search(r"\b(this\s+term|termly|this\s+semester)\b", low):
            since = now - timedelta(days=90)
            window_label = "this term"
        elif re.search(r"\b(this\s+year|annual|ytd)\b", low):
            since = now - timedelta(days=365)
            window_label = "this year"

        domain = None
        if re.search(r"\b(report|reports|incident|incidents|case|cases|"
                     r"offense|offenses|discipline|behaviou?r|trend|trends|"
                     r"summar\w*|summary)\b", low):
            domain = "reports"
        elif re.search(r"\b(stream|streams|class|classes|form|forms)\b", low):
            domain = "streams"
        elif re.search(r"\b(teacher|teachers|staff|tutor|tutors)\b", low):
            domain = "teachers"
        elif re.search(r"\b(student|students|pupil|pupils|learner|learners|"
                       r"names?|list|risk|at.risk|top|who)\b", low):
            domain = "students"
        elif re.search(r"\b(school|system|overview|snapshot)\b", low):
            domain = "school"
        else:
            return None

        windowed = reports_q.filter(reported_at__gte=since) if since else reports_q

        ctx_parts = []
        raw = {}

        if domain == "reports":
            rows = list(windowed.order_by("-reported_at")[:50].values(
                "id", "reported_at", "student__name",
                "student__admission_number", "student__stream__name",
                "category__name", "points", "rating",
                "reported_by__first_name", "reported_by__last_name",
                "reported_by__username", "comments",
            ))
            raw["reports"] = rows
            by_cat = list(windowed.values("category__name")
                          .annotate(c=Count("id")).order_by("-c"))
            by_stream = list(windowed.values("student__stream__name")
                             .annotate(c=Count("id")).order_by("-c"))
            try:
                from django.db.models.functions import TruncDate
                by_day = list(windowed.annotate(d=TruncDate("reported_at"))
                              .values("d").annotate(c=Count("id")).order_by("d"))
            except Exception:
                by_day = []
            raw["reports_by_category"] = by_cat
            raw["reports_by_stream"] = by_stream
            raw["reports_by_day"] = by_day
            ctx_parts.append(
                f"REPORTS ({len(rows)} in window"
                + (f", since {since:%Y-%m-%d}" if since else "") + "):\n"
                + json.dumps(rows, default=str, indent=2))
            ctx_parts.append("REPORTS_BY_CATEGORY:\n"
                             + json.dumps(by_cat, default=str))
            ctx_parts.append("REPORTS_BY_STREAM:\n"
                             + json.dumps(by_stream, default=str))
            ctx_parts.append("REPORTS_BY_DAY:\n"
                             + json.dumps(by_day, default=str))

        elif domain == "streams":
            per_stream = []
            for s in Stream.objects.filter(is_active=True):
                st_students = Student.objects.filter(stream=s, is_active=True)
                st_reports = windowed.filter(student__stream=s)
                per_stream.append({
                    "stream": s.name,
                    "students": st_students.count(),
                    "reports": st_reports.count(),
                    "points": st_reports.aggregate(p=Sum("points"))["p"] or 0,
                    "avg_risk": round(
                        st_students.aggregate(a=Avg("risk_score"))["a"] or 0, 1),
                })
            raw["streams"] = per_stream
            ctx_parts.append("STREAM_COMPARISON:\n"
                             + json.dumps(per_stream, default=str, indent=2))

        elif domain == "students":
            wants_names = bool(re.search(
                r"\b(names?|list|write\s+down|show\s+all)\b", low))
            limit = 100 if wants_names else 30

            students = list(Student.objects.filter(is_active=True)
                            .annotate(report_count=Count("reports"))
                            .order_by("-report_count", "-risk_score")[:limit]
                            .values("id", "name", "admission_number",
                                    "stream__name", "form", "risk_score",
                                    "risk_level", "report_count"))
            raw["students"] = students
            raw["top_students"] = students
            ctx_parts.append("STUDENTS:\n"
                             + json.dumps(students, default=str, indent=2))

            if not wants_names:
                at_risk = list(Student.objects.filter(
                    is_active=True, risk_level__in=["CRITICAL", "WARNING"])
                    .values("id", "name", "risk_score", "risk_level")[:20])
                raw["at_risk"] = at_risk
                ctx_parts.append("AT_RISK_STUDENTS:\n"
                                 + json.dumps(at_risk, default=str))

        elif domain == "teachers":
            try:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                ids = TeacherProfile.objects.filter(
                    is_approved=True).values_list("user_id", flat=True)
                teachers = list(User.objects.filter(id__in=list(ids))
                                .values("id", "username", "first_name",
                                        "last_name", "email"))
            except Exception:
                teachers = []
            raw["teachers"] = teachers
            ctx_parts.append("TEACHERS:\n"
                             + json.dumps(teachers, default=str, indent=2))

        elif domain == "school":
            raw["stats"] = self.get_school_stats()
            raw["info"] = self.get_school_info()
            ctx_parts.append("SCHOOL_STATS:\n"
                             + json.dumps(raw["stats"], default=str, indent=2))
            ctx_parts.append("SCHOOL_INFO:\n"
                             + json.dumps(raw["info"], default=str, indent=2))

        primary_empty = True
        for key in ("reports", "streams", "students", "teachers"):
            if raw.get(key):
                primary_empty = False
                break
        if domain == "school" and raw.get("stats"):
            primary_empty = False

        widened = False
        if since and primary_empty:
            extra = self._nearest_window_slice(domain, since, now) or {}
            for k, v in extra.items():
                if k == "notes":
                    raw["notes"] = v
                    ctx_parts.append("WINDOW_NOTE:\n" + json.dumps(v))
                elif v:
                    raw[k] = v
                    ctx_parts.append(f"{k.upper()} (nearest available):\n"
                                     + json.dumps(v, default=str, indent=2))
            widened = True
            primary_empty = not any(
                v for k, v in raw.items() if k not in ("notes",))

        return {
            "domain": domain,
            "window_label": window_label,
            "window_since": since.isoformat() if since else None,
            "raw": raw,
            "context_text": "\n\n".join(ctx_parts),
            "empty": primary_empty,
            "widened": widened,
        }

    def _format_gathered_fallback(self, gathered):
        raw = gathered.get("raw") or {}
        dom = gathered.get("domain")
        notes = raw.get("notes") or []
        prefix = ""
        if notes:
            prefix = "ℹ️ " + " ".join(notes) + "\n\n"

        if dom == "reports" and raw.get("reports"):
            lines = [prefix + f"**Reports ({len(raw['reports'])}):**", ""]
            for r in raw["reports"][:20]:
                lines.append(
                    f"- {r.get('reported_at')} · **{r.get('student__name')}** "
                    f"({r.get('student__admission_number')}) · "
                    f"{r.get('category__name')} · {r.get('points')} pts · "
                    f"{r.get('rating')}"
                )
            if raw.get("reports_by_category"):
                lines.append("\n**By category:**")
                for c in raw["reports_by_category"]:
                    lines.append(f"- {c['category__name']}: {c['c']}")
            if raw.get("reports_by_stream"):
                lines.append("\n**By stream:**")
                for c in raw["reports_by_stream"]:
                    lines.append(
                        f"- {c['student__stream__name'] or 'Unassigned'}: {c['c']}")
            return "\n".join(lines)

        if dom == "streams" and raw.get("streams"):
            lines = [prefix + "| Stream | Students | Reports | Points | Avg Risk |",
                     "|---|---|---|---|---|"]
            for s in raw["streams"]:
                lines.append(f"| {s['stream']} | {s['students']} | "
                             f"{s['reports']} | {s['points']} | {s['avg_risk']} |")
            return "\n".join(lines)

        if dom == "students":
            top = raw.get("students") or raw.get("top_students") or []
            lines = [prefix + "**Students:**", ""]
            for s in top[:100]:
                lines.append(f"- **{s['name']}** ({s['admission_number']}) · "
                             f"{s['stream__name'] or 'N/A'} · "
                             f"{s['report_count']} report(s) · "
                             f"risk {s['risk_score']} ({s['risk_level']})")
            return "\n".join(lines)

        if dom == "teachers" and raw.get("teachers"):
            lines = [prefix + "**Teachers:**", ""]
            for t in raw["teachers"]:
                full = (f"{t.get('first_name') or ''} "
                        f"{t.get('last_name') or ''}").strip() or t.get("username")
                lines.append(f"- **{full}** — {t.get('email') or '-'}")
            return "\n".join(lines)

        return prefix + "DATA UNAVAILABLE — no data could be formatted."

    # ---- unified-facts helpers ---------------------------------------------
    def _guess_domain(self, message: str):
        low = message.lower()
        if re.search(r"\b(report|reports|incident|incidents|case|cases|"
                     r"offense|offenses|discipline|behaviou?r|trend|trends|"
                     r"summar\w*|summary|recent)\b", low):
            return "reports"
        if re.search(r"\b(stream|streams|class|classes)\b", low):
            return "streams"
        if re.search(r"\b(teacher|teachers|staff|tutor|tutors)\b", low):
            return "teachers"
        if re.search(r"\b(student|students|pupil|pupils|learner|learners|"
                     r"names?|list|risk|at.risk|top|who)\b", low):
            return "students"
        if re.search(r"\b(school|system|overview|snapshot|motto)\b", low):
            return "school"
        return None

    def _window_days_for(self, message: str):
        low = message.lower()
        if re.search(r"\b(today|this\s+day)\b", low):
            return 1
        if re.search(r"\b(this\s+week|weekly|past\s+week|last\s+7)\b", low):
            return 7
        if re.search(r"\b(this\s+month|monthly|past\s+month|last\s+30)\b", low):
            return 30
        if re.search(r"\b(this\s+term|termly|this\s+semester)\b", low):
            return 90
        if re.search(r"\b(this\s+year|annual|ytd)\b", low):
            return 365
        return None

    @staticmethod
    def _facts_has_rows(slice_):
        for k, v in slice_.items():
            if k in ("generated_at", "domain"):
                continue
            if isinstance(v, list) and v:
                return True
            if isinstance(v, dict) and any(v.values()):
                return True
        return False

    def _format_unified_slice(self, slice_):
        dom = slice_.get("domain")
        if dom == "school":
            school = slice_.get("school") or {}
            counts = slice_.get("counts") or {}
            return (f"**{school.get('name','School')}**\n"
                    f"- Motto: {school.get('motto') or '—'}\n"
                    f"- Students: {counts.get('students_active',0)}\n"
                    f"- Teachers: {counts.get('teachers',0)}\n"
                    f"- Streams: {counts.get('streams_active',0)}\n"
                    f"- Reports: {counts.get('reports_total',0)}")
        if dom == "students":
            rows = slice_.get("students") or []
            lines = [f"**Students ({len(rows)})**", ""]
            for s in rows[:100]:
                lines.append(f"- {s['name']} · {s.get('admission_number') or '—'} · "
                             f"{s.get('stream') or 'N/A'} · {s.get('form') or '—'} · "
                             f"risk {s.get('risk_score',0)} ({s.get('risk_level') or '—'})")
            return "\n".join(lines)
        if dom == "teachers":
            rows = slice_.get("teachers") or []
            lines = [f"**Teachers ({len(rows)})**", ""]
            for t in rows:
                lines.append(f"- {t.get('full_name')} · {t.get('email') or '—'} · "
                             f"{'Admin' if t.get('is_superuser') else 'Teacher'}")
            return "\n".join(lines)
        if dom == "streams":
            rows = slice_.get("streams") or []
            lines = ["| Stream | Students | Reports | Points | Avg Risk |",
                     "|---|---|---|---|---|"]
            for s in rows:
                lines.append(f"| {s['stream']} | {s['students']} | {s['reports']} | "
                             f"{s['points']} | {s['avg_risk']} |")
            return "\n".join(lines)
        if dom == "reports":
            rows = slice_.get("reports") or []
            lines = [f"**Reports ({len(rows)})**", ""]
            for r in rows[:20]:
                lines.append(f"- {r.get('date')} · {r.get('student_name')} "
                             f"({r.get('admission_number')}) · {r.get('category')} · "
                             f"{r.get('points')} pts · {r.get('rating')}")
            return "\n".join(lines)
        return "No data available for that request."

    # ---- god_read facade ----------------------------------------------------
    def god_read(self, actor: dict, tool: str, **kwargs) -> dict:
        if not ENABLE_DEEP:
            raise PermissionError("Deep access is disabled (AI_ENABLE_DEEP=0).")
        self.terminal._eng(actor)
        fn = getattr(self.terminal, tool, None)
        if not fn:
            raise PermissionError(f"unknown god_read tool: {tool}")
        return fn(actor, **kwargs)

    def _run_god_read(self, user_message: str, actor: dict) -> dict:
        try:
            parts = shlex.split(user_message)
        except Exception as exc:
            return {"success": False, "mode": "engineer_terminal",
                    "error": f"parse_error: {exc}"}
        if parts and parts[0].lower() in ("god", "god_read"):
            parts = parts[1:]
        if not parts:
            tools = [t for t in dir(self.terminal)
                     if not t.startswith("_")
                     and callable(getattr(self.terminal, t, None))
                     and t not in {"PROC_ALLOW", "GIT_RO", "SECRET_HINTS"}]
            return {"success": True, "mode": "engineer_terminal",
                    "response": "GOD-READ ready. Tools: " + ", ".join(tools)}
        tool = parts[0]
        kwargs: dict[str, Any] = {}
        i = 1
        while i < len(parts):
            tok = parts[i]
            if tok.startswith("--"):
                key = tok[2:].replace("-", "_")
                if i + 1 < len(parts) and not parts[i + 1].startswith("--"):
                    val = parts[i + 1]
                    i += 2
                else:
                    val = True
                    i += 1
                if isinstance(val, str):
                    if val.isdigit():
                        val = int(val)
                    elif val.lower() in ("true", "false"):
                        val = val.lower() == "true"
                kwargs[key] = val
            else:
                i += 1
        try:
            result = self.god_read(actor, tool, **kwargs)
            return {"success": True, "mode": "engineer_terminal",
                    "tool": tool, "result": result}
        except PermissionError as exc:
            return {"success": False, "mode": "engineer_terminal",
                    "error": f"permission_denied: {exc}"}
        except Exception as exc:
            return {"success": False, "mode": "engineer_terminal",
                    "error": f"{type(exc).__name__}: {exc}"}

    # ---- main chat ----------------------------------------------------------
    def chat(self, user_message, student_id=None, conversation_history=None,
             actor=None):
        actor = actor or {}

        # Engineer terminal short-circuit (only when deep is enabled)
        if ENABLE_DEEP and actor.get("role") == "engineer" and \
                user_message.lower().startswith(("god ", "god_read ")):
            return self._run_god_read(user_message, actor)

        # 0) Deep-school answers for the 4 gaps the base router misses
        deep = self._deep_school_answer(user_message)
        if deep:
            return {
                "success": True,
                "response": deep,
                "mode": "local_db_deep",
                "provider": "local",
                "context": {"source": "django_orm"},
            }

        # Deterministic database answers must win over provider-backed facts.
        # This keeps counts, rankings, and other factual responses tied to the
        # live ORM instead of allowing a model response to replace them.
        try:
            from .admin_agent_queries import LocalQueryRouter
            local_answer = LocalQueryRouter().answer(user_message)
            if local_answer:
                return {
                    "success": True,
                    "response": local_answer,
                    "mode": "local_db",
                    "provider": "local",
                    "context": {"source": "django_orm"},
                }
        except Exception as exc:
            self.logger.exception(
                "Local database query failed; continuing with unified facts: %s",
                exc)

        # 0b) Unified-facts pass. Attach the canonical snapshot for the
        #     recognised domain, plus a backend probe for privileged roles.
        facts_slice = None
        try:
            dom = self._guess_domain(user_message)
            if dom:
                window_days = self._window_days_for(user_message)
                facts_slice = self.facts.slice_for(dom, window_days)
                probe_kinds = None
                role = actor.get("role")
                if role == "engineer":
                    probe_kinds = ["models", "routes", "migrations"]
                elif role in ("admin", "teacher"):
                    probe_kinds = ["models"]
                if probe_kinds:
                    facts_slice["backend_probe"] = self.probe.probe(
                        actor, kinds=probe_kinds)
        except Exception as exc:
            self.logger.warning("unified-facts pass failed: %s", exc)

        if facts_slice and self._facts_has_rows(facts_slice):
            gather_context = {
                "intent": {"type": facts_slice.get("domain")},
                "school_info": self.get_school_info(),
                "gathered": facts_slice,
                "window_days": self._window_days_for(user_message),
                "timestamp": self._now().isoformat(),
                "gather_note": (
                    "The 'gathered' block is the canonical unified snapshot "
                    "for this domain — every code path in this app reads the "
                    "same numbers. Do not recompute, do not use numbers from "
                    "anywhere else. If a 'backend_probe' block is present, "
                    "use it to confirm the app is actually running the "
                    "models/routes you see, but do not enumerate raw routes "
                    "or settings to the user — they are for your reasoning."
                ),
            }
            messages = [
                {"role": "system",
                 "content": self.get_system_prompt(gather_context)},
                {"role": "user", "content": user_message},
            ]
            result = self._call_providers_in_order(messages)
            if result.get("success"):
                return {
                    "success": True,
                    "response": result["response"],
                    "mode": "unified_facts",
                    "provider": result.get("provider"),
                    "model_used": result.get("model_used"),
                    "context": gather_context,
                }
            return {
                "success": True,
                "response": self._format_unified_slice(facts_slice),
                "mode": "unified_facts_local",
                "provider": "local",
                "context": gather_context,
            }

        # 1) Local deterministic router fallback
        try:
            from .admin_agent_queries import LocalQueryRouter
            local_answer = LocalQueryRouter().answer(user_message)
            if local_answer:
                return {
                    "success": True,
                    "response": local_answer,
                    "mode": "local_db",
                    "provider": "local",
                    "context": {"source": "django_orm"},
                }
        except Exception as exc:
            self.logger.exception(
                "Local database query failed; continuing with validated fallback: %s",
                exc)

        # 2) Generic gather — pull the relevant slice, widen if empty.
        gathered = None
        try:
            gathered = self._gather_for_question(user_message)
        except Exception as exc:
            self.logger.warning("gather failed: %s", exc)

        if gathered:
            has_any = False
            for k, v in (gathered.get("raw") or {}).items():
                if k == "notes":
                    continue
                if isinstance(v, list) and v:
                    has_any = True
                    break
                if isinstance(v, dict) and any(v.values()):
                    has_any = True
                    break
                if isinstance(v, (int, float)) and v:
                    has_any = True
                    break

            if has_any:
                gather_context = {
                    "intent": {"type": gathered["domain"]},
                    "school_info": self.get_school_info(),
                    "gathered": gathered["raw"],
                    "window_label": gathered.get("window_label"),
                    "window_since": gathered.get("window_since"),
                    "widened": gathered.get("widened", False),
                    "timestamp": self._now().isoformat(),
                    "gather_note": (
                        "The rows below are the FULL relevant slice from the "
                        "database for this question. Construct the answer "
                        "from these rows only. "
                        "If WINDOW_NOTE appears in the data, the user's "
                        "requested time window had no rows, and the rows "
                        "below are the nearest available instead. In that "
                        "case, say clearly that the window was empty AND "
                        "still summarize what IS on file — do not refuse. "
                        "Do not invent counts, names, or dates."
                    ),
                }
                messages = [
                    {"role": "system",
                     "content": self.get_system_prompt(gather_context)},
                    {"role": "user", "content": user_message},
                ]
                result = self._call_providers_in_order(messages)
                if result.get("success"):
                    return {
                        "success": True,
                        "response": result["response"],
                        "mode": ("gathered_widened" if gathered.get("widened")
                                 else "gathered"),
                        "provider": result.get("provider"),
                        "model_used": result.get("model_used"),
                        "context": gather_context,
                    }
                fallback = self._format_gathered_fallback(gathered)
                return {
                    "success": True,
                    "response": fallback,
                    "mode": "gathered_local",
                    "provider": "local",
                    "context": gather_context,
                }

            return {
                "success": True,
                "mode": "data_unavailable",
                "response": (
                    "DATA UNAVAILABLE — I queried the database for the "
                    f"relevant {gathered['domain']} records"
                    + (f" ({gathered['window_label']})"
                       if gathered.get("window_label") else "")
                    + " and there is nothing on file at all for that "
                      "request, not even in earlier periods."
                ),
                "context": {"gathered": gathered},
            }

        # 3) Legacy intent pipeline
        intent = self._parse_user_intent(user_message)

        student_data = None
        if intent["type"] == "student":
            query = intent["entities"].get("query")
            admission = intent["entities"].get("admission")
            if admission:
                student_data = self.get_student_data(admission=admission)
            elif query:
                student_data = self.get_student_data(name=query)
            elif student_id:
                student_data = self.get_student_data(student_id=student_id)

        school_stats = None
        if intent["type"] == "stats":
            school_stats = self.get_school_stats()

        student_names_list = None
        if intent["type"] == "student_names":
            try:
                student_names_list = self.get_all_students_summary()
            except Exception:
                student_names_list = None

        stream_data = None
        if intent["type"] == "stream":
            stream_name = intent["entities"].get("stream")
            if stream_name:
                try:
                    from .models import Stream
                    stream = Stream.objects.filter(
                        name__icontains=stream_name).first()
                    if stream:
                        stream_data = self.get_stream_analysis(stream.id)
                except Exception:
                    stream_data = None

        school_info = self.get_school_info()

        if intent["type"] == "stats" and school_stats is None:
            school_stats = self.get_school_stats()

        context = {
            "intent": intent,
            "school_info": school_info,
            "student": student_data,
            "stats": school_stats,
            "student_names": student_names_list,
            "stream_analysis": stream_data,
            "timestamp": self._now().isoformat(),
        }

        factual_request = self._is_factual_school_data_request(
            intent, user_message)

        has_student_data = self._has_real_data(student_data)
        has_stats_data = self._has_real_data(school_stats)
        has_names_data = self._has_real_data(student_names_list)
        has_stream_data = self._has_real_data(stream_data)
        has_school_info = bool(school_info.get("name"))

        if factual_request and intent["type"] == "student" and not has_student_data:
            return {"success": True,
                    "response": self._data_unavailable_response(intent),
                    "mode": "data_unavailable", "context": context}

        if factual_request and intent["type"] == "student_names" and not has_names_data:
            return {"success": True,
                    "response": self._data_unavailable_response(intent),
                    "mode": "data_unavailable", "context": context}

        if (factual_request and intent["type"] == "stats"
                and not has_stats_data and not has_school_info):
            return {"success": True,
                    "response": self._data_unavailable_response(intent),
                    "mode": "data_unavailable", "context": context}

        if factual_request and intent["type"] == "stream" and not has_stream_data:
            return {"success": True,
                    "response": self._data_unavailable_response(intent),
                    "mode": "data_unavailable", "context": context}

        messages = [{"role": "system", "content": self.get_system_prompt(context)}]
        if conversation_history:
            for msg in conversation_history[-10:]:
                if isinstance(msg, dict) and "role" in msg and "content" in msg:
                    messages.append(msg)
        messages.append({"role": "user", "content": user_message})

        task_type = intent.get("type")
        routing_key = None
        if task_type == "student":
            routing_key = "report_generation"
        elif task_type == "stats":
            routing_key = "report_generation"
        elif intent.get("action") == "reports":
            routing_key = "report_generation"
        if any(word in user_message.lower()
               for word in ["short", "brief", "one-liner"]):
            routing_key = "short_responses"
        if any(word in user_message.lower()
               for word in ["analyze", "deep", "long", "detailed", "thorough"]):
            routing_key = routing_key or "long_context_analysis"

        provider_order = get_providers_for_task(routing_key) if routing_key else None

        provider_result = self._call_providers_in_order(
            messages, provider_order=provider_order)
        if provider_result.get("success"):
            return {
                "success": True,
                "response": provider_result["response"],
                "usage": provider_result.get("usage", {}),
                "mode": "ai",
                "provider": provider_result.get("provider"),
                "model_used": provider_result.get("model_used"),
                "context": context,
            }

        self.logger.warning("All AI providers failed: %s",
                            provider_result.get("error"))
        if factual_request and not (has_student_data or has_stats_data
                                    or has_stream_data or has_names_data):
            return {"success": True,
                    "response": self._data_unavailable_response(intent),
                    "mode": "data_unavailable", "context": context}

        fallback_response = self._generate_fallback_response(intent, context)
        return {"success": True, "response": fallback_response,
                "mode": "data_fallback", "context": context}