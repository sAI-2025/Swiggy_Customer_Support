"""
Deletes LangGraph checkpoint threads inactive for more than 7 days.

Run manually:
    python manage.py cleanup_expired_checkpoints
    python manage.py cleanup_expired_checkpoints --dry-run

Schedule it (pick one):
    Windows Task Scheduler -> daily trigger ->
        program: <path to venv>\\Scripts\\python.exe
        arguments: manage.py cleanup_expired_checkpoints
        start in: C:\\Users\\chskc\\Desktop\\Swiggy

    Linux/macOS cron (daily at 3 AM):
        0 3 * * * /path/to/venv/bin/python /path/to/Swiggy/manage.py cleanup_expired_checkpoints
"""
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand

from swiggy.agent_bridge import get_app, get_activity_connection

EXPIRY_DAYS = 7


class Command(BaseCommand):
    help = "Deletes LangGraph checkpoint threads inactive for more than 7 days."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List threads that would be deleted without deleting them.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        app = get_app()
        conn = get_activity_connection()

        cutoff = datetime.now(timezone.utc) - timedelta(days=EXPIRY_DAYS)
        cutoff_iso = cutoff.isoformat()

        rows = conn.execute(
            "SELECT thread_id, last_active_at FROM thread_activity WHERE last_active_at < ?",
            (cutoff_iso,),
        ).fetchall()

        if not rows:
            self.stdout.write(self.style.SUCCESS("No expired conversation threads found."))
            return

        self.stdout.write(f"Found {len(rows)} thread(s) older than {EXPIRY_DAYS} days.")

        for thread_id, last_active_at in rows:
            if dry_run:
                self.stdout.write(f"  [DRY RUN] Would delete: {thread_id} (last active {last_active_at})")
                continue

            app.checkpointer.delete_thread(thread_id)
            conn.execute("DELETE FROM thread_activity WHERE thread_id = ?", (thread_id,))
            conn.commit()
            self.stdout.write(self.style.WARNING(f"  Deleted: {thread_id} (last active {last_active_at})"))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"Cleanup complete. {len(rows)} thread(s) removed."))
