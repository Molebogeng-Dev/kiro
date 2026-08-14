"""Create the Supabase Storage bucket for papers if it does not exist.

So that setting up a fresh clone is a command rather than a walkthrough of the
Supabase dashboard.
"""

from django.core.management.base import BaseCommand, CommandError

from core.storage import SupabaseStorageError, papers_storage


class Command(BaseCommand):
    help = "Create the Supabase Storage bucket for uploaded papers, if missing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--public",
            action="store_true",
            help=(
                "Create the bucket as public. Not recommended: papers are "
                "children's schoolwork, and a public bucket serves every "
                "object to anyone who can guess its path."
            ),
        )

    def handle(self, *args, **options):
        storage = papers_storage()

        if not hasattr(storage, "ensure_bucket"):
            raise CommandError(
                f"The 'papers' storage backend is {type(storage).__name__}, which "
                "does not manage buckets. This command is only meaningful with "
                "SupabaseStorage."
            )

        try:
            created = storage.ensure_bucket(public=options["public"])
        except SupabaseStorageError as exc:
            raise CommandError(str(exc)) from exc

        if created:
            visibility = "public" if options["public"] else "private"
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created {visibility} bucket {storage.bucket!r}."
                )
            )
        else:
            self.stdout.write(f"Bucket {storage.bucket!r} already exists.")
