# Student & Client Marketplace

Flask + Supabase (PostgreSQL). Universities and companies subscribe. Companies
post briefs in a field of study; lecturers with that expertise apply to
supervise; once approved they place their own students and monitor the work
through four phases to the deadline. Students browse and build portfolios but
never apply directly.

## Roles

| Role | Gets in by | Can do |
|---|---|---|
| **Student** | Free signup on their university's portal | Browse projects, submit work, keep a GitHub portfolio. Cannot apply. |
| **Lecturer** | Passcode, on their university's portal | See projects matching their expertise, apply, place students, monitor phases |
| **University** | Self-signup, admin activates subscription | Issue lecturer passcodes, see all lecturers/students/placements |
| **Company** | Self-signup, admin activates subscription | Post projects, approve lecturers, watch progress |
| **Admin** | `create_admin.py` only | Platform-wide oversight — see below |

## Setup

1. **Tables** — Supabase → SQL Editor → paste `database/schema.sql` → Run.
   It drops the old v1 tables first.
2. **Connection string** — Supabase → Connect → Session pooler (port 5432).
3. **`.env`** — copy `.env.example` to `.env`, fill in both values.
4. **Run**

   ```
   pip install -r requirements.txt
   python create_admin.py
   python app.py
   ```

## First run, in order

1. Sign in as admin.
2. Register a **university** at `/register` (sign out first). Back as admin →
   Subscriptions → set it **active**.
3. As the university, the dashboard shows your portal link,
   `/u/your-institution-name`, plus direct links for each route.
4. Under Passcodes, generate one. **Copy it — shown once.**
5. Send each lecturer their code and the lecturer link. Students use the
   student link — lecturers can forward it to their classes. Everyone signs in
   afterwards at the portal.
6. Register a **company**; admin activates it; post a project in a field.
7. As the lecturer: Find projects → apply. As the company: approve.
8. As the lecturer: place students → monitor phases.
9. As a student: open the placement, submit a GitHub link.

## How work flows

No money changes hands for projects — every brief is practical experience.
Subscriptions are the only payment, and they are between the platform and the
institution or company.

1. **Company posts a brief** — title, description, deliverables, field of
   study, deadline, number of places. No amount, no bidding.
2. **A lecturer asks for it** — any open brief, not only ones matching their
   expertise. Briefs in their registered fields are simply listed first. The
   request says how the students will approach it and how many places are needed.
3. **The company approves or declines.** Nobody is placed until they approve,
   and the lecturer can never assign more students than the places granted.
4. **The lecturer assigns students** — their own, at their own institution.
   Each placement gets four phases spread to the deadline, and the student is
   notified immediately.
5. **The student builds it** and submits a repository link.
6. **The work travels up the chain**: student → lecturer → university →
   company. Each holder can pass it on with a comment, or send it back for
   correction, which returns it to the student.
7. **The company approves**, a certificate is issued, and the project appears
   in the student's profile.

A student cannot submit again while a version is still under review, and a
correction request from any level reopens submission.

## University portals

Each university gets a public page at `/u/<slug>` — its advertisement, with
student signup, lecturer signup (passcode required) and sign-in. The
university's private control panel is at `/university/dashboard`, labelled
**Admin**.

The main site at `/` is a public landing page explaining the platform, with
subscribe buttons for universities and companies.

## What a lecturer sees of their students

**Lecturer → Students** lists everyone registered under the same institution:
name, email, faculty, course, level, matric number, GitHub link, how much work
they have on, how many placements are with this lecturer, and certificates
earned. Students studying one of the lecturer's own areas of expertise are
listed first and flagged; the filter defaults to those and can be widened to
the whole institution. A lecturer never sees students from another university.

## When a subscription expires

`subscribed_until` is checked on every read, so expiry needs no scheduler.
Once past that date:

- the institution's portal shows "X's subscription has expired" and closes
  both signup routes and portal sign-in
- students and lecturers of that institution see a red banner and cannot
  assign, submit or forward anything
- existing data is untouched and becomes available again on renewal
- companies are gated the same way: no posting while expired

## The platform admin

One account oversees the whole site. It belongs to no university and no
company, and sees across all of them:

| Page | Shows | Can do |
|---|---|---|
| Dashboard | Totals for every role, pending activations, recent signups and actions | — |
| Users | Every account, filterable by role, with organisation and last login | Block / unblock with a reason |
| Universities | Every institution, its lecturers, students, open codes, placements | Set subscription, block the account |
| Companies | Every company, its projects and placements | Set subscription, block the account |
| Projects | Every brief on the platform, filterable by status | Cancel a draft or open brief |
| Placements | Every student on every project, with overdue phases flagged | — |
| Activity | The full audit log | — |

Admins cannot block themselves or other admins. Cancelling only applies to
draft and open briefs — once a project is engaged, students are working on it,
so pulling it is not a one-click action.

## Trying it before anyone pays

Two ways to get a working system without going through activation.

**Demo mode.** Put `DEMO_MODE=true` in `.env` and restart. Registration opens
on every portal regardless of subscription, and every subscription check is
bypassed, so a lecturer can apply and a company can post immediately. A brass
banner appears on every page while it is on. Turn it off before going live —
with it on, anyone who finds the site can register and post.

**Demo data.** `python seed_demo.py` fills the database with a university whose
subscription is already active, two lecturers, six students, two companies and
four projects — one of them already engaged, with students placed, phases
part-completed and a GitHub submission in place. Every page then has something
real on it.

Every seeded account uses the password `demo1234` and an email ending in
`@demo.marketplace`. The script prints the full list plus two unused lecturer
passcodes so you can walk the registration flow by hand. Re-running it removes
and rebuilds only its own accounts — your admin and anything you created
yourself are untouched.

Delete `seed_demo.py` before deploying, or at minimum never run it against a
live database.

## Security

- Passwords and passcodes are both hashed. A leaked database yields no
  working codes.
- Passcodes are single-use, expire in 14 days, and are consumed inside the
  registration transaction, so two people racing the same code cannot both win.
- Lecturers only ever see projects in their declared fields — enforced in SQL,
  not by hiding links.
- A lecturer can only place students from their own university and field.
- Subscription checks gate the paid actions; read-only pages stay open so an
  expired account can still see its data.
- Blocked accounts are signed out on their next request and cannot log in.
- Admins cannot block themselves or other admins.
- Every block, unblock, subscription change and passcode batch is written to
  `audit_log`.

## Not built yet

- **Payment** — subscriptions are activated by an admin after payment clears
  out of band. Flutterwave integration is the obvious next step, and needs a
  decision on whether project rewards are held in escrow or paid direct.
- **Assessment and certificates** — the tables exist; scoring a submission and
  issuing a certificate is the next feature.
- **File uploads** — submissions take a repository link. Uploaded files would
  need Supabase Storage, since hosts wipe local disks on restart.
- **Email** — passcodes are shown on screen, not emailed.

## Deployment

Not Netlify — it cannot run Python. `Procfile` and `render.yaml` are included,
so Render works out of the box: connect the repository, and it reads the build
command, start command and health check from `render.yaml`.

Set these environment variables on the host:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Supabase session pooler URI, port 5432 |
| `SECRET_KEY` | 64 random hex characters. Render can generate it |
| `SECURE_COOKIES` | `true` once the site is on HTTPS |

**Before going live:**

- Set `SECURE_COOKIES=true`. Without it the session cookie is sent over plain
  HTTP as well.
- Remove or restrict `create_admin.py` on the server. Anyone with shell access
  can otherwise mint an administrator.
- Rotate `SECRET_KEY` away from any value used in development. Changing it
  signs everyone out, which is the point.
- Take a Supabase backup before the first real users arrive.

**What is already handled:** `ProxyFix` so the real client address and scheme
survive the load balancer; a `/healthz` probe that does not touch the database;
sign-in throttling per address (8 failures, then a five-minute cooldown,
configurable); an upload cap with its own error page; and a refusal to start at
all if `SECRET_KEY` is missing, rather than falling back to a default.

**Known limits.** Throttling is per process and in memory — run more than one
web worker process and each keeps its own count; move it to the database or
Redis if that matters. There is no password reset flow yet, no email, and
subscriptions are still activated by hand.
