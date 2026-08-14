"""Tests for the DATABASE_URL parser.

Every database connection in the project goes through this function, and the
inputs it has to survive are the ones a human pastes out of the Supabase
dashboard. The awkward cases below are all real: they are what the connection
string looked like on day one of Sprint 1.
"""

from django.test import SimpleTestCase

from .db import DatabaseURLError, parse_database_url

BASE_URL = "postgresql://user:secret@db.example.com:5432/postgres"


class DatabaseURLBasicsTests(SimpleTestCase):
    def test_parses_a_standard_url(self):
        config = parse_database_url(BASE_URL)

        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["USER"], "user")
        self.assertEqual(config["PASSWORD"], "secret")
        self.assertEqual(config["HOST"], "db.example.com")
        self.assertEqual(config["PORT"], "5432")
        self.assertEqual(config["NAME"], "postgres")

    def test_port_and_database_name_fall_back_to_postgres_defaults(self):
        config = parse_database_url("postgresql://user:secret@db.example.com")

        self.assertEqual(config["PORT"], "5432")
        self.assertEqual(config["NAME"], "postgres")

    def test_conn_max_age_is_passed_through(self):
        self.assertEqual(parse_database_url(BASE_URL, conn_max_age=600)["CONN_MAX_AGE"], 600)
        self.assertEqual(parse_database_url(BASE_URL)["CONN_MAX_AGE"], 0)

    def test_the_postgres_url_schemes_are_accepted(self):
        for scheme in ("postgres", "postgresql", "postgresql+psycopg2"):
            with self.subTest(scheme=scheme):
                config = parse_database_url(f"{scheme}://user:secret@h:5432/db")
                self.assertEqual(config["HOST"], "h")


class DatabaseURLPasswordTests(SimpleTestCase):
    """Passwords that are not percent-encoded still have to work.

    Supabase generates passwords containing punctuation, and people paste them
    in raw. Failing on those looks like an authentication problem, which is a
    slow thing to debug.
    """

    def password_for(self, raw_password):
        url = f"postgresql://user:{raw_password}@db.example.com:5432/postgres"
        return parse_database_url(url)["PASSWORD"]

    def test_unencoded_special_characters_survive(self):
        cases = {
            "pa@ss": "an unencoded at-sign",
            "pa/ss": "an unencoded slash",
            "pa#ss": "an unencoded hash",
            "pa?ss": "an unencoded question mark",
            "pa:ss": "an unencoded colon",
            "p@ss/w#rd": "an at-sign, a slash and a hash together",
        }
        for raw_password, description in cases.items():
            with self.subTest(description):
                self.assertEqual(self.password_for(raw_password), raw_password)

    def test_an_unresolvable_password_fails_with_useful_advice(self):
        """The one combination no parser can resolve: an unencoded @ and ?.

        Either "@" could be the separator between credentials and host, and
        nothing in the string says which. Rather than guess and then fail
        against a host that does not exist, say what to fix.
        """
        with self.assertRaises(DatabaseURLError) as caught:
            self.password_for("p@ss?word")

        self.assertIn("percent-encoding", str(caught.exception))

        # Percent-encoding either character resolves it.
        self.assertEqual(self.password_for("p%40ss?word"), "p@ss?word")
        self.assertEqual(self.password_for("p@ss%3Fword"), "p@ss?word")

    def test_percent_encoded_characters_are_decoded(self):
        self.assertEqual(self.password_for("pa%40ss"), "pa@ss")
        self.assertEqual(self.password_for("pa%2Fss"), "pa/ss")
        self.assertEqual(self.password_for("pa%23ss"), "pa#ss")

    def test_supabase_placeholder_brackets_are_stripped(self):
        """Supabase hands out postgresql://...:[YOUR-PASSWORD]@...

        Leaving the brackets in place is the single easiest mistake to make,
        and it authenticates as the wrong string rather than failing loudly.
        """
        self.assertEqual(self.password_for("[secret]"), "secret")

    def test_a_password_that_really_contains_brackets_is_preserved(self):
        self.assertEqual(self.password_for("se[cr]et"), "se[cr]et")

    def test_the_host_is_not_confused_by_an_at_sign_in_the_password(self):
        config = parse_database_url(
            "postgresql://postgres.abc:sec@ret@aws-0-us-west-2.pooler.supabase.com:5432/postgres"
        )
        self.assertEqual(config["USER"], "postgres.abc")
        self.assertEqual(config["PASSWORD"], "sec@ret")
        self.assertEqual(config["HOST"], "aws-0-us-west-2.pooler.supabase.com")


class DatabaseURLOptionsTests(SimpleTestCase):
    def test_sslmode_defaults_to_require(self):
        """Supabase refuses plaintext connections, so require is the safe default."""
        self.assertEqual(parse_database_url(BASE_URL)["OPTIONS"]["sslmode"], "require")

    def test_sslmode_can_be_overridden(self):
        for mode in ("disable", "prefer", "verify-full"):
            with self.subTest(sslmode=mode):
                config = parse_database_url(f"{BASE_URL}?sslmode={mode}")
                self.assertEqual(config["OPTIONS"]["sslmode"], mode)

    def test_unknown_query_parameters_are_passed_through_to_libpq(self):
        config = parse_database_url(
            f"{BASE_URL}?application_name=isgela"
            "&target_session_attrs=read-write"
            "&sslrootcert=/etc/ssl/supabase.crt"
        )

        self.assertEqual(config["OPTIONS"]["application_name"], "isgela")
        self.assertEqual(config["OPTIONS"]["target_session_attrs"], "read-write")
        self.assertEqual(config["OPTIONS"]["sslrootcert"], "/etc/ssl/supabase.crt")

    def test_percent_encoded_query_values_are_decoded(self):
        config = parse_database_url(f"{BASE_URL}?options=-csearch_path%3Dpublic")
        self.assertEqual(config["OPTIONS"]["options"], "-csearch_path=public")

    def test_connect_timeout_defaults_and_is_an_integer(self):
        self.assertEqual(parse_database_url(BASE_URL)["OPTIONS"]["connect_timeout"], 10)

        config = parse_database_url(f"{BASE_URL}?connect_timeout=30")
        self.assertEqual(config["OPTIONS"]["connect_timeout"], 30)

    def test_a_nonsense_connect_timeout_is_reported_clearly(self):
        with self.assertRaises(DatabaseURLError):
            parse_database_url(f"{BASE_URL}?connect_timeout=soon")

    def test_a_query_value_containing_an_at_sign_does_not_break_the_host(self):
        config = parse_database_url(f"{BASE_URL}?application_name=isgela@school")

        self.assertEqual(config["HOST"], "db.example.com")
        self.assertEqual(config["PASSWORD"], "secret")
        self.assertEqual(config["OPTIONS"]["application_name"], "isgela@school")


class DatabaseURLErrorTests(SimpleTestCase):
    """Bad configuration should say what is wrong, not fail somewhere later."""

    def test_an_empty_url_is_rejected(self):
        for empty in ("", None):
            with self.subTest(value=empty):
                with self.assertRaises(DatabaseURLError):
                    parse_database_url(empty)

    def test_a_url_without_a_scheme_is_rejected(self):
        with self.assertRaises(DatabaseURLError):
            parse_database_url("user:secret@db.example.com:5432/postgres")

    def test_a_non_postgres_scheme_is_rejected(self):
        with self.assertRaises(DatabaseURLError) as caught:
            parse_database_url("mysql://user:secret@db.example.com:3306/postgres")
        self.assertIn("mysql", str(caught.exception))

    def test_a_url_without_credentials_is_rejected(self):
        with self.assertRaises(DatabaseURLError):
            parse_database_url("postgresql://db.example.com:5432/postgres")

    def test_a_url_without_a_host_is_rejected(self):
        with self.assertRaises(DatabaseURLError):
            parse_database_url("postgresql://user:secret@/postgres")
