"""Tests for the OpenRouter transport.

The point of these is that each HTTP failure becomes a *distinct* exception. On a
free tier, being able to tell a rate limit from a retired model slug from a
provider outage is the difference between knowing what to do and guessing.
"""

from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from marking.openrouter import (
    InsufficientCredit,
    ModelUnavailable,
    OpenRouterClient,
    OpenRouterError,
    OpenRouterNotConfigured,
    RateLimited,
    ServiceUnavailable,
    UnexpectedResponse,
)

from .support import FakeResponse, completion_response, error_response, valid_marking_json


def build_client(models=("primary/model",), **kwargs):
    return OpenRouterClient(api_key="sk-or-test", models=list(models), **kwargs)


def call(client):
    return client.complete_with_image(
        system_prompt="system",
        user_prompt="user",
        image_bytes=b"pretend-jpeg-bytes",
    )


class RequestShapeTests(SimpleTestCase):
    def test_the_image_is_sent_as_a_base64_data_uri(self):
        client = build_client()

        with patch("marking.openrouter.requests.post") as post:
            post.return_value = completion_response(valid_marking_json())
            call(client)

        payload = post.call_args.kwargs["json"]
        content = payload["messages"][1]["content"]
        image_part = next(part for part in content if part["type"] == "image_url")

        self.assertTrue(image_part["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(payload["model"], "primary/model")
        # Marking the same paper twice should not produce two different marks.
        self.assertEqual(payload["temperature"], 0)

    def test_attribution_headers_are_sent_when_configured(self):
        client = build_client(app_url="https://isgela.example", app_title="iSgela")

        with patch("marking.openrouter.requests.post") as post:
            post.return_value = completion_response(valid_marking_json())
            call(client)

        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["HTTP-Referer"], "https://isgela.example")
        self.assertEqual(headers["X-Title"], "iSgela")

    def test_a_missing_api_key_is_reported_before_any_request(self):
        client = OpenRouterClient(api_key="", models=["primary/model"])

        with patch("marking.openrouter.requests.post") as post:
            with self.assertRaises(OpenRouterNotConfigured):
                call(client)

        post.assert_not_called()


class ResponseHandlingTests(SimpleTestCase):
    def test_a_successful_reply_is_returned_with_its_model_and_usage(self):
        client = build_client()

        with patch("marking.openrouter.requests.post") as post:
            post.return_value = completion_response(valid_marking_json(), model="served/model")
            completion = call(client)

        self.assertIn("questions", completion.content)
        self.assertEqual(completion.model, "served/model")
        self.assertEqual(completion.total_tokens, 1234)

    def test_content_returned_as_parts_is_joined(self):
        """Some providers use OpenAI's content-parts shape for the reply."""
        client = build_client()
        body = {
            "model": "served/model",
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": '{"questions": '},
                            {"type": "text", "text": "[]}"},
                        ]
                    }
                }
            ],
        }

        with patch("marking.openrouter.requests.post") as post:
            post.return_value = FakeResponse(200, body)
            completion = call(client)

        self.assertEqual(completion.content, '{"questions": []}')

    def test_an_empty_reply_is_an_error(self):
        client = build_client()

        with patch("marking.openrouter.requests.post") as post:
            post.return_value = completion_response("   ")
            with self.assertRaises(UnexpectedResponse):
                call(client)

    def test_an_error_envelope_inside_a_200_is_treated_as_a_failure(self):
        client = build_client()

        with patch("marking.openrouter.requests.post") as post:
            post.return_value = FakeResponse(200, {"error": {"message": "upstream refused"}})
            with self.assertRaises(ServiceUnavailable) as caught:
                call(client)

        self.assertIn("upstream refused", str(caught.exception))


class FailureMappingTests(SimpleTestCase):
    """Each status code maps to its own exception type."""

    def assert_status_raises(self, status_code, expected, **kwargs):
        client = build_client()
        with patch("marking.openrouter.requests.post") as post:
            post.return_value = error_response(status_code, **kwargs)
            with self.assertRaises(expected) as caught:
                call(client)
        return caught.exception

    def test_429_is_a_rate_limit_and_carries_retry_after(self):
        exception = self.assert_status_raises(
            429, RateLimited, message="rate limit exceeded", headers={"Retry-After": "30"}
        )
        self.assertEqual(exception.retry_after, "30")

    def test_402_is_insufficient_credit(self):
        self.assert_status_raises(402, InsufficientCredit)

    def test_404_is_an_unavailable_model_and_says_slugs_change(self):
        exception = self.assert_status_raises(404, ModelUnavailable)
        self.assertIn("openrouter.ai/models", str(exception))

    def test_401_and_403_are_configuration_problems(self):
        for status_code in (401, 403):
            with self.subTest(status_code=status_code):
                self.assert_status_raises(status_code, OpenRouterNotConfigured)

    def test_an_unrecognised_status_is_a_generic_error(self):
        self.assert_status_raises(418, OpenRouterError)

    def test_a_rate_limit_is_not_retried(self):
        """Retrying into a rate limit is how you stay rate limited."""
        client = build_client()

        with patch("marking.openrouter.requests.post") as post:
            post.return_value = error_response(429)
            with self.assertRaises(RateLimited):
                call(client)

        self.assertEqual(post.call_count, 1)


class TransientFailureTests(SimpleTestCase):
    def setUp(self):
        # The client sleeps between attempts; no need to actually wait.
        patcher = patch("marking.openrouter.time.sleep")
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_a_500_is_retried_once_then_succeeds(self):
        client = build_client()

        with patch("marking.openrouter.requests.post") as post:
            post.side_effect = [
                error_response(500, "internal error"),
                completion_response(valid_marking_json()),
            ]
            completion = call(client)

        self.assertEqual(post.call_count, 2)
        self.assertIn("questions", completion.content)

    def test_a_persistent_500_becomes_service_unavailable(self):
        client = build_client()

        with patch("marking.openrouter.requests.post") as post:
            post.return_value = error_response(503, "overloaded")
            with self.assertRaises(ServiceUnavailable):
                call(client)

        self.assertEqual(post.call_count, 2)

    def test_a_timeout_is_retried_once_then_reported(self):
        client = build_client(timeout=5)

        with patch("marking.openrouter.requests.post") as post:
            post.side_effect = requests.Timeout("too slow")
            with self.assertRaises(ServiceUnavailable) as caught:
                call(client)

        self.assertEqual(post.call_count, 2)
        self.assertIn("5s", str(caught.exception))

    def test_a_connection_error_is_reported_as_unreachable(self):
        client = build_client()

        with patch("marking.openrouter.requests.post") as post:
            post.side_effect = requests.ConnectionError("no route to host")
            with self.assertRaises(ServiceUnavailable):
                call(client)

    def test_a_timeout_then_success_recovers(self):
        client = build_client()

        with patch("marking.openrouter.requests.post") as post:
            post.side_effect = [
                requests.Timeout("too slow"),
                completion_response(valid_marking_json()),
            ]
            self.assertIn("questions", call(client).content)


class FallbackModelTests(SimpleTestCase):
    """Free-tier slugs get withdrawn and rate limited, so a fallback list helps."""

    def test_an_unavailable_primary_model_falls_through_to_the_next(self):
        client = build_client(models=("gone/model", "working/model"))

        with patch("marking.openrouter.requests.post") as post:
            post.side_effect = [
                error_response(404, "no such model"),
                completion_response(valid_marking_json(), model="working/model"),
            ]
            completion = call(client)

        self.assertEqual(completion.model, "working/model")
        self.assertEqual(
            [call_.kwargs["json"]["model"] for call_ in post.call_args_list],
            ["gone/model", "working/model"],
        )

    def test_a_rate_limited_primary_model_falls_through(self):
        client = build_client(models=("busy/model", "working/model"))

        with patch("marking.openrouter.requests.post") as post:
            post.side_effect = [
                error_response(429),
                completion_response(valid_marking_json(), model="working/model"),
            ]
            self.assertEqual(call(client).model, "working/model")

    def test_when_every_model_is_rate_limited_the_rate_limit_surfaces(self):
        """Not a generic failure: the actionable fact is that we are throttled."""
        client = build_client(models=("busy/one", "busy/two"))

        with patch("marking.openrouter.requests.post") as post:
            post.return_value = error_response(429)
            with self.assertRaises(RateLimited):
                call(client)

        self.assertEqual(post.call_count, 2)

    def test_a_rate_limit_outranks_a_later_model_unavailable(self):
        client = build_client(models=("busy/one", "gone/two"))

        with patch("marking.openrouter.requests.post") as post:
            post.side_effect = [error_response(429), error_response(404)]
            with self.assertRaises(RateLimited):
                call(client)

    def test_configuring_no_models_is_rejected_immediately(self):
        with self.assertRaises(OpenRouterNotConfigured):
            OpenRouterClient(api_key="sk-or-test", models=[])
