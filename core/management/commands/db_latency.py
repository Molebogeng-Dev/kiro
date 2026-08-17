"""Measure round-trip latency to the configured database.

A quick diagnostic for "is Supabase just slow right now, or is something
actually broken?" — useful given how much this project's session logs have
been shaped by a flaky pooler connection. Runs a trivial `SELECT NOW()` and
times the full round trip, optionally several times to see how much it varies.

Usage:
    python manage.py db_latency
    python manage.py db_latency --count 5
    python manage.py db_latency --count 5 --json
"""

import json
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = "Report round-trip latency to the database with a trivial query."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=1,
            help="Number of round trips to time (default: 1).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Print machine-readable JSON instead of plain text.",
        )

    def handle(self, *args, **options):
        count = options["count"]
        if count < 1:
            raise CommandError("--count must be at least 1.")

        samples = []
        db_time = None

        for _ in range(count):
            start = time.perf_counter()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT NOW()")
                    db_time = cursor.fetchone()[0]
            except OperationalError as exc:
                raise CommandError(f"Could not reach the database: {exc}") from exc
            samples.append((time.perf_counter() - start) * 1000)

        engine = connection.settings_dict.get("ENGINE", "")
        host = connection.settings_dict.get("HOST") or "(local)"

        if options["as_json"]:
            self.stdout.write(
                json.dumps(
                    {
                        "engine": engine,
                        "host": host,
                        "db_time": str(db_time),
                        "samples_ms": [round(s, 2) for s in samples],
                        "min_ms": round(min(samples), 2),
                        "max_ms": round(max(samples), 2),
                        "avg_ms": round(sum(samples) / len(samples), 2),
                    }
                )
            )
            return

        self.stdout.write(f"Database engine: {engine}")
        self.stdout.write(f"Host: {host}")
        self.stdout.write(f"Server time: {db_time}")
        self.stdout.write("")

        if count == 1:
            self.stdout.write(self.style.SUCCESS(f"Round-trip latency: {samples[0]:.2f} ms"))
        else:
            for index, sample in enumerate(samples, start=1):
                self.stdout.write(f"  [{index}/{count}] {sample:.2f} ms")
            self.stdout.write("")
            self.stdout.write(
                self.style.SUCCESS(
                    f"min {min(samples):.2f} ms  /  "
                    f"avg {sum(samples) / len(samples):.2f} ms  /  "
                    f"max {max(samples):.2f} ms"
                )
            )
