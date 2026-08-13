# iSgela

An AI-powered platform connecting South African teachers, students, and parents — cutting the time teachers spend on marking and admin so they can spend more time teaching and tailor guiding invididual students to a future that suits them.


## The problem

Africa's (and other continents) education system has a connection problem, not just a resourcing problem — three groups who all need to work together often aren't in contact at all.

**Parents are disconnected from their child's progress.** 
Many parents work long hours and can't get to the school to ask a teacher how their child is actually doing. Without that connection, a child can be struggling — or thriving — and the parent finds out too late, or not at all.

**Teachers lack a clear picture of where each student is falling behind.** 
Marking is manual and time-consuming, especially in the foundation phase, which leaves little time to actually track *why* a student keeps getting something wrong and step in early.

**Students who don't speak up fall through the cracks.** 
Not every child tells a teacher or parent when they're struggling, skipping homework, or avoiding school. Without a system watching for that pattern, it goes unnoticed until it's a bigger problem.

## Solution
*iSgela* closes these gaps by putting the teacher, student, and parent in one connected loop — so a marked paper/exam, a missed homework assignment, or an absence surfaces to the people who need to know, automatically, instead of depending on everyone finding time to communicate manually.

### Beyond the classroom

At scale, this also becomes a dataset many countries doesn't currently have: real, granular visibility into where students nationally are struggling and why. That's a longer-term direction, not part of this MVP, but it's the reason this problem is worth solving properly from day one rather than as a one-off tool.

## What it does

- **Scan & Mark** — teachers photograph a paper or students submit homework by camera; AI marks it against the memorandum and explains *why* an answer was wrong, not just that it was and also suggests solutions on how to help the student if they struggled. 
- **Attendance** — single facial-recognition scan at arrival and departure, no manual register. Uploads the record on both the parent and teacher's portal
- **Three connected portals** — teacher, student, and parent each see what matters to them, in one place
- **Parent notifications** — plain-language summaries sent automatically when homework/exam is marked

## Tech stack

- Django + Django REST Framework
- Supabase (Postgres, storage)
- Claude API (vision-based marking)
- face-api.js (attendance check-in)
- Twilio WhatsApp sandbox (parent notifications)

## Setup
 TODO:

## Built with Kiro

See `.kiro/sprints/*.md` for the full sprint-by-sprint breakdown of how this was built.

## Demo video

[Link once recorded]