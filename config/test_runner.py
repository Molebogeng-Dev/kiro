"""A test runner that reports only the tests that did not pass.

Django's default runner prints a dot per test and dumps tracebacks at the end.
For the per-sprint commands in ``run.sh`` we want the opposite emphasis: stay
silent about the (many) passing tests, and name every test that fails, errors,
or is skipped — the way pytest's short summary does — while keeping the full
tracebacks underneath.

Used via::

    manage.py test --testrunner config.test_runner.ConciseTestRunner

It changes only how results are printed. Discovery, the database, fixtures, and
everything else are Django's normal behaviour.

Note: this is meant for serial runs (the sprint commands do not pass
``--parallel``). Under ``--parallel`` Django ships results back from worker
processes through its own result type, and this per-test printing would not
apply.
"""

import unittest

from django.test.runner import DiscoverRunner


class ConciseTestResult(unittest.TextTestResult):
    """Print a labelled line only for tests that need attention.

    A passing run produces no per-test output at all; only the trailing
    ``Ran N tests`` / ``OK`` line from the runner remains. Anything that is not
    a plain success is named, and failures and errors still get their full
    traceback from ``printErrors()``.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Silence the per-test "ok"/dot chatter. printErrors() still runs at the
        # end, so tracebacks for failures and errors are not lost.
        self.showAll = False
        self.dots = False

    def _report(self, label, test, extra=""):
        self.stream.writeln(f"{label:<9} {test.id()}{extra}")
        self.stream.flush()

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._report("FAIL", test)

    def addError(self, test, err):
        super().addError(test, err)
        self._report("ERROR", test)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._report("SKIP", test, f"  ({reason})")

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._report("XFAIL", test)

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._report("XPASS", test)


class ConciseTestRunner(DiscoverRunner):
    def get_resultclass(self):
        # Respect --pdb and --debug-sql, which need their own result classes;
        # otherwise use the concise one.
        return super().get_resultclass() or ConciseTestResult
