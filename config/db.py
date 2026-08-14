"""Turn a Postgres connection URL into Django's ``DATABASES`` configuration.

This is hand-rolled rather than pulled in from ``dj-database-url`` for two
reasons:

1. It keeps the dependency list to what is already installed.
2. It is forgiving about the exact string people paste out of the Supabase
   dashboard. Supabase hands you a template like
   ``postgresql://postgres:[YOUR-PASSWORD]@host:5432/postgres``, and it is very
   easy to leave the square brackets in place, or to paste a password
   containing characters such as ``@`` or ``#`` without URL-encoding them.
   Both of those break a strict URL parser in ways that look like a
   credentials problem rather than a formatting problem.
"""

import re
from urllib.parse import parse_qsl, unquote

VALID_SCHEMES = {"postgres", "postgresql", "postgresql+psycopg2"}

# libpq connection keywords are identifiers: sslmode, application_name, and so on.
LIBPQ_KEYWORD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

DEFAULT_ENGINE = "django.db.backends.postgresql"
DEFAULT_NAME = "postgres"
DEFAULT_PORT = "5432"

# Supabase requires TLS. Overridable with ?sslmode=... in the URL for anyone
# running a plain local Postgres instead.
DEFAULT_SSLMODE = "require"

# Fail fast on an unreachable host instead of hanging a request or a migration.
DEFAULT_CONNECT_TIMEOUT = 10


class DatabaseURLError(ValueError):
    """Raised when DATABASE_URL cannot be understood."""


def _clean_password(raw: str) -> str:
    """Strip Supabase's placeholder brackets, then percent-decode."""
    if len(raw) > 1 and raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return unquote(raw)


def _split_credentials(remainder: str) -> tuple[str, str]:
    """Split ``user:password@host/name?query`` at the right ``@``.

    Which ``@`` separates the credentials from the host is genuinely ambiguous
    when the password is not percent-encoded, because ``@`` is legal in both a
    password and a query parameter value. The rule below resolves every
    realistic combination:

    * If any ``@`` appears before the first ``?``, the authority is everything
      up to the *last* such ``@``. This keeps ``pa@ss`` intact as a password
      and stops an ``@`` inside a query value from stealing the split.
    * Otherwise the only ``@`` characters sit after the first ``?``, which
      means that ``?`` is part of the password rather than the start of the
      query string, so fall back to the last ``@`` in the whole string.
    """
    first_question = remainder.find("?")

    if first_question != -1 and "@" in remainder[:first_question]:
        boundary = remainder.rindex("@", 0, first_question)
    else:
        boundary = remainder.rfind("@")

    if boundary == -1:
        raise DatabaseURLError(
            "DATABASE_URL is missing credentials (expected user:password@host)."
        )

    return remainder[:boundary], remainder[boundary + 1 :]


def parse_database_url(url: str, conn_max_age: int = 0) -> dict:
    """Parse ``url`` into a single Django database configuration dict."""
    if not url:
        raise DatabaseURLError(
            "DATABASE_URL is empty. Copy your Supabase connection string into .env."
        )

    scheme, separator, remainder = url.partition("://")
    if not separator:
        raise DatabaseURLError(
            "DATABASE_URL must look like postgresql://user:password@host:port/dbname"
        )
    if scheme not in VALID_SCHEMES:
        raise DatabaseURLError(
            f"Unsupported DATABASE_URL scheme {scheme!r}. "
            f"Expected one of: {sorted(VALID_SCHEMES)}."
        )

    credentials, host_section = _split_credentials(remainder)

    # The query string is split off *after* the credentials, so an unencoded
    # "?" in a password cannot truncate it.
    host_and_path, _, query = host_section.partition("?")

    user, _, password = credentials.partition(":")
    host_and_port, _, name = host_and_path.partition("/")
    host, _, port = host_and_port.partition(":")

    if not host:
        raise DatabaseURLError("DATABASE_URL is missing a host.")

    return {
        "ENGINE": DEFAULT_ENGINE,
        "NAME": unquote(name) or DEFAULT_NAME,
        "USER": unquote(user),
        "PASSWORD": _clean_password(password),
        "HOST": host,
        "PORT": port or DEFAULT_PORT,
        "CONN_MAX_AGE": conn_max_age,
        "OPTIONS": _build_options(query),
    }


def _build_options(query: str) -> dict:
    """Turn the URL query string into psycopg2 connection keywords.

    Every parameter is passed through, not just the ones this module knows
    about: they are libpq connection keywords (``application_name``,
    ``options``, ``target_session_attrs``, ``sslrootcert``, and so on), and
    silently dropping one would be a confusing way to lose a setting.
    """
    options = dict(parse_qsl(query, keep_blank_values=True))

    # libpq keywords are plain identifiers. Anything else means the query
    # string is not a query string, which in practice means the password was
    # not percent-encoded and the URL split in the wrong place. Better to say
    # so here than to attempt a connection to a host that does not exist.
    for key in options:
        if not LIBPQ_KEYWORD.match(key):
            raise DatabaseURLError(
                f"{key!r} is not a valid connection parameter. This usually "
                "means the password contains characters that need "
                "percent-encoding: replace @ with %40, ? with %3F, / with "
                "%2F, and # with %23."
            )

    options.setdefault("sslmode", DEFAULT_SSLMODE)
    options.setdefault("connect_timeout", DEFAULT_CONNECT_TIMEOUT)

    try:
        options["connect_timeout"] = int(options["connect_timeout"])
    except (TypeError, ValueError):
        raise DatabaseURLError(
            f"connect_timeout must be a whole number of seconds, "
            f"got {options['connect_timeout']!r}."
        ) from None

    return options


def supabase_url_from_database_url(url: str) -> str:
    """Derive the Supabase project API URL from the database connection string.

    The project reference appears in one of two places depending on which
    connection string was copied out of the dashboard:

    * pooler:  ``postgresql://postgres.<ref>:...@aws-0-region.pooler.supabase.com``
    * direct:  ``postgresql://postgres:...@db.<ref>.supabase.co``

    Returning ``https://<ref>.supabase.co`` from the same string the database
    already uses removes a variable that could otherwise drift out of sync and
    point storage at a different project. Returns an empty string when the URL
    is not a Supabase one, so a plain local Postgres setup is unaffected.
    """
    if not url:
        return ""

    try:
        config = parse_database_url(url)
    except DatabaseURLError:
        return ""

    host = config["HOST"]
    user = config["USER"]

    reference = ""
    if host.endswith(".pooler.supabase.com"):
        # The pooler multiplexes projects, so the reference is on the username.
        if "." in user:
            reference = user.split(".", 1)[1]
    elif host.endswith(".supabase.co") and host.startswith("db."):
        reference = host.split(".")[1]

    return f"https://{reference}.supabase.co" if reference else ""
