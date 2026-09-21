# Nuvision High School — Student Council Election System

A complete, self-contained web app for running student council elections:
student registration/login, application letter uploads, Discipline Master (DM)
review and interview decisions, campaigning, voting, live results, and winner
announcements.

This is a **real Flask + SQLite web application** — not a Claude artifact —
so it works for any number of students on any device, with no Claude account
needed. It has been tested end-to-end (registration, applications, review,
voting, live results, winner announcement, deadline enforcement) and runs
without errors.

## What it does

- **Students** create an account with their full name + a personal nickname
  and password, log in, submit their application letter (Word `.doc`/`.docx`
  or PDF) during the application window, see DM feedback, view candidates
  during campaigning, and vote once per position during the voting window.
- **The Discipline Master (DM)** is the only admin. The DM:
  - Sets the available positions (President, Vice President, etc.)
  - Sets the schedule: when applications open/close, when campaigning
    starts, and the voting window
  - Reviews every application (with the uploaded document), leaves
    feedback, approves students for interview, and after the interview
    confirms who becomes an official candidate
  - Watches **live results** (auto-refreshing, rising bar chart) that only
    the DM can see
  - Announces the winner for each position, which then becomes visible to
    students
  - Is the only one who can grant **read-only results access** to other
    staff (they get their own login but cannot change anything)
- Students automatically see a **warning banner** as the application
  deadline approaches (2-day, 24-hour, and 1-hour thresholds), and the
  application form **closes itself automatically** once the deadline
  passes — no manual step needed.

## Project structure

```
nuvision/
├── app.py                 # All routes and logic
├── requirements.txt
├── templates/              # All pages (Jinja2)
├── static/style.css        # All styling
└── uploads/                 # Uploaded application letters (created automatically)
```

## Running it locally

```bash
cd nuvision
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python3 app.py
```

Open **http://localhost:5000**. The database (`nuvision.db`) is created
automatically on first run.

## First-time setup (important!)

1. Go to the home page and click **"First-time Setup"** under the Discipline
   Master panel.
2. You'll be asked for a **setup code**. By default it is:

   ```
   NUVISION-DM-2026
   ```

   **Change this before you deploy for real students**, by setting an
   environment variable:

   ```bash
   export NUVISION_DM_CODE="something-only-you-know"
   ```

   This code is what stops a random student from creating themselves a DM
   account — only share it with the actual Discipline Master. Only **one**
   DM account is needed; once it exists, the setup page is disabled.

3. Also set a real session secret in production:

   ```bash
   export NUVISION_SECRET_KEY="a-long-random-string"
   ```

## Using the site (DM)

1. Log in as the DM.
2. Go to **Positions & Dates** → add each position students can run for, and
   set the five dates (applications open/close, campaigning starts, voting
   opens/closes).
3. Students can now register and, once applications open, submit their
   letter.
4. Go to **Applications** to review each one: download the letter, leave
   feedback, and either **Approve for Interview** or **Reject**.
5. After you've interviewed approved students offline, come back and mark
   each one **Confirm as Candidate** (they'll now appear on the ballot and
   in campaigning) or **Not Selected**.
6. Go to **Live Results** any time to watch votes come in — this page is
   private to you (and anyone you grant access via **Access**).
7. Once voting closes, pick the winning candidate for each position and
   click **Announce Winner** — it instantly appears on every student's
   dashboard.

## Deploying it for real students

This is a standard Flask app, so it deploys anywhere Python apps run:

- **Render / Railway / Fly.io**: connect the repo, set the environment
  variables above, and use `gunicorn app:app` as the start command
  (already in `requirements.txt`).
- **A school server / PythonAnywhere**: same idea — run behind gunicorn or
  another WSGI server, not `python3 app.py` (that's for local testing only,
  as Flask's own warning says).
- **Scale**: SQLite comfortably handles a few thousand students for a
  school election like this. If you expect very heavy simultaneous traffic
  (e.g., everyone voting in the last 5 minutes), consider switching the
  `DB_PATH` connection to PostgreSQL — the SQL is close to standard and
  would need only small changes in `app.py`.
- Put the app behind **HTTPS** (most hosts above do this for you
  automatically) so nicknames and passwords aren't sent in the clear.

## Notes on security

- Passwords are hashed (never stored in plain text) using Werkzeug's
  industry-standard hashing.
- Only the DM can see live results, review applications, or open uploaded
  files.
- Each student can submit exactly one application and cast exactly one vote
  per position — enforced at the database level, not just in the interface.
- This is built for an honest-system school election (like a paper ballot
  box supervised by a teacher), not for adversarial, anonymous internet
  voting — there's no SMS/email verification step. If you want that added
  (e.g., verifying student ID numbers, or one-time codes issued by the
  school), it can be layered on top.

## Optional: AI feedback assistant

You mentioned AI only if it's actually useful — this build doesn't force
one in, since the DM writing personal feedback is more meaningful than a
generated comment. If you'd like, a later version could add an optional
"suggest feedback wording" button in the applications review page using the
Anthropic API — just ask and it can be added without changing anything
else.
