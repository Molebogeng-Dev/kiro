"""Memorandum subject normalization and backfill (Sprint 7).

The subject is what the progress dashboard groups marks by, so "maths",
"Maths ", and "MATHS" have to land in one bucket, and a memo saved without a
subject must not form a nameless group of its own. Both the ongoing
normalization (on save) and the one-off backfill (the migration) run through the
same ``normalize_subject`` function, so they cannot drift apart — this tests that
function directly, the save path, and the migration's data operation.
"""

import importlib

from django.apps import apps as global_apps
from django.test import TestCase

from marking.models import DEFAULT_SUBJECT, Memorandum, normalize_subject


class NormalizeSubjectTests(TestCase):
    """The pure function every path shares."""

    def test_whitespace_is_stripped_and_words_title_cased(self):
        self.assertEqual(normalize_subject("  life orientation "), "Life Orientation")

    def test_case_variants_collapse_to_one_form(self):
        self.assertEqual(normalize_subject("maths"), "Maths")
        self.assertEqual(normalize_subject("MATHS"), "Maths")
        self.assertEqual(normalize_subject("Maths "), "Maths")

    def test_blank_becomes_the_default(self):
        self.assertEqual(normalize_subject(""), DEFAULT_SUBJECT)
        self.assertEqual(normalize_subject("   "), DEFAULT_SUBJECT)
        self.assertEqual(normalize_subject(None), DEFAULT_SUBJECT)


class MemorandumSaveTests(TestCase):
    """Saving a memorandum normalizes its subject, whatever the path in."""

    def test_a_messy_subject_is_tidied_on_save(self):
        memo = Memorandum.objects.create(
            title="Term 3 test", subject="  physical sciences ", content="Q1 (2)."
        )
        self.assertEqual(memo.subject, "Physical Sciences")

    def test_a_blank_subject_files_under_the_default(self):
        memo = Memorandum.objects.create(
            title="No subject given", subject="", content="Q1 (2)."
        )
        self.assertEqual(memo.subject, DEFAULT_SUBJECT)

    def test_two_spellings_group_as_one_subject(self):
        first = Memorandum.objects.create(title="A", subject="maths", content="Q1.")
        second = Memorandum.objects.create(title="B", subject="Maths ", content="Q2.")
        self.assertEqual(first.subject, second.subject)


class BackfillMigrationTests(TestCase):
    """The 0004 data migration normalizes rows written before this sprint."""

    def _backfill(self):
        migration = importlib.import_module(
            "marking.migrations.0004_alter_memorandum_subject"
        )
        migration.backfill_subjects(global_apps, None)

    def test_blank_and_messy_existing_subjects_are_normalized(self):
        memo = Memorandum.objects.create(title="Old", subject="History", content="Q.")

        # Force pre-normalization values straight into the row, bypassing save(),
        # as if written before this sprint existed.
        Memorandum.objects.filter(pk=memo.pk).update(subject="   ")
        other = Memorandum.objects.create(title="Old 2", subject="Geo", content="Q.")
        Memorandum.objects.filter(pk=other.pk).update(subject="natural sciences")

        self._backfill()

        memo.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(memo.subject, DEFAULT_SUBJECT)
        self.assertEqual(other.subject, "Natural Sciences")
