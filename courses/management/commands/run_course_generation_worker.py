"""Background worker for asynchronous course generation.

Run alongside the web server:

    python manage.py runserver
    python manage.py run_course_generation_worker

This is a separate process on purpose. A thread spawned inside the web process
dies on every dev-server reload, worker recycle, or deploy -- and a course left
in ``processing`` permanently consumes one of the learner's active-course
slots. A durable job row plus an external worker survives all of that.

The command coordinates only. All generation logic lives in
``courses.course_generation_service``.
"""
import logging
import os
import socket
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from courses.course_generation_queue import (
    claim_next_job,
    recover_stale_jobs,
)
from courses.course_generation_service import (
    GenerationOutcome,
    execute_course_generation,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process queued course generation jobs (Course Forge background worker)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process at most one job, then exit. Useful for tests and demos.",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=2.0,
            help="Seconds to wait between polls when the queue is empty.",
        )

    def handle(self, *args, **options):
        run_once = options["once"]
        interval = options["interval"]
        worker_id = f"{socket.gethostname()}:{os.getpid()}"

        self.stdout.write(self.style.SUCCESS(
            f"Course generation worker started (id={worker_id})."
        ))

        # Anything left "running" by a dead worker is reclaimed before we begin,
        # so a stranded course does not hold an active-course slot indefinitely.
        try:
            recovered = recover_stale_jobs()
            if recovered:
                self.stdout.write(self.style.WARNING(
                    f"Recovered {recovered} interrupted job(s)."
                ))
        except Exception:
            logger.exception("Stale job recovery failed; continuing.")

        while True:
            close_old_connections()

            job = None
            try:
                job = claim_next_job(worker_id)
            except Exception:
                logger.exception("Failed to claim a job; retrying.")

            if job is None:
                if run_once:
                    self.stdout.write("No queued jobs.")
                    return
                time.sleep(interval)
                continue

            self.stdout.write(f"Processing job {job.attempt_id} (course {job.course_id})...")

            try:
                outcome = execute_course_generation(job.pk)
            except Exception:
                # execute_course_generation records its own failures; anything
                # reaching here is a bug in the worker itself.
                logger.exception("Unhandled error executing job %s", job.pk)
                outcome = GenerationOutcome.FAILED

            if outcome == GenerationOutcome.SUCCEEDED:
                self.stdout.write(self.style.SUCCESS(
                    f"Job {job.attempt_id} succeeded."
                ))
            elif outcome == GenerationOutcome.FAILED:
                self.stdout.write(self.style.ERROR(
                    f"Job {job.attempt_id} failed."
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    f"Job {job.attempt_id} skipped."
                ))

            if run_once:
                return