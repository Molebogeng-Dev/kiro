"""Registration and login."""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render

from .forms import RegistrationForm


class RoleLoginView(LoginView):
    """Standard Django login.

    Where you land afterwards is decided by core:home, which reads the role off
    the logged-in user, so there is no per-role login page to maintain.
    """

    template_name = "accounts/login.html"
    redirect_authenticated_user = True


def register(request):
    """Create an account for whichever role the visitor picked."""
    if request.user.is_authenticated:
        return redirect("core:home")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(
                request,
                f"Welcome to iSgela, {user.get_short_name() or user.username}.",
            )
            return redirect("core:home")
    else:
        form = RegistrationForm()

    return render(request, "accounts/register.html", {"form": form})
