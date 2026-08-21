"""Registration and login."""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render

from .forms import RegistrationError, RegistrationForm, SchoolRegistrationForm


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
            try:
                user = form.save()
            except RegistrationError:
                # A teacher code was claimed by someone else between validating
                # and saving. Nothing was persisted; show the error and re-render.
                form.add_error(
                    "code",
                    "That code was just used by someone else. Ask your school "
                    "for a new one.",
                )
            else:
                login(request, user)
                messages.success(
                    request,
                    f"Welcome to iSgela, {user.get_short_name() or user.username}.",
                )
                return redirect("core:home")
    else:
        form = RegistrationForm()

    return render(request, "accounts/register.html", {"form": form})


def register_school(request):
    """Register a school and its administrator in one step.

    A distinct flow from the teacher/student/parent form: those join an existing
    school with a code, this one creates the school itself.
    """
    if request.user.is_authenticated:
        return redirect("core:home")

    if request.method == "POST":
        form = SchoolRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(
                request,
                f"Welcome to iSgela, {user.get_short_name() or user.username}. "
                f"{user.school.name} is registered.",
            )
            return redirect("core:home")
    else:
        form = SchoolRegistrationForm()

    return render(request, "accounts/register_school.html", {"form": form})
