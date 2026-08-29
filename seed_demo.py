"""Fill the database with a working example so every page has something on it.

    python seed_demo.py

Creates one university (subscription already active), two lecturers, six
students, two companies and four projects — one of them already running, with
students placed, phases part-completed and work submitted.

Safe to re-run: it removes anything it created before starting again. It never
touches accounts it did not create, so your own admin survives.
"""

from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from app import app
from auth_utils import generate_passcode, slugify
from db import execute, get_db, query

PASSWORD = "demo1234"
MARK = "@demo.marketplace"          # every seeded email ends with this

UNIVERSITY = ("University of Lagos", "unilag", "Lagos", "https://unilag.edu.ng")

LECTURERS = [
    ("Dr Bello Ajayi", "bello" + MARK, "Computer Science",
     ["Software Engineering", "Computer Science"]),
    ("Prof Ngozi Umeh", "ngozi" + MARK, "Civil Engineering",
     ["Civil Engineering"]),
]

STUDENTS = [
    ("Ada Obi", "ada" + MARK, "Software Engineering", "400", "CSC/19/001",
     "https://github.com/adaobi"),
    ("Chidi Eze", "chidi" + MARK, "Software Engineering", "300", "CSC/20/014",
     "https://github.com/chidieze"),
    ("Funke Adeyemi", "funke" + MARK, "Software Engineering", "400", "CSC/19/027",
     "https://github.com/funkeadeyemi"),
    ("Emeka Nwosu", "emeka" + MARK, "Computer Science", "300", "CSC/20/033", None),
    ("Aisha Bello", "aisha" + MARK, "Civil Engineering", "400", "CVE/19/008", None),
    ("Tolu Salami", "tolu" + MARK, "Civil Engineering", "300", "CVE/20/019", None),
]

COMPANIES = [
    ("Pettof Technologies", "hr" + MARK, "https://pettof.ng",
     "Ride-hailing and logistics, Lagos."),
    ("Titocasa Furniture", "jobs" + MARK, "https://titocasa.ng",
     "Furniture retail with a growing online arm."),
]

PROJECTS = [
    # (company index, field, title, description, days, places, status)
    (0, "Software Engineering", "Driver earnings dashboard",
     "Build a web dashboard where drivers see daily and weekly earnings, trip "
     "counts and payout history. Needs a clean mobile layout — most drivers "
     "are on phones. Data comes from a REST API we provide.",
     45, 2, "open"),
    (0, "Software Engineering", "Pharmacy inventory system",
     "Stock tracking with expiry alerts, low-stock warnings and a simple "
     "reporting screen for a chain of six pharmacies. Include the database "
     "schema and seed data.",
     40, 2, "running"),
    (1, "Software Engineering", "Furniture catalogue redesign",
     "Rebuild our product catalogue as a fast static site with search and "
     "filtering by room, material and price. Photography and copy provided.",
     30, 3, "open"),
    (1, "Civil Engineering", "Warehouse load assessment",
     "Assess floor loading for a proposed mezzanine in our Ikeja warehouse and "
     "produce a written report with calculations and drawings.",
     60, 1, "draft"),
]

PHASES = [(1, "Requirements and planning"), (2, "Build"),
          (3, "Testing and documentation"), (4, "Final delivery")]


def wipe():
    """Remove only what a previous run created."""
    execute("""DELETE FROM users WHERE email LIKE %s""", ("%" + MARK,))
    execute("""DELETE FROM universities WHERE name = %s""", (UNIVERSITY[0],))


def field_id(name):
    row = query("SELECT id FROM fields WHERE name = %s", (name,), one=True)
    if not row:
        raise SystemExit(f"Field '{name}' is missing — run database/schema.sql first.")
    return row["id"]


def make_user(cur, fullname, email, role):
    cur.execute(
        """INSERT INTO users (fullname, email, password, role)
           VALUES (%s,%s,%s,%s) RETURNING id""",
        (fullname, email, generate_password_hash(PASSWORD), role))
    return cur.fetchone()["id"]


with app.app_context():
    if not query("SELECT id FROM fields LIMIT 1", one=True):
        raise SystemExit("No curriculum fields found. Run database/schema.sql first.")

    wipe()
    conn = get_db()
    codes = []

    with conn.cursor() as cur:
        # --- university, already subscribed ---
        name, _, city, website = UNIVERSITY
        taken = {r["slug"] for r in query("SELECT slug FROM universities")}
        admin_id = make_user(cur, "Olusola Adegboye", "registrar" + MARK, "university")
        cur.execute(
            """INSERT INTO universities
               (name, slug, website, city, contact_email, admin_user_id,
                subscription_status, subscribed_until)
               VALUES (%s,%s,%s,%s,%s,%s,'active',%s) RETURNING id, slug""",
            (name, slugify(name, taken), website, city, "registrar" + MARK,
             admin_id, date.today() + timedelta(days=365)))
        uni = cur.fetchone()

        # --- two spare passcodes, so the flow can be tried by hand ---
        for label in ("Faculty of Science", "Faculty of Engineering"):
            code = generate_passcode()
            codes.append((code, label))
            cur.execute(
                """INSERT INTO passcodes (university_id, code_hash, code_hint,
                   label, issued_by, expires_at)
                   VALUES (%s,%s,%s,%s,%s, NOW() + INTERVAL '14 days')""",
                (uni["id"], generate_password_hash(code), code[:4], label, admin_id))

        # --- lecturers ---
        lecturer_ids = {}
        for fullname, email, dept, expertise in LECTURERS:
            uid = make_user(cur, fullname, email, "lecturer")
            cur.execute(
                """INSERT INTO lecturer_profiles (user_id, university_id, department)
                   VALUES (%s,%s,%s)""", (uid, uni["id"], dept))
            for f in expertise:
                cur.execute(
                    "INSERT INTO lecturer_fields (lecturer_id, field_id) VALUES (%s,%s)",
                    (uid, field_id(f)))
            lecturer_ids[email] = uid

        # --- students ---
        student_ids = {}
        for fullname, email, course, level, matric, github in STUDENTS:
            uid = make_user(cur, fullname, email, "student")
            cur.execute(
                """INSERT INTO student_profiles (user_id, university_id, field_id,
                   department, level, matric_no, github_url)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (uid, uni["id"], field_id(course), course, level, matric, github))
            student_ids[email] = uid

        # --- companies, already subscribed ---
        company_ids = []
        for cname, email, site, blurb in COMPANIES:
            uid = make_user(cur, "Account holder", email, "company")
            cur.execute(
                """INSERT INTO companies (user_id, name, description, website,
                   subscription_status, subscribed_until)
                   VALUES (%s,%s,%s,%s,'active',%s) RETURNING id""",
                (uid, cname, blurb, site, date.today() + timedelta(days=365)))
            company_ids.append(cur.fetchone()["id"])

        # --- projects ---
        skills = {r["name"]: r["id"] for r in query("SELECT id, name FROM skills")}
        running = None
        for ci, course, title, desc, days, places, state in PROJECTS:
            status = "open" if state == "running" else state
            cur.execute(
                """INSERT INTO projects (company_id, field_id, title, description,
                   difficulty, deadline, max_students, status)
                   VALUES (%s,%s,%s,%s,'intermediate',%s,%s,%s) RETURNING id""",
                (company_ids[ci], field_id(course), title, desc,
                 date.today() + timedelta(days=days), places, status))
            pid = cur.fetchone()["id"]
            for s in ("Python", "Flask", "PostgreSQL", "JavaScript"):
                if s in skills:
                    cur.execute(
                        "INSERT INTO project_skills VALUES (%s,%s)", (pid, skills[s]))
            if state == "running":
                running = (pid, days)

        # --- one project taken all the way through ---
        if running:
            pid, days = running
            lec = lecturer_ids["bello" + MARK]
            cur.execute(
                """INSERT INTO project_requests (project_id, lecturer_id, message,
                   places, status, decided_at)
                   VALUES (%s,%s,%s,2,'approved',NOW()) RETURNING id""",
                (pid, lec,
                 "My 400 level software engineering class will take this on as their "
                 "semester practical. Two students, weekly supervision."))
            req_id = cur.fetchone()["id"]
            cur.execute("UPDATE projects SET status='engaged' WHERE id=%s", (pid,))

            for i, email in enumerate(["ada" + MARK, "chidi" + MARK]):
                cur.execute(
                    """INSERT INTO assignments (request_id, project_id, student_id,
                       lecturer_id, status) VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                    (req_id, pid, student_ids[email], lec,
                     "submitted" if i == 0 else "assigned"))
                asg = cur.fetchone()["id"]

                for phase, ptitle in PHASES:
                    due = date.today() + timedelta(days=int(days * phase / 4))
                    # First student is a phase ahead, so progress bars differ.
                    done = phase == 1 or (i == 0 and phase == 2)
                    cur.execute(
                        """INSERT INTO milestones (assignment_id, phase, title,
                           due_date, status, note)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (asg, phase, ptitle, due, "done" if done else "pending",
                         "Approved in supervision meeting." if done else None))

                if i == 0:
                    cur.execute(
                        """INSERT INTO submissions (assignment_id, version,
                           repo_url, notes, stage)
                           VALUES (%s,1,%s,%s,'with_lecturer') RETURNING id""",
                        (asg, "https://github.com/adaobi/pharmacy-inventory",
                         "First working version — stock in and out, expiry alerts "
                         "still to come."))
                    sub_id = cur.fetchone()["id"]
                    cur.execute(
                        """INSERT INTO notifications (user_id, body, url)
                           VALUES (%s,%s,%s)""",
                        (lec, "Ada Obi submitted work on \"Pharmacy inventory "
                              "system\" (version 1).", "/lecturer/inbox"))

        # A second lecturer asks for an open brief, left pending on purpose.
        with conn.cursor() as cur:
            cur.execute("""SELECT p.id, p.title FROM projects p
                            WHERE p.status='open'
                              AND NOT EXISTS (SELECT 1 FROM project_requests r
                                               WHERE r.project_id=p.id AND r.lecturer_id=%s)
                            ORDER BY p.id LIMIT 1""", (lecturer_ids["bello" + MARK],))
            open_p = cur.fetchone()
            if open_p:
                cur.execute(
                    """INSERT INTO project_requests (project_id, lecturer_id, message, places)
                       VALUES (%s,%s,%s,2)
                       ON CONFLICT (project_id, lecturer_id) DO NOTHING""",
                    (open_p["id"], lecturer_ids["bello" + MARK],
                     "This fits my 300 level web development module. I would put two "
                     "students on it with weekly check-ins."))
                cur.execute(
                    """INSERT INTO notifications (user_id, body, url)
                       SELECT c.user_id, %s, '/company/requests' FROM projects p
                       JOIN companies c ON c.id = p.company_id WHERE p.id = %s""",
                    (f"Dr Bello Ajayi asked to take on \"{open_p['title']}\" "
                     "for 2 student(s).", open_p["id"]))

    conn.commit()

print("\nDemo data loaded.\n")
print(f"  Portal:   /u/{uni['slug']}")
print(f"  Password for every demo account: {PASSWORD}\n")
print("  University admin  registrar" + MARK)
print("  Lecturers         " + ", ".join(e for _, e, _, _ in LECTURERS))
print("  Students          " + ", ".join(e for _, e, _, _, _, _ in STUDENTS))
print("  Companies         " + ", ".join(e for _, e, _, _ in COMPANIES))
print("\n  Unused lecturer passcodes:")
for code, label in codes:
    print(f"    {code}   ({label})")
print("\nRe-running this script removes and rebuilds only these accounts.\n")
