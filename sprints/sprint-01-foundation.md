# Sprint 1 — Foundation

## Overview

Sprint 1 lays the groundwork for iSega: an AI-powered platform connecting  teachers, students, and parents around a student's schoolwork, so less time goes to admin and more time goes to teaching. This sprint builds no AI, camera, or portal features yet — just the skeleton every later sprint depends on: the project setup,the database connection, and role-based access for the app's three users.

## Goals

- Stand up the Django project with a clean, extensible structure
- Connect to a serverless Postgres database (Supabase)
- Support three distinct user roles — teacher, student, parent — with a custom user model
- Get registration and login working for all three roles
- Route each role to its own dashboard, and enforce that no role can access another role's pages
- Keep secrets out of the codebase from day one

## What was built

- **Django project** with separate `accounts` and `core` apps rather than one monolithic app
- **Config management** via environment variables (`python-decouple` or `django-environ`) and the real `.env` gitignored — no connection strings or keys hardcoded anywhere
- **Supabase Postgres connection**, confirmed working via Django's default auth migrations
- **Custom User model** with a `role` field (`teacher` / `student` / `parent`), 
where:
  - each user has exactly one role
  - a student can be linked to one or more parent accounts
  - a placeholder `School` model exists (name field only) — the full school/class structure comes in a later sprint
- **Auth flows**: registration and login for all three roles
- **Role-based routing**: `/teacher/`, `/student/`, `/parent/` dashboards, each currently a placeholder page ("Welcome, [role]")
- **Access control enforced at the view level** (not just hidden links) — a student cannot reach `/teacher/` by guessing the URL, and so on
- **Basic navigation shell** per role — functional, not yet styled

## Tech stack (this sprint)

- Django
- Supabase (Postgres)
- python-decouple (or django-environ) for environment config

## Setup

1. Clone the repo and `cd` into it
2. Create a virtual environment and activate it
3. `pip install -r requirements.txt`
4. Create `.env` in the project root and fill in your Supabase connection string (the README lists every variable)
5. `python manage.py migrate`
6. `python manage.py runserver`
7. Visit `/accounts/register/`, create one account per role (teacher, student, parent), and confirm each lands on its correct dashboard after login

## Definition of done

- [x] Can register as a teacher, student, or parent
- [x] Can log in and land on the correct dashboard for that role
- [x] Cannot access another role's dashboard by URL-guessing
- [x] Project runs from a fresh clone with just `.env` filled in and
      standard Django commands
- [x] A basic automated test confirms role-based access control works

## Out of scope for this sprint

AI marking, camera/scanning, facial recognition, WhatsApp notifications, study materials, homework, attendance tracking, and dashboards with real data. These land in Sprints 2–7.

## Known limitations

- Dashboards are placeholders with no real content yet
- `School` model is a name-only stub, not a full structure
- Styling is minimal — functional but not demo-polished

## Next up — Sprint 2

Build the AI Scan & Mark engine: a teacher or student submits a photo of a paper, it's matched against a memorandum, and the system returns a score plus per-question feedback and suggestions. This engine is built once and reused by both the teacher's exam-marking flow and the student's homework-submission flow in later sprints.