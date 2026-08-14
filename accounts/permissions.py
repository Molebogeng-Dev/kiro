"""Role-based access control.

Enforced on the view, not in the template. Hiding a nav link is presentation;
this is the actual gate, so guessing a URL gets you a 403 rather than a page
you were not meant to see.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_required(*roles):
    """Allow only the given roles through; anyone else gets a 403.

    Anonymous visitors are sent to the login page first (via login_required),
    so an unauthenticated request never leaks the difference between "wrong
    role" and "not logged in".

    Usage::

        @role_required(Role.TEACHER)
        def teacher_dashboard(request): ...
    """
    allowed = {str(role) for role in roles}

    def decorator(view):
        @wraps(view)
        def check_role(request, *args, **kwargs):
            if request.user.role not in allowed:
                raise PermissionDenied(
                    "This page is not available for your account type."
                )
            return view(request, *args, **kwargs)

        return login_required(check_role)

    return decorator
