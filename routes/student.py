from datetime import date

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)

from auth_utils import role_required, subscription_required
from db import execute, get_db, query, scalar
from workflow import chain_people, history, notify, STAGE_LABEL

student_bp = Blueprint("student", __name__, url_prefix="/student")


@student_bp.route("/dashboard")
@role_required("student")
def dashboard():
    uid = g.user["id"]
    stats = {
        "available": scalar(
            "SELECT COUNT(*) FROM projects WHERE status='open' AND deadline >= CURRENT_DATE"),
        "active": scalar(
            """SELECT COUNT(*) FROM assignments
                WHERE student_id=%s AND status IN ('assigned','in_progress','revision')""",
            (uid,)),
        "completed": scalar(
            "SELECT COUNT(*) FROM assignments WHERE student_id=%s AND status='completed'",
            (uid,)),
        "certificates": scalar("SELECT COUNT(*) FROM certificates WHERE student_id=%s", (uid,)),
        "overdue": scalar(
            """SELECT COUNT(*) FROM milestones m JOIN assignments a ON a.id=m.assignment_id
                WHERE a.student_id=%s AND m.status<>'done' AND m.due_date < CURRENT_DATE""",
            (uid,)),
    }

    profile = query(
        """SELECT sp.*, f.name AS field_name, u.name AS university_name
             FROM student_profiles sp
             LEFT JOIN fields f ON f.id=sp.field_id
             LEFT JOIN universities u ON u.id=sp.university_id
            WHERE sp.user_id=%s""", (uid,), one=True) or {}

    active = query(
        """SELECT a.id, a.status, p.title, p.deadline, c.name AS company_name,
                  lec.fullname AS lecturer_name,
                  (SELECT COUNT(*) FROM milestones m
                    WHERE m.assignment_id=a.id AND m.status='done') AS done,
                  (SELECT COUNT(*) FROM milestones m WHERE m.assignment_id=a.id) AS total,
                  (SELECT stage FROM submissions s WHERE s.assignment_id=a.id
                    ORDER BY version DESC LIMIT 1) AS stage
             FROM assignments a
             JOIN projects p ON p.id=a.project_id
             JOIN companies c ON c.id=p.company_id
             JOIN users lec ON lec.id=a.lecturer_id
            WHERE a.student_id=%s AND a.status NOT IN ('completed','cancelled')
            ORDER BY p.deadline""", (uid,))

    return render_template("student/dashboard.html", stats=stats, profile=profile,
                           active=active, stage_label=STAGE_LABEL)


@student_bp.route("/notifications")
@role_required("student")
def notifications():
    rows = query(
        "SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 100",
        (g.user["id"],))
    execute("UPDATE notifications SET read_at=NOW() WHERE user_id=%s AND read_at IS NULL",
            (g.user["id"],))
    return render_template("shared/notifications.html", rows=rows)


@student_bp.route("/projects")
@role_required("student")
def browse():
    """Read-only. A lecturer decides who works on what."""
    search = request.args.get("q", "").strip()
    sql = """SELECT p.id, p.title, p.description, p.difficulty, p.deadline,
                    p.max_students, f.name AS field_name, c.name AS company_name,
                    (SELECT string_agg(s.name, ', ' ORDER BY s.name)
                       FROM project_skills ps JOIN skills s ON s.id=ps.skill_id
                      WHERE ps.project_id=p.id) AS skill_list
               FROM projects p JOIN fields f ON f.id=p.field_id
               JOIN companies c ON c.id=p.company_id
              WHERE p.status='open' AND p.deadline >= CURRENT_DATE"""
    params = []
    if search:
        sql += " AND (p.title ILIKE %s OR p.description ILIKE %s)"
        params += [f"%{search}%", f"%{search}%"]
    sql += " ORDER BY p.created_at DESC"
    return render_template("student/projects.html", projects=query(sql, tuple(params)),
                           search=search)


@student_bp.route("/projects/<int:project_id>")
@role_required("student")
def project_detail(project_id):
    project = query(
        """SELECT p.*, f.name AS field_name, c.name AS company_name, c.website,
                  (SELECT string_agg(s.name, ', ' ORDER BY s.name)
                     FROM project_skills ps JOIN skills s ON s.id=ps.skill_id
                    WHERE ps.project_id=p.id) AS skill_list
             FROM projects p JOIN fields f ON f.id=p.field_id
             JOIN companies c ON c.id=p.company_id
            WHERE p.id=%s AND p.status IN ('open','engaged','completed')""",
        (project_id,), one=True)
    if not project:
        abort(404)
    return render_template("student/project_detail.html", project=project,
                           today=date.today())


@student_bp.route("/work/<int:assignment_id>", methods=["GET", "POST"])
@role_required("student")
def work(assignment_id):
    row = query(
        """SELECT a.*, p.title, p.description, p.deliverables, p.deadline,
                  c.name AS company_name, lec.fullname AS lecturer_name
             FROM assignments a
             JOIN projects p ON p.id=a.project_id
             JOIN companies c ON c.id=p.company_id
             JOIN users lec ON lec.id=a.lecturer_id
            WHERE a.id=%s AND a.student_id=%s""",
        (assignment_id, g.user["id"]), one=True)
    if not row:
        abort(404)

    if request.method == "POST":
        if row["status"] == "pending":
            flash("The company has not approved this placement yet.", "error")
            return redirect(url_for("student.work", assignment_id=assignment_id))
        if row["status"] == "cancelled":
            flash("This placement was declined by the company.", "error")
            return redirect(url_for("student.dashboard"))

        repo_url = request.form.get("repo_url", "").strip()
        notes = request.form.get("notes", "").strip()

        if not repo_url.startswith(("https://github.com/", "https://gitlab.com/")):
            flash("Paste a GitHub or GitLab repository link.", "error")
            return redirect(url_for("student.work", assignment_id=assignment_id))

        # Nothing to send while the chain still holds the last version.
        held = scalar(
            """SELECT COUNT(*) FROM submissions WHERE assignment_id=%s
                AND stage IN ('with_lecturer','with_university','with_company')""",
            (assignment_id,))
        if held:
            flash("Your last submission is still being reviewed.", "error")
            return redirect(url_for("student.work", assignment_id=assignment_id))

        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(version),0)+1 AS v FROM submissions WHERE assignment_id=%s",
                    (assignment_id,))
                version = cur.fetchone()["v"]
                cur.execute(
                    """INSERT INTO submissions (assignment_id, version, repo_url, notes)
                       VALUES (%s,%s,%s,%s)""", (assignment_id, version, repo_url, notes))
                cur.execute("UPDATE assignments SET status='submitted' WHERE id=%s",
                            (assignment_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        notify(row["lecturer_id"],
               f"{g.user['fullname']} submitted work on \"{row['title']}\" (version {version}).",
               url_for("lecturer.inbox"))
        flash("Sent to your supervisor.", "success")
        return redirect(url_for("student.work", assignment_id=assignment_id))

    submissions = query(
        "SELECT * FROM submissions WHERE assignment_id=%s ORDER BY version DESC",
        (assignment_id,))
    timeline = history(submissions[0]["id"]) if submissions else []

    return render_template(
        "student/work.html", row=row,
        milestones=query("SELECT * FROM milestones WHERE assignment_id=%s ORDER BY phase",
                         (assignment_id,)),
        submissions=submissions, timeline=timeline, today=date.today(),
        stage_label=STAGE_LABEL)


@student_bp.route("/profile", methods=["GET", "POST"])
@student_bp.route("/portfolio", methods=["GET", "POST"], endpoint="portfolio")
@role_required("student")
def profile():
    uid = g.user["id"]

    if request.method == "POST":
        github = request.form.get("github_url", "").strip()
        if github and not github.startswith("https://github.com/"):
            flash("Enter a full GitHub profile link starting with https://github.com/", "error")
        else:
            execute(
                """UPDATE student_profiles
                      SET github_url=%s, bio=%s, phone=%s, department=%s,
                          level=%s, available=%s
                    WHERE user_id=%s""",
                (github or None, request.form.get("bio", "").strip() or None,
                 request.form.get("phone", "").strip() or None,
                 request.form.get("department", "").strip() or None,
                 request.form.get("level", "").strip() or None,
                 "available" in request.form, uid))
            flash("Profile updated.", "success")
        return redirect(url_for("student.profile"))

    profile_row = query(
        """SELECT sp.*, f.name AS field_name, u.name AS university_name
             FROM student_profiles sp
             LEFT JOIN fields f ON f.id=sp.field_id
             LEFT JOIN universities u ON u.id=sp.university_id
            WHERE sp.user_id=%s""", (uid,), one=True) or {}

    work_history = query(
        """SELECT p.title, p.deadline, c.name AS company_name, a.status,
                  (SELECT repo_url FROM submissions s WHERE s.assignment_id=a.id
                    ORDER BY version DESC LIMIT 1) AS repo_url,
                  (SELECT string_agg(sk.name, ', ' ORDER BY sk.name)
                     FROM project_skills ps JOIN skills sk ON sk.id=ps.skill_id
                    WHERE ps.project_id=p.id) AS skill_list
             FROM assignments a
             JOIN projects p ON p.id=a.project_id
             JOIN companies c ON c.id=p.company_id
            WHERE a.student_id=%s ORDER BY a.assigned_at DESC""", (uid,))

    certificates = query(
        """SELECT ce.certificate_number, ce.issued_at, p.title, c.name AS company_name
             FROM certificates ce JOIN projects p ON p.id=ce.project_id
             JOIN companies c ON c.id=p.company_id
            WHERE ce.student_id=%s ORDER BY ce.issued_at DESC""", (uid,))

    return render_template("student/profile.html", profile=profile_row,
                           work_history=work_history, certificates=certificates)
