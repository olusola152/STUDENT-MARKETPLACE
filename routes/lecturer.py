from datetime import date, timedelta

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)

from auth_utils import log_action, role_required, subscription_required
from db import execute, get_db, query, scalar
from workflow import (chain_people, history, notify, record_review,
                      submission_for)

lecturer_bp = Blueprint("lecturer", __name__, url_prefix="/lecturer")

PHASES = [
    (1, "Requirements and planning"),
    (2, "Build"),
    (3, "Testing and documentation"),
    (4, "Final delivery"),
]


@lecturer_bp.route("/dashboard")
@role_required("lecturer")
def dashboard():
    uid = g.user["id"]
    fields = query(
        """SELECT f.name FROM lecturer_fields lf JOIN fields f ON f.id = lf.field_id
            WHERE lf.lecturer_id = %s ORDER BY f.name""", (uid,))

    stats = {
        "available": scalar(
            "SELECT COUNT(*) FROM projects WHERE status='open' AND deadline >= CURRENT_DATE"),
        "pending": scalar(
            "SELECT COUNT(*) FROM project_requests WHERE lecturer_id=%s AND status='pending'",
            (uid,)),
        "students": scalar("SELECT COUNT(*) FROM assignments WHERE lecturer_id=%s", (uid,)),
        "waiting": scalar(
            """SELECT COUNT(*) FROM submissions s
               JOIN assignments a ON a.id = s.assignment_id
              WHERE a.lecturer_id=%s AND s.stage='with_lecturer'""", (uid,)),
        "overdue": scalar(
            """SELECT COUNT(*) FROM milestones m JOIN assignments a ON a.id=m.assignment_id
                WHERE a.lecturer_id=%s AND m.status<>'done' AND m.due_date < CURRENT_DATE""",
            (uid,)),
    }

    supervising = query(
        """SELECT p.id, p.title, p.deadline, c.name AS company_name,
                  COUNT(DISTINCT a.id) AS student_count,
                  SUM(CASE WHEN m.status='done' THEN 1 ELSE 0 END) AS done,
                  COUNT(m.id) AS total
             FROM assignments a
             JOIN projects p ON p.id = a.project_id
             JOIN companies c ON c.id = p.company_id
             LEFT JOIN milestones m ON m.assignment_id = a.id
            WHERE a.lecturer_id = %s
            GROUP BY p.id, c.name ORDER BY p.deadline""", (uid,))

    requests_out = query(
        """SELECT r.id, r.status, r.places, r.created_at, p.id AS project_id, p.title,
                  c.name AS company_name
             FROM project_requests r
             JOIN projects p ON p.id = r.project_id
             JOIN companies c ON c.id = p.company_id
            WHERE r.lecturer_id = %s ORDER BY r.created_at DESC LIMIT 10""", (uid,))

    return render_template("lecturer/dashboard.html", stats=stats, fields=fields,
                           supervising=supervising, requests_out=requests_out)


@lecturer_bp.route("/projects")
@role_required("lecturer")
def browse():
    """Every open brief. A lecturer may ask for any of them."""
    search = request.args.get("q", "").strip()
    mine_only = request.args.get("mine") == "1"

    sql = """SELECT p.id, p.title, p.description, p.difficulty, p.deadline,
                    p.max_students, f.name AS field_name, c.name AS company_name,
                    (SELECT string_agg(s.name, ', ' ORDER BY s.name)
                       FROM project_skills ps JOIN skills s ON s.id = ps.skill_id
                      WHERE ps.project_id = p.id) AS skill_list,
                    (SELECT COUNT(*) FROM assignments a WHERE a.project_id = p.id) AS taken,
                    (SELECT COUNT(*) FROM assignments a
                      WHERE a.project_id = p.id AND a.lecturer_id = %s) AS mine,
                    (SELECT status FROM project_requests r
                      WHERE r.project_id = p.id AND r.lecturer_id = %s) AS request_status,
                    (p.field_id IN (SELECT field_id FROM lecturer_fields
                                     WHERE lecturer_id = %s)) AS in_my_field
               FROM projects p
               JOIN fields f ON f.id = p.field_id
               JOIN companies c ON c.id = p.company_id
              WHERE p.status IN ('open','engaged') AND p.deadline >= CURRENT_DATE"""
    params = [g.user["id"], g.user["id"], g.user["id"]]
    if search:
        sql += " AND (p.title ILIKE %s OR c.name ILIKE %s)"
        params += [f"%{search}%", f"%{search}%"]
    if mine_only:
        sql += (" AND p.field_id IN (SELECT field_id FROM lecturer_fields"
                " WHERE lecturer_id = %s)")
        params.append(g.user["id"])
    sql += " ORDER BY in_my_field DESC, p.created_at DESC"

    return render_template("lecturer/projects.html", projects=query(sql, tuple(params)),
                           search=search, mine_only=mine_only)


@lecturer_bp.route("/projects/<int:project_id>")
@role_required("lecturer")
def project_detail(project_id):
    project = query(
        """SELECT p.*, f.name AS field_name, c.name AS company_name, c.website,
                  (SELECT string_agg(s.name, ', ' ORDER BY s.name)
                     FROM project_skills ps JOIN skills s ON s.id = ps.skill_id
                    WHERE ps.project_id = p.id) AS skill_list,
                  (SELECT COUNT(*) FROM assignments a WHERE a.project_id = p.id) AS taken
             FROM projects p JOIN fields f ON f.id = p.field_id
             JOIN companies c ON c.id = p.company_id
            WHERE p.id = %s AND p.status IN ('open','engaged','completed')""",
        (project_id,), one=True)
    if not project:
        abort(404)

    my_request = query(
        "SELECT * FROM project_requests WHERE project_id=%s AND lecturer_id=%s",
        (project_id, g.user["id"]), one=True)

    students = query(
        """SELECT u.id, u.fullname, sp.level, sp.department, sp.matric_no,
                  sp.github_url, sp.available, f.name AS course,
                  (SELECT COUNT(*) FROM assignments a2
                    WHERE a2.student_id = u.id
                      AND a2.status IN ('assigned','in_progress','revision','submitted'))
                    AS active_jobs,
                  EXISTS (SELECT 1 FROM assignments a3
                           WHERE a3.student_id = u.id AND a3.project_id = %s) AS on_project
             FROM users u
             JOIN student_profiles sp ON sp.user_id = u.id
             JOIN lecturer_profiles lp ON lp.university_id = sp.university_id
             LEFT JOIN fields f ON f.id = sp.field_id
            WHERE lp.user_id = %s AND u.role='student' AND u.status='active'
            ORDER BY (sp.field_id = %s) DESC, u.fullname""",
        (project_id, g.user["id"], project["field_id"]))

    placed = query(
        """SELECT a.id, a.status, u.fullname,
                  (SELECT COUNT(*) FROM milestones m
                    WHERE m.assignment_id=a.id AND m.status='done') AS done,
                  (SELECT COUNT(*) FROM milestones m WHERE m.assignment_id=a.id) AS total
             FROM assignments a JOIN users u ON u.id = a.student_id
            WHERE a.project_id=%s AND a.lecturer_id=%s ORDER BY u.fullname""",
        (project_id, g.user["id"]))

    return render_template("lecturer/project_detail.html", project=project,
                           students=students, placed=placed, my_request=my_request,
                           today=date.today())


@lecturer_bp.route("/projects/<int:project_id>/request", methods=["POST"])
@role_required("lecturer")
@subscription_required
def request_project(project_id):
    """Ask the company for the brief. Nobody is placed until they approve."""
    project = query(
        """SELECT p.*, c.user_id AS company_user_id, c.name AS company_name
             FROM projects p JOIN companies c ON c.id = p.company_id
            WHERE p.id=%s AND p.status IN ('open','engaged')""", (project_id,), one=True)
    if not project:
        abort(404)

    message = request.form.get("message", "").strip()
    try:
        places = int(request.form.get("places") or 1)
    except ValueError:
        places = 0

    if len(message) < 40:
        flash("Write at least 40 characters on how your students will approach it.", "error")
        return redirect(url_for("lecturer.project_detail", project_id=project_id))
    if not 1 <= places <= project["max_students"]:
        flash(f"Ask for between 1 and {project['max_students']} places.", "error")
        return redirect(url_for("lecturer.project_detail", project_id=project_id))

    created = execute(
        """INSERT INTO project_requests (project_id, lecturer_id, message, places)
           VALUES (%s,%s,%s,%s)
           ON CONFLICT (project_id, lecturer_id) DO NOTHING RETURNING id""",
        (project_id, g.user["id"], message, places), returning=True)

    if created:
        uni = g.get("university")
        notify(project["company_user_id"],
               f"{g.user['fullname']}{' of ' + uni['name'] if uni else ''} asked to "
               f"take on \"{project['title']}\" for {places} student(s).",
               url_for("company.requests", project_id=project_id))
        log_action("request_project", "project", project_id)
        flash("Request sent. The company decides before you can place students.", "success")
    else:
        flash("You have already asked for this project.", "error")
    return redirect(url_for("lecturer.project_detail", project_id=project_id))


@lecturer_bp.route("/projects/<int:project_id>/assign", methods=["POST"])
@role_required("lecturer")
@subscription_required
def assign(project_id):
    project = query(
        "SELECT * FROM projects WHERE id=%s AND status IN ('open','engaged')",
        (project_id,), one=True)
    if not project:
        abort(404)

    approved = query(
        """SELECT * FROM project_requests
            WHERE project_id=%s AND lecturer_id=%s AND status='approved'""",
        (project_id, g.user["id"]), one=True)
    if not approved:
        flash("The company has not approved you for this project yet.", "error")
        return redirect(url_for("lecturer.project_detail", project_id=project_id))

    picked = request.form.getlist("students")
    conn = get_db()
    placed = 0
    try:
        with conn.cursor() as cur:
            # Lock the project first, then count — two lecturers assigning at once
            # cannot both slip past the capacity check.
            cur.execute("SELECT max_students FROM projects WHERE id=%s FOR UPDATE",
                        (project_id,))
            cap = cur.fetchone()["max_students"]
            cur.execute("SELECT COUNT(*) AS n FROM assignments WHERE project_id=%s",
                        (project_id,))
            room = cap - cur.fetchone()["n"]

            # Never more than the places this lecturer was granted.
            cur.execute("SELECT COUNT(*) AS n FROM assignments WHERE request_id=%s",
                        (approved["id"],))
            room = min(room, approved["places"] - cur.fetchone()["n"])

            for sid in picked:
                if room <= 0:
                    break
                # Must be this lecturer's own student, at their own institution.
                cur.execute(
                    """SELECT u.id FROM users u
                         JOIN student_profiles sp ON sp.user_id = u.id
                         JOIN lecturer_profiles lp ON lp.university_id = sp.university_id
                        WHERE u.id=%s AND u.role='student' AND u.status='active'
                          AND lp.user_id=%s""", (sid, g.user["id"]))
                if cur.fetchone() is None:
                    continue

                cur.execute(
                    """INSERT INTO assignments (request_id, project_id, student_id, lecturer_id)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (project_id, student_id) DO NOTHING RETURNING id""",
                    (approved["id"], project_id, sid, g.user["id"]))
                new = cur.fetchone()
                if not new:
                    continue

                span = max((project["deadline"] - date.today()).days, 4)
                for phase, title in PHASES:
                    due = date.today() + timedelta(days=int(span * phase / len(PHASES)))
                    cur.execute(
                        """INSERT INTO milestones (assignment_id, phase, title, due_date)
                           VALUES (%s,%s,%s,%s)""", (new["id"], phase, title, due))

                cur.execute(
                    "INSERT INTO notifications (user_id, body, url) VALUES (%s,%s,%s)",
                    (int(sid),
                     f"{g.user['fullname']} assigned you to \"{project['title']}\". "
                     f"Due {project['deadline'].strftime('%d %b %Y')}.",
                     url_for("student.work", assignment_id=new["id"])))
                placed += 1
                room -= 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    log_action("assign_students", "project", project_id, f"{placed} placed")
    flash(f"{placed} student(s) assigned. They have been notified." if placed
          else "No students were assigned — check the places you were granted.",
          "success" if placed else "error")
    return redirect(url_for("lecturer.project_detail", project_id=project_id))


@lecturer_bp.route("/students")
@role_required("lecturer")
def students():
    """Students registered under this lecturer's institution.

    Those studying one of the lecturer's own areas of expertise come first.
    """
    uid = g.user["id"]
    show = request.args.get("show", "mine")
    search = request.args.get("q", "").strip()

    sql = """SELECT u.id, u.fullname, u.email, sp.level, sp.department, sp.matric_no,
                    sp.github_url, sp.available, f.name AS course, f.faculty,
                    (f.id IN (SELECT field_id FROM lecturer_fields WHERE lecturer_id=%s))
                      AS my_field,
                    (SELECT COUNT(*) FROM assignments a
                      WHERE a.student_id=u.id
                        AND a.status IN ('assigned','in_progress','revision','submitted'))
                      AS active_jobs,
                    (SELECT COUNT(*) FROM assignments a
                      WHERE a.student_id=u.id AND a.lecturer_id=%s) AS with_me,
                    (SELECT COUNT(*) FROM certificates c WHERE c.student_id=u.id)
                      AS certificates
               FROM users u
               JOIN student_profiles sp ON sp.user_id = u.id
               JOIN lecturer_profiles lp ON lp.university_id = sp.university_id
               LEFT JOIN fields f ON f.id = sp.field_id
              WHERE lp.user_id = %s AND u.role='student' AND u.status='active'"""
    params = [uid, uid, uid]

    if show == "mine":
        sql += (" AND sp.field_id IN (SELECT field_id FROM lecturer_fields"
                " WHERE lecturer_id=%s)")
        params.append(uid)
    if search:
        sql += " AND (u.fullname ILIKE %s OR sp.matric_no ILIKE %s)"
        params += [f"%{search}%", f"%{search}%"]
    sql += " ORDER BY my_field DESC, f.faculty NULLS LAST, u.fullname"

    fields = query(
        """SELECT f.name, f.faculty FROM lecturer_fields lf
             JOIN fields f ON f.id = lf.field_id
            WHERE lf.lecturer_id=%s ORDER BY f.name""", (uid,))

    return render_template("lecturer/students.html", students=query(sql, tuple(params)),
                           fields=fields, show=show, search=search)


@lecturer_bp.route("/inbox")
@role_required("lecturer")
def inbox():
    rows = query(
        """SELECT s.id, s.version, s.repo_url, s.notes, s.stage, s.submitted_at,
                  s.updated_at, u.fullname AS student_name, p.title,
                  c.name AS company_name
             FROM submissions s
             JOIN assignments a ON a.id = s.assignment_id
             JOIN users u ON u.id = a.student_id
             JOIN projects p ON p.id = a.project_id
             JOIN companies c ON c.id = p.company_id
            WHERE a.lecturer_id = %s
            ORDER BY CASE s.stage WHEN 'with_lecturer' THEN 0 ELSE 1 END,
                     s.updated_at DESC""", (g.user["id"],))
    return render_template("lecturer/inbox.html", submissions=rows)


@lecturer_bp.route("/submissions/<int:submission_id>", methods=["GET", "POST"])
@role_required("lecturer")
def review(submission_id):
    sub = submission_for(submission_id)
    if not sub or sub["lecturer_id"] != g.user["id"]:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action", "")
        comment = request.form.get("comment", "").strip()

        if sub["stage"] != "with_lecturer":
            flash("This submission has already moved on.", "error")
            return redirect(url_for("lecturer.review", submission_id=submission_id))

        people = chain_people(sub["assignment_id"])

        if action == "forward":
            record_review(submission_id, "forward", comment, "with_university")
            notify(people["university_id"],
                   f"{sub['lecturer_name']} forwarded {sub['student_name']}'s work on "
                   f"\"{sub['title']}\" for your review.",
                   url_for("university.review", submission_id=submission_id))
            notify(sub["student_id"],
                   f"Your supervisor passed your work on \"{sub['title']}\" to the university.",
                   url_for("student.work", assignment_id=sub["assignment_id"]))
            flash("Sent to the university.", "success")

        elif action == "request_correction":
            if len(comment) < 10:
                flash("Say what needs correcting — at least 10 characters.", "error")
                return redirect(url_for("lecturer.review", submission_id=submission_id))
            record_review(submission_id, "request_correction", comment, "revision")
            execute("UPDATE assignments SET status='revision' WHERE id=%s",
                    (sub["assignment_id"],))
            notify(sub["student_id"],
                   f"{g.user['fullname']} asked for corrections on \"{sub['title']}\".",
                   url_for("student.work", assignment_id=sub["assignment_id"]))
            flash("Correction requested. The student has been notified.", "success")
        else:
            abort(400)

        return redirect(url_for("lecturer.inbox"))

    return render_template("lecturer/review.html", sub=sub, timeline=history(submission_id))


@lecturer_bp.route("/students/<int:assignment_id>/monitor")
@role_required("lecturer")
def monitor(assignment_id):
    row = query(
        """SELECT a.*, u.fullname AS student_name, sp.github_url, sp.level,
                  p.title, p.deadline, c.name AS company_name
             FROM assignments a
             JOIN users u ON u.id = a.student_id
             LEFT JOIN student_profiles sp ON sp.user_id = u.id
             JOIN projects p ON p.id = a.project_id
             JOIN companies c ON c.id = p.company_id
            WHERE a.id=%s AND a.lecturer_id=%s""",
        (assignment_id, g.user["id"]), one=True)
    if not row:
        abort(404)

    return render_template(
        "lecturer/monitor.html", row=row,
        milestones=query("SELECT * FROM milestones WHERE assignment_id=%s ORDER BY phase",
                         (assignment_id,)),
        submissions=query("SELECT * FROM submissions WHERE assignment_id=%s ORDER BY version DESC",
                          (assignment_id,)),
        today=date.today())


@lecturer_bp.route("/milestones/<int:milestone_id>", methods=["POST"])
@role_required("lecturer")
def update_milestone(milestone_id):
    status = request.form.get("status", "")
    note = request.form.get("note", "").strip()
    if status not in ("pending", "in_progress", "done"):
        abort(400)

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE milestones m SET status=%s, note=%s, updated_at=NOW()
                 FROM assignments a
                WHERE m.id=%s AND a.id=m.assignment_id AND a.lecturer_id=%s
            RETURNING m.assignment_id""",
            (status, note or None, milestone_id, g.user["id"]))
        row = cur.fetchone()
    conn.commit()
    if row is None:
        abort(404)

    flash("Phase updated.", "success")
    return redirect(url_for("lecturer.monitor", assignment_id=row["assignment_id"]))


@lecturer_bp.route("/profile", methods=["GET", "POST"])
@role_required("lecturer")
def profile():
    uid = g.user["id"]
    all_fields = query("SELECT id, name, faculty FROM fields ORDER BY faculty, name")

    if request.method == "POST":
        picked = request.form.getlist("fields")
        valid = {str(f["id"]) for f in all_fields}
        field_ids = [int(f) for f in picked if f in valid]

        if not field_ids:
            flash("Keep at least one area of expertise.", "error")
            return redirect(url_for("lecturer.profile"))

        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE lecturer_profiles SET department=%s, staff_no=%s, phone=%s
                        WHERE user_id=%s""",
                    (request.form.get("department", "").strip() or None,
                     request.form.get("staff_no", "").strip() or None,
                     request.form.get("phone", "").strip() or None, uid))
                cur.execute("DELETE FROM lecturer_fields WHERE lecturer_id=%s", (uid,))
                cur.executemany(
                    "INSERT INTO lecturer_fields (lecturer_id, field_id) VALUES (%s,%s)",
                    [(uid, f) for f in field_ids])
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        flash("Profile updated.", "success")
        return redirect(url_for("lecturer.profile"))

    prof = query(
        """SELECT lp.*, u.name AS university_name
             FROM lecturer_profiles lp JOIN universities u ON u.id=lp.university_id
            WHERE lp.user_id=%s""", (uid,), one=True) or {}
    mine = {r["field_id"] for r in
            query("SELECT field_id FROM lecturer_fields WHERE lecturer_id=%s", (uid,))}
    supervised = query(
        """SELECT p.title, c.name AS company_name, u.fullname AS student_name, a.status
             FROM assignments a JOIN projects p ON p.id=a.project_id
             JOIN companies c ON c.id=p.company_id JOIN users u ON u.id=a.student_id
            WHERE a.lecturer_id=%s ORDER BY a.assigned_at DESC LIMIT 20""", (uid,))

    return render_template("lecturer/profile.html", prof=prof, all_fields=all_fields,
                           mine=mine, supervised=supervised)


@lecturer_bp.route("/notifications")
@role_required("lecturer")
def notifications():
    rows = query(
        "SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 100",
        (g.user["id"],))
    execute("UPDATE notifications SET read_at=NOW() WHERE user_id=%s AND read_at IS NULL",
            (g.user["id"],))
    return render_template("shared/notifications.html", rows=rows)
