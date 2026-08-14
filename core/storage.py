"""Django storage backend for Supabase Storage.

Implemented against Supabase's Storage REST API with ``requests`` rather than
the ``supabase`` SDK: the SDK pulls in a substantial dependency tree for what
amounts to five HTTP calls, and we are already using ``requests`` to talk to
OpenRouter.

Because this is a real ``Storage`` subclass, ``ImageField`` and friends work
normally and later sprints get remote storage for free.

Privacy note: papers are children's schoolwork, so the bucket is created
private and ``url()`` returns a time-limited signed URL rather than a public
one. Making the bucket public would expose every uploaded paper to anyone who
can guess a path.
"""

from urllib.parse import quote

import requests
from django.core.files.base import ContentFile
from django.core.files.storage import Storage, storages
from django.utils.deconstruct import deconstructible

DEFAULT_TIMEOUT = 30
DEFAULT_SIGNED_URL_EXPIRY = 60 * 60  # one hour


class SupabaseStorageError(Exception):
    """A Supabase Storage request failed."""


class SupabaseStorageNotConfigured(SupabaseStorageError):
    """Credentials are missing, so no request can be attempted."""


@deconstructible
class SupabaseStorage(Storage):
    """Store files in a Supabase Storage bucket.

    Configuration comes from ``STORAGES`` in settings, which reads it from the
    environment. Nothing is validated at import time so that ``manage.py
    check``, ``migrate``, and the test suite all work on a machine with no
    Supabase keys; the error surfaces on first use instead, where it can say
    something useful.
    """

    def __init__(
        self,
        bucket=None,
        base_url=None,
        service_key=None,
        signed_url_expiry=DEFAULT_SIGNED_URL_EXPIRY,
        timeout=DEFAULT_TIMEOUT,
    ):
        self.bucket = bucket
        self.base_url = (base_url or "").rstrip("/")
        self.service_key = service_key
        self.signed_url_expiry = signed_url_expiry
        self.timeout = timeout

    # -- configuration ---------------------------------------------------- #

    def _require_config(self):
        missing = [
            name
            for name, value in (
                ("SUPABASE_URL", self.base_url),
                ("SUPABASE_SERVICE_KEY", self.service_key),
                ("SUPABASE_STORAGE_BUCKET", self.bucket),
            )
            if not value
        ]
        if missing:
            raise SupabaseStorageNotConfigured(
                "Supabase Storage is not configured. Missing: "
                + ", ".join(missing)
                + ". Add them to .env (see the README setup section)."
            )

    @property
    def _headers(self):
        return {"Authorization": f"Bearer {self.service_key}"}

    def _object_url(self, name, prefix="object"):
        # Object names contain slashes that must stay slashes, so only the
        # individual path segments are escaped.
        path = "/".join(quote(segment, safe="") for segment in name.split("/"))
        return f"{self.base_url}/storage/v1/{prefix}/{self.bucket}/{path}"

    # -- Django Storage API ----------------------------------------------- #

    def _save(self, name, content):
        self._require_config()

        content.seek(0)
        payload = content.read()

        response = requests.post(
            self._object_url(name),
            headers={
                **self._headers,
                "Content-Type": getattr(content, "content_type", None) or "image/jpeg",
                # Object names are UUID-based, so an upsert only ever happens
                # on a genuine retry of the same upload.
                "x-upsert": "true",
            },
            data=payload,
            timeout=self.timeout,
        )

        if response.status_code not in (200, 201):
            raise SupabaseStorageError(
                f"Upload of {name!r} failed with HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        return name

    def _open(self, name, mode="rb"):
        self._require_config()

        if "w" in mode:
            raise SupabaseStorageError("Supabase Storage files are not opened for writing.")

        response = requests.get(
            self._object_url(name), headers=self._headers, timeout=self.timeout
        )

        if _is_not_found(response):
            raise FileNotFoundError(f"{name!r} is not in the {self.bucket!r} bucket.")
        if response.status_code != 200:
            raise SupabaseStorageError(
                f"Download of {name!r} failed with HTTP {response.status_code}."
            )

        return ContentFile(response.content, name=name)

    def delete(self, name):
        self._require_config()

        response = requests.delete(
            self._object_url(name), headers=self._headers, timeout=self.timeout
        )

        # Django's contract is that deleting a missing file is not an error, and
        # it calls delete() whenever a FileField is replaced. Supabase reports a
        # missing object as HTTP 400 with a 404 in the body, so the status code
        # alone is not enough to tell "already gone" from "actually broken".
        if response.status_code in (200, 204) or _is_not_found(response):
            return

        raise SupabaseStorageError(
            f"Delete of {name!r} failed with HTTP {response.status_code}: "
            f"{response.text[:200]}"
        )

    def exists(self, name):
        self._require_config()

        response = requests.get(
            self._object_url(name, prefix="object/info"),
            headers=self._headers,
            timeout=self.timeout,
        )

        if response.status_code == 200:
            return True
        if _is_not_found(response):
            return False

        # Anything else is a real failure. Reporting it as "does not exist"
        # would let Django overwrite an object it could not read.
        raise SupabaseStorageError(
            f"Could not check whether {name!r} exists: HTTP "
            f"{response.status_code} {response.text[:200]}"
        )

    def size(self, name):
        metadata = self._info(name)
        return int(metadata.get("size") or metadata.get("contentLength") or 0)

    def url(self, name):
        """A signed, expiring URL. See the privacy note in the module docstring."""
        self._require_config()

        response = requests.post(
            self._object_url(name, prefix="object/sign"),
            headers=self._headers,
            json={"expiresIn": self.signed_url_expiry},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise SupabaseStorageError(
                f"Could not sign a URL for {name!r}: HTTP {response.status_code} "
                f"{response.text[:200]}"
            )

        signed_path = response.json().get("signedURL") or response.json().get("signedUrl")
        if not signed_path:
            raise SupabaseStorageError(f"Supabase returned no signed URL for {name!r}.")

        return f"{self.base_url}/storage/v1{signed_path}"

    def listdir(self, path):
        raise NotImplementedError("Directory listing is not needed for iSgela.")

    # -- helpers ---------------------------------------------------------- #

    def _info(self, name):
        self._require_config()

        response = requests.get(
            self._object_url(name, prefix="object/info"),
            headers=self._headers,
            timeout=self.timeout,
        )

        if _is_not_found(response):
            raise FileNotFoundError(f"{name!r} is not in the {self.bucket!r} bucket.")
        if response.status_code != 200:
            raise SupabaseStorageError(
                f"No metadata for {name!r}: HTTP {response.status_code}."
            )
        return response.json()

    def ensure_bucket(self, public=False):
        """Create the bucket if it is not there yet. Idempotent.

        Used by the ``ensure_storage_bucket`` management command so a fresh
        clone does not need anyone to click through the Supabase dashboard.
        """
        self._require_config()

        response = requests.get(
            f"{self.base_url}/storage/v1/bucket/{self.bucket}",
            headers=self._headers,
            timeout=self.timeout,
        )
        if response.status_code == 200:
            return False

        response = requests.post(
            f"{self.base_url}/storage/v1/bucket",
            headers=self._headers,
            json={"id": self.bucket, "name": self.bucket, "public": public},
            timeout=self.timeout,
        )
        if response.status_code not in (200, 201):
            raise SupabaseStorageError(
                f"Could not create bucket {self.bucket!r}: HTTP "
                f"{response.status_code} {response.text[:300]}"
            )
        return True


def _is_not_found(response) -> bool:
    """Did Supabase mean "no such object", whatever status code it used?

    Supabase Storage reports a missing object as HTTP 400 with a body of
    ``{"statusCode":"404","error":"not_found","code":"NoSuchKey"}``. Matching on
    the status code alone would turn "already deleted" into a hard error, so the
    body is consulted too. Verified against the live API rather than assumed.
    """
    if response.status_code == 404:
        return True
    if response.status_code != 400:
        return False

    try:
        body = response.json()
    except ValueError:
        return False

    if not isinstance(body, dict):
        return False

    return (
        str(body.get("statusCode")) == "404"
        or body.get("error") == "not_found"
        or body.get("code") == "NoSuchKey"
    )


def papers_storage():
    """Storage for uploaded papers.

    A callable rather than a direct reference so that ``ImageField`` resolves it
    at runtime: it keeps migrations free of credentials and lets the test suite
    swap in in-memory storage.
    """
    return storages["papers"]
