from datetime import date

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)

from auth_utils import log_action, role_required, subscription_required
from db import execute, get_db, query, scalar
from workflow import (chain_people, history, notify, notify_many, record_review,
                      submission_for)

company_bp = Blueprint("company", __name__, url_prefix="/company")

DIFFICULTIES = ["beginner", "intermediate", "advanced"]
MAX_STUDENTS_LIMIT = 20


@company_bp.route("/dashboard")
@role_required("company")
def dashboard():
    cid = g.user["company_id"]
    stats = {
        "posted": scalar("SELECT COUNT(*) FROM projects WHERE company_id=%s", (cid,)),
        "open": scalar(
            "SELECT COUNT(*) FROM projects WHERE company_id=%s AND status='open'", (cid,)),
        "students": scalar(
            """SELECT COUNT(*) FROM assignments a JOIN projects p ON p.id=a.project_id
                WHERE p.company_id=%s""", (cid,)),
        "waiting": scalar(
            """SELECT COUNT(*) FROM submissions s
               JOIN assignments a ON a.id=s.assignment_id
               JOIN projects p ON p.id=a.project_id
              WHERE p.company_id=%s AND s.stage='with_company'""", (cid,)),
        "completed": scalar(
            "SELECT COUNT(*) FROM projects WHERE company_id=%s AND status='completed'", (cid,)),
    }
    company = query("SELECT * FROM companies WHERE id=%s", (cid,), one=True)
    recent = query(
        """SELECT id, title, status, deadline FROM projects
            WHERE company_id=%s ORDER BY created_at DESC LIMIT 5""", (cid,))
    return render_template("company/dashboard.html", stats=stats, company=company,
                           recent=recent, today=date.today())


@company_bp.route("/projects")
@role_required("company")
def projects():
    rows = query(
        """SELECT p.*, f.name AS field_name,
                  (SELECT COUNT(*) FROM assignments a
                    WHERE a.project_id=p.id AND a.status<>'cancelled') AS students_on,
                  (SELECT COUNT(*) FROM submissions s
                     JOIN assignments a2 ON a2.id=s.assignment_id
                    WHERE a2.project_id=p.id AND s.stage='with_company') AS waiting
             FROM projects p JOIN fields f ON f.id=p.field_id
            WHERE p.company_id=%s ORDER BY p.created_at DESC""", (g.user["company_id"],))
    return render_template("company/projects.html", projects=rows, today=date.today())


@company_bp.route("/projects/new", methods=["GET", "POST"])
@role_required("company")
@subscription_required
def create_project():
    fields = query("SELECT id, name FROM fields ORDER BY name")
    skills = query("SELECT id, name FROM skills ORDER BY name")
    form = {"difficulty": "beginner", "max_students": "1"}

    if request.method == "POST":
        form = {k: v.strip() for k, v in request.form.items()}
        picked = request.form.getlist("skills")
        form["skills"] = picked
        publish_now = "publish" in request.form

        errors = []
        if not 5 <= len(form.get("title", "")) <= 255:
            errors.append("Give the project a title between 5 and 255 characters.")
        if len(form.get("description", "")) < 30:
            errors.append("Describe the work in at least 30 characters.")
        if form.get("difficulty") not in DIFFICULTIES:
            errors.append("Choose a difficulty level.")

        valid_fields = {str(f["id"]) for f in fields}
        if form.get("field_id") not in valid_fields:
            errors.append("Choose the field of study this project belongs to.")
        field_id = int(form["field_id"]) if form.get("field_id") in valid_fields else None

        deadline = None
        try:
            deadline = date.fromisoformat(form.get("deadline", ""))
            if deadline <= date.today():
                errors.append("The deadline has to be a future date.")
        except ValueError:
            errors.append("Set a valid deadline.")

        try:
            max_students = int(form.get("max_students") or 1)
        except ValueError:
            max_students = 0
        if not 1 <= max_students <= MAX_STUDENTS_LIMIT:
            errors.append(f"Allow between 1 and {MAX_STUDENTS_LIMIT} students.")

        valid_skills = {str(s["id"]) for s in skills}
        skill_ids = [int(s) for s in picked if s in valid_skills]
        if not skill_ids:
            errors.append("Pick at least one required skill.")

        if errors:
            for m in errors:
                flash(m, "error")
            return render_template("company/create_project.html", form=form,
                                   fields=fields, skills=skills,
                                   difficulties=DIFFICULTIES), 400

        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO projects (company_id, field_id, title, description,
                       deliverables, difficulty, deadline, max_students, status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (g.user["company_id"], field_id, form["title"], form["description"],
                     form.get("deliverables"), form["difficulty"], deadline,
                     max_students, "open" if publish_now else "draft"))
                pid = cur.fetchone()["id"]
                cur.executemany(
                    "INSERT INTO project_skills (project_id, skill_id) VALUES (%s,%s)",
                    [(pid, s) for s in skill_ids])
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        log_action("create_project", "project", pid, form["title"])
        flash("Project published. Lecturers can now take it on." if publish_now
              else "Saved as a draft.", "success")
        return redirect(url_for("company.projects"))

    return render_template("company/create_project.html", form=form, fields=fields,
                           skills=skills, difficulties=DIFFICULTIES)


@company_bp.route("/projects/<int:project_id>/publish", methods=["POST"])
@role_required("company")
@subscription_required
def publish_project(project_id):
    changed = execute(
        """UPDATE projects SET status='open'
            WHERE id=%s AND company_id=%s AND status='draft' RETURNING id""",
        (project_id, g.user["company_id"]), returning=True)
    flash("Project published." if changed else "That project could not be published.",
          "success" if changed else "error")
    return redirect(url_for("company.projects"))


@company_bp.route("/projects/<int:project_id>/close", methods=["POST"])
@role_required("company")
def close_project(project_id):
    changed = execute(
        """UPDATE projects SET status='cancelled'
            WHERE id=%s AND company_id=%s AND status IN ('draft','open') RETURNING id""",
        (project_id, g.user["company_id"]), returning=True)
    flash("Project closed." if changed else "That project could not be closed.",
          "success" if changed else "error")
    return redirect(url_for("company.projects"))


@company_bp.route("/requests")
@company_bp.route("/projects/<int:project_id>/requests")
@role_required("company")
def requests(project_id=None):
    """Lecturers asking to take on this company's briefs."""
    sql = """SELECT r.id, r.message, r.places, r.status, r.created_at,
                    p.id AS project_id, p.title, p.max_students,
                    u.fullname AS lecturer_name, lp.department, lp.staff_no,
                    uni.name AS university_name,
                    (SELECT string_agg(f.name, ', ' ORDER BY f.name)
                       FROM lecturer_fields lf JOIN fields f ON f.id = lf.field_id
                      WHERE lf.lecturer_id = u.id) AS expertise,
                    (SELECT COUNT(*) FROM assignments a
                      WHERE a.request_id = r.id) AS placed
               FROM project_requests r
               JOIN projects p ON p.id = r.project_id
               JOIN users u ON u.id = r.lecturer_id
               LEFT JOIN lecturer_profiles lp ON lp.user_id = u.id
               LEFT JOIN universities uni ON uni.id = lp.university_id
              WHERE p.company_id = %s"""
    params = [g.user["company_id"]]
    if project_id:
        sql += " AND p.id = %s"
        params.append(project_id)
    sql += (" ORDER BY CASE r.status WHEN 'pending' THEN 0 ELSE 1 END,"
            " r.created_at DESC")

    project = None
    if project_id:
        project = query("SELECT * FROM projects WHERE id=%s AND company_id=%s",
                        (project_id, g.user["company_id"]), one=True)
        if not project:
            abort(404)

    return render_template("company/requests.html", requests=query(sql, tuple(params)),
                           project=project)


@company_bp.route("/requests/<int:request_id>/approve", methods=["POST"])
@role_required("company")
@subscription_required
def approve_request(request_id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.project_id, r.lecturer_id, r.places, r.status,
                          p.title, p.max_students
                     FROM project_requests r
                     JOIN projects p ON p.id = r.project_id
                    WHERE r.id=%s AND p.company_id=%s
                    FOR UPDATE OF r""",
                (request_id, g.user["company_id"]))
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                abort(404)
            if row["status"] != "pending":
                conn.rollback()
                flash("That request has already been decided.", "error")
                return redirect(url_for("company.requests"))

            # Places already promised to other lecturers on the same brief.
            cur.execute(
                """SELECT COALESCE(SUM(places),0) AS n FROM project_requests
                    WHERE project_id=%s AND status='approved'""", (row["project_id"],))
            promised = cur.fetchone()["n"]
            if promised + row["places"] > row["max_students"]:
                conn.rollback()
                flash(f"Only {row['max_students'] - promised} place(s) remain on "
                      "this project.", "error")
                return redirect(url_for("company.requests"))

            cur.execute(
                """UPDATE project_requests SET status='approved', decided_at=NOW()
                    WHERE id=%s""", (request_id,))
            cur.execute("UPDATE projects SET status='engaged' WHERE id=%s",
                        (row["project_id"],))
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    notify(row["lecturer_id"],
           f"{g.user['company_name'] or 'The company'} approved you for "
           f"\"{row['title']}\". You can now assign {row['places']} student(s).",
           url_for("lecturer.project_detail", project_id=row["project_id"]))
    log_action("approve_request", "project_request", request_id)
    flash("Approved. The lecturer can now place students.", "success")
    return redirect(url_for("company.requests"))


@company_bp.route("/requests/<int:request_id>/decline", methods=["POST"])
@role_required("company")
@subscription_required
def decline_request(request_id):
    reason = request.form.get("reason", "").strip()
    row = query(
        """SELECT r.id, r.lecturer_id, r.project_id, p.title
             FROM project_requests r JOIN projects p ON p.id = r.project_id
            WHERE r.id=%s AND p.company_id=%s AND r.status='pending'""",
        (request_id, g.user["company_id"]), one=True)
    if not row:
        abort(404)

    execute("""UPDATE project_requests SET status='declined', decided_at=NOW()
                WHERE id=%s""", (request_id,))
    notify(row["lecturer_id"],
           f"Your request for \"{row['title']}\" was declined."
           + (f" {reason}" if reason else ""),
           url_for("lecturer.browse"))
    log_action("decline_request", "project_request", request_id, reason)
    flash("Request declined.", "success")
    return redirect(url_for("company.requests"))


@company_bp.route("/inbox")
@role_required("company")
def inbox():
    """Work forwarded up the chain, waiting for the company's verdict."""
    rows = query(
        """SELECT s.id, s.version, s.repo_url, s.notes, s.stage, s.updated_at,
                  stu.fullname AS student_name, lec.fullname AS lecturer_name,
                  uni.name AS university_name, p.title
             FROM submissions s
             JOIN assignments a ON a.id = s.assignment_id
             JOIN projects p ON p.id = a.project_id
             JOIN users stu ON stu.id = a.student_id
             JOIN users lec ON lec.id = a.lecturer_id
             LEFT JOIN lecturer_profiles lp ON lp.user_id = a.lecturer_id
             LEFT JOIN universities uni ON uni.id = lp.university_id
            WHERE p.company_id = %s AND s.stage IN ('with_company','approved','revision')
            ORDER BY CASE s.stage WHEN 'with_company' THEN 0 ELSE 1 END,
                     s.updated_at DESC""", (g.user["company_id"],))
    return render_template("company/inbox.html", submissions=rows)


@company_bp.route("/submissions/<int:submission_id>", methods=["GET", "POST"])
@role_required("company")
def review(submission_id):
    sub = submission_for(submission_id)
    if not sub or sub["company_id"] != g.user["company_id"]:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action", "")
        comment = request.form.get("comment", "").strip()

        if sub["stage"] != "with_company":
            flash("This submission is not with you at the moment.", "error")
            return redirect(url_for("company.review", submission_id=submission_id))

        people = chain_people(sub["assignment_id"])

        if action == "approve":
            record_review(submission_id, "approve", comment, "approved")
            execute("""UPDATE assignments SET status='completed', completed_at=NOW()
                        WHERE id=%s""", (sub["assignment_id"],))

            # Certificate on approval.
            number = f"SPH-{date.today().year}-{sub['assignment_id']:05d}"
            execute("""INSERT INTO certificates (student_id, project_id, certificate_number)
                       VALUES (%s,%s,%s) ON CONFLICT (certificate_number) DO NOTHING""",
                    (sub["student_id"], sub["project_id"], number))

            # Project completes once every placed student is finished.
            outstanding = scalar(
                """SELECT COUNT(*) FROM assignments
                    WHERE project_id=%s AND status <> 'completed'""", (sub["project_id"],))
            if not outstanding:
                execute("UPDATE projects SET status='completed' WHERE id=%s",
                        (sub["project_id"],))

            notify_many(
                [sub["student_id"], sub["lecturer_id"], people["university_id"]],
                f"{sub['company_name']} approved the work on \"{sub['title']}\". "
                f"Certificate {number} issued to {sub['student_name']}.",
                url_for("company.review", submission_id=submission_id))
            flash("Approved. A certificate has been issued.", "success")

        elif action == "request_correction":
            if len(comment) < 10:
                flash("Say what needs correcting — at least 10 characters.", "error")
                return redirect(url_for("company.review", submission_id=submission_id))
            record_review(submission_id, "request_correction", comment, "revision")
            execute("UPDATE assignments SET status='revision' WHERE id=%s",
                    (sub["assignment_id"],))
            notify_many(
                [sub["student_id"], sub["lecturer_id"], people["university_id"]],
                f"{sub['company_name']} asked for corrections on \"{sub['title']}\".",
                url_for("student.work", assignment_id=sub["assignment_id"]))
            flash("Correction requested. Everyone on the chain has been notified.",
                  "success")
        else:
            abort(400)

        return redirect(url_for("company.inbox"))

    return render_template("company/review.html", sub=sub, timeline=history(submission_id))


@company_bp.route("/projects/<int:project_id>/progress")
@role_required("company")
def progress(project_id):
    project = query("SELECT * FROM projects WHERE id=%s AND company_id=%s",
                    (project_id, g.user["company_id"]), one=True)
    if not project:
        abort(404)

    students = query(
        """SELECT a.id, a.status, u.fullname, sp.github_url, sp.level,
                  uni.name AS university_name, lec.fullname AS lecturer_name,
                  (SELECT COUNT(*) FROM milestones m
                    WHERE m.assignment_id=a.id AND m.status='done') AS done,
                  (SELECT COUNT(*) FROM milestones m WHERE m.assignment_id=a.id) AS total
             FROM assignments a
             JOIN users u ON u.id=a.student_id
             LEFT JOIN student_profiles sp ON sp.user_id=u.id
             LEFT JOIN universities uni ON uni.id=sp.university_id
             JOIN users lec ON lec.id=a.lecturer_id
            WHERE a.project_id=%s ORDER BY u.fullname""", (project_id,))

    return render_template("company/progress.html", project=project, students=students)


@company_bp.route("/notifications")
@role_required("company")
def notifications():
    rows = query(
        "SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 100",
        (g.user["id"],))
    execute("UPDATE notifications SET read_at=NOW() WHERE user_id=%s AND read_at IS NULL",
            (g.user["id"],))
    return render_template("shared/notifications.html", rows=rows)
