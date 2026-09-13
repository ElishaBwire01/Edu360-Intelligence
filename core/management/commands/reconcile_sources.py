import json

from django.core.management.base import BaseCommand, CommandError

from core.integration.reconciliation.runner import run_sqlite_reconciliation


class Command(BaseCommand):
    help = "Generate a read-only EduGrade/Discipline reconciliation report."

    def add_arguments(self, parser):
        parser.add_argument("--edugrade-db", required=True)
        parser.add_argument("--discipline-db", required=True)
        parser.add_argument("--edugrade-school-key")
        parser.add_argument("--discipline-school-key")
        parser.add_argument("--output")

    def handle(self, *args, **options):
        try:
            result = run_sqlite_reconciliation(
                edugrade_database=options["edugrade_db"],
                discipline_database=options["discipline_db"],
                edugrade_school_key=options.get("edugrade_school_key"),
                discipline_school_key=options.get("discipline_school_key"),
            )
        except (FileNotFoundError, ValueError, OSError) as error:
            raise CommandError(str(error)) from error

        rendered = json.dumps(result, indent=2, default=str)
        output = options.get("output")
        if output:
            with open(output, "w", encoding="utf-8", newline="\n") as report_file:
                report_file.write(rendered)
            self.stdout.write(f"Read-only reconciliation report written to {output}")
        else:
            self.stdout.write(rendered)