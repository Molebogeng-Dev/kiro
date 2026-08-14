"""Transport layer for OpenRouter's OpenAI-compatible chat completions API.

This module knows how to make one HTTP call and how to interpret the ways it can
fail. It does not know anything about marking; that separation is what lets the
parsing and prompting be tested without a network.

Failure modes are split into distinct exceptions rather than one generic error
because the caller genuinely needs to tell them apart. On a free tier, "you have
been rate limited" is an expected Tuesday afternoon, "this model slug no longer
exists" is a config problem, and "the provider 500ed" is worth retrying. A
single ``APIError`` would flatten all three into a shrug.
"""

import base64
import logging
import time
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# One retry, because a timeout or a 502 is usually transient. Rate limits are
# deliberately excluded: retrying into a limit is how you stay limited.
TRANSIENT_RETRY_DELAY_SECONDS = 2


class OpenRouterError(Exception):
    """Base class for anything that went wrong talking to OpenRouter."""


class OpenRouterNotConfigured(OpenRouterError):
    """No API key, so no call can be attempted."""


class RateLimited(OpenRouterError):
    """HTTP 429. Free-tier quota is exhausted, at least for now."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class InsufficientCredit(OpenRouterError):
    """HTTP 402. The chosen model is not free and the account has no credit."""


class ModelUnavailable(OpenRouterError):
    """HTTP 404 on a model slug. Usually a slug that has been retired."""


class ServiceUnavailable(OpenRouterError):
    """A timeout, a connection failure, or a 5xx from the provider."""


class UnexpectedResponse(OpenRouterError):
    """A 2xx that did not contain a message we can use."""


@dataclass(frozen=True)
class Completion:
    """A successful reply."""

    content: str
    model: str
    usage: dict

    @property
    def total_tokens(self):
        return self.usage.get("total_tokens")


class OpenRouterClient:
    """Calls one or more vision models until one of them answers."""

    def __init__(
        self,
        api_key,
        models,
        base_url="https://openrouter.ai/api/v1",
        timeout=120,
        max_tokens=2000,
        app_url="",
        app_title="",
    ):
        if not models:
            raise OpenRouterNotConfigured("No model configured for marking.")

        self.api_key = api_key
        self.models = list(models)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.app_url = app_url
        self.app_title = app_title

    @classmethod
    def from_settings(cls):
        models = [settings.OPENROUTER_MODEL, *settings.OPENROUTER_FALLBACK_MODELS]
        return cls(
            api_key=settings.OPENROUTER_API_KEY,
            models=[model for model in models if model],
            base_url=settings.OPENROUTER_BASE_URL,
            timeout=settings.OPENROUTER_TIMEOUT,
            max_tokens=settings.OPENROUTER_MAX_TOKENS,
            app_url=settings.OPENROUTER_APP_URL,
            app_title=settings.OPENROUTER_APP_TITLE,
        )

    # -- public API -------------------------------------------------------- #

    def complete_with_image(self, *, system_prompt, user_prompt, image_bytes, mime_type="image/jpeg"):
        """Send a prompt plus one image and return the assistant's reply.

        Each configured model is tried in turn. A rate limit or an unavailable
        slug moves on to the next one; if every model is rate limited, the rate
        limit is what surfaces, because that is the actionable fact.
        """
        if not self.api_key:
            raise OpenRouterNotConfigured(
                "OPENROUTER_API_KEY is not set. Add it to .env before marking."
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": self._data_uri(image_bytes, mime_type)},
                    },
                ],
            },
        ]

        rate_limit_error = None
        last_error = None

        for model in self.models:
            try:
                return self._post_completion(model, messages)
            except RateLimited as exc:
                logger.warning("Rate limited on %s; trying the next model.", model)
                rate_limit_error = exc
                last_error = exc
            except (ModelUnavailable, InsufficientCredit) as exc:
                logger.warning("%s unusable (%s); trying the next model.", model, exc)
                last_error = exc

        if rate_limit_error is not None:
            raise rate_limit_error
        raise last_error

    # -- internals --------------------------------------------------------- #

    @staticmethod
    def _data_uri(image_bytes, mime_type):
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @property
    def _headers(self):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # Optional attribution headers, so this app's usage is identifiable in
        # the OpenRouter dashboard.
        if self.app_url:
            headers["HTTP-Referer"] = self.app_url
        if self.app_title:
            headers["X-Title"] = self.app_title
        return headers

    def _post_completion(self, model, messages):
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            # Marking must not vary between identical submissions.
            "temperature": 0,
        }

        response = self._post_with_one_retry(payload)
        self._raise_for_status(response, model)

        return self._extract_completion(response, model)

    def _post_with_one_retry(self, payload):
        url = f"{self.base_url}/chat/completions"

        for attempt in (1, 2):
            try:
                response = requests.post(
                    url, headers=self._headers, json=payload, timeout=self.timeout
                )
            except requests.Timeout as exc:
                if attempt == 2:
                    raise ServiceUnavailable(
                        f"OpenRouter did not respond within {self.timeout}s."
                    ) from exc
            except requests.RequestException as exc:
                if attempt == 2:
                    raise ServiceUnavailable(f"Could not reach OpenRouter: {exc}") from exc
            else:
                # A 5xx is worth one retry; anything else is final.
                if response.status_code < 500 or attempt == 2:
                    return response
                logger.warning(
                    "OpenRouter returned %s; retrying once.", response.status_code
                )

            time.sleep(TRANSIENT_RETRY_DELAY_SECONDS)

        raise ServiceUnavailable("OpenRouter could not be reached.")

    @staticmethod
    def _raise_for_status(response, model):
        if response.status_code == 200:
            return

        detail = _error_detail(response)

        if response.status_code == 429:
            raise RateLimited(
                f"OpenRouter rate limit reached for {model}: {detail}",
                retry_after=response.headers.get("Retry-After"),
            )
        if response.status_code == 402:
            raise InsufficientCredit(
                f"{model} requires OpenRouter credit: {detail}"
            )
        if response.status_code == 404:
            raise ModelUnavailable(
                f"OpenRouter does not recognise the model {model!r}: {detail}. "
                "Free-tier slugs are withdrawn regularly; check "
                "openrouter.ai/models and update OPENROUTER_MODEL."
            )
        if response.status_code in (401, 403):
            raise OpenRouterNotConfigured(
                f"OpenRouter rejected the API key: {detail}"
            )
        if response.status_code >= 500:
            raise ServiceUnavailable(
                f"OpenRouter returned {response.status_code}: {detail}"
            )

        raise OpenRouterError(f"OpenRouter returned {response.status_code}: {detail}")

    @staticmethod
    def _extract_completion(response, model):
        try:
            body = response.json()
        except ValueError as exc:
            raise UnexpectedResponse("OpenRouter returned a non-JSON body.") from exc

        # OpenRouter can return a 200 whose body is an error envelope, for
        # instance when an upstream provider refuses the request.
        if "error" in body and not body.get("choices"):
            message = (body.get("error") or {}).get("message", "unknown error")
            raise ServiceUnavailable(f"OpenRouter reported: {message}")

        choices = body.get("choices") or []
        if not choices:
            raise UnexpectedResponse("OpenRouter returned no choices.")

        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, list):
            # Some providers return the OpenAI content-parts shape.
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not content or not content.strip():
            raise UnexpectedResponse("The model returned an empty reply.")

        return Completion(
            content=content,
            model=body.get("model") or model,
            usage=body.get("usage") or {},
        )


def _error_detail(response):
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]

    error = body.get("error")
    if isinstance(error, dict):
        return error.get("message") or str(error)[:200]
    return str(error or body)[:200]
