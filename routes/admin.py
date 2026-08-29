from datetime import date, timedelta

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)

from auth_utils import log_action, role_required
from db import execute, query, scalar

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

SUB_STATUSES = ("pending", "active", "expired", "suspended")
DEFAULT_TERM_DAYS = 365


@admin_bp.route("/dashboard")
@role_required("admin")
def dashboard():
    stats = {
        "students": scalar("SELECT COUNT(*) FROM users WHERE role='student'"),
        "lecturers": scalar("SELECT COUNT(*) FROM users WHERE role='lecturer'"),
        "universities": scalar("SELECT COUNT(*) FROM universities"),
        "companies": scalar("SELECT COUNT(*) FROM companies"),
        "blocked": scalar("SELECT COUNT(*) FROM users WHERE status='blocked'"),
        "projects": scalar("SELECT COUNT(*) FROM projects"),
        "placements": scalar("SELECT COUNT(*) FROM assignments"),
        "pending_subs": scalar(
            """SELECT (SELECT COUNT(*) FROM universities WHERE subscription_status='pending')
                    + (SELECT COUNT(*) FROM companies WHERE subscription_status='pending')"""),
    }
    recent = query(
        """SELECT u.fullname, u.email, u.role, u.status, u.created_at
             FROM users u ORDER BY u.created_at DESC LIMIT 10""")
    activity = query(
        """SELECT a.action, a.target_type, a.target_id, a.detail, a.created_at,
                  u.fullname AS actor
             FROM audit_log a LEFT JOIN users u ON u.id = a.actor_id
            ORDER BY a.created_at DESC LIMIT 12""")
    return render_template("admin/dashboard.html", stats=stats, recent=recent,
                           activity=activity)


@admin_bp.route("/users")
@role_required("admin")
def users():
    role = request.args.get("role", "")
    search = request.args.get("q", "").strip()

    sql = """SELECT u.id, u.fullname, u.email, u.role, u.status, u.created_at,
                    u.last_login_at, u.blocked_reason,
                    COALESCE(uni.name, luni.name, c.name) AS org
               FROM users u
               LEFT JOIN student_profiles sp ON sp.user_id = u.id
               LEFT JOIN universities uni ON uni.id = sp.university_id
               LEFT JOIN lecturer_profiles lp ON lp.user_id = u.id
               LEFT JOIN universities luni ON luni.id = lp.university_id
               LEFT JOIN companies c ON c.user_id = u.id
              WHERE 1=1"""
    params = []
    if role in ("student", "lecturer", "university", "company", "admin"):
        sql += " AND u.role = %s"
        params.append(role)
    if search:
        sql += " AND (u.fullname ILIKE %s OR u.email ILIKE %s)"
        params += [f"%{search}%", f"%{search}%"]
    sql += " ORDER BY u.created_at DESC LIMIT 200"

    return render_template("admin/users.html", users=query(sql, tuple(params)),
                           role=role, search=search)


@admin_bp.route("/users/<int:user_id>/block", methods=["POST"])
@role_required("admin")
def block_user(user_id):
    if user_id == g.user["id"]:
        flash("You cannot block your own account.", "error")
        return redirect(url_for("admin.users"))

    reason = request.form.get("reason", "").strip() or None
    changed = execute(
        """UPDATE users SET status='blocked', blocked_reason=%s
            WHERE id=%s AND role <> 'admin' RETURNING id""",
        (reason, user_id), returning=True)

    if changed:
        log_action("block_user", "user", user_id, reason)
        flash("Account blocked. They are signed out on their next request.", "success")
    else:
        flash("That account could not be blocked.", "error")
    return redirect(request.referrer or url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/unblock", methods=["POST"])
@role_required("admin")
def unblock_user(user_id):
    changed = execute(
        """UPDATE users SET status='active', blocked_reason=NULL
            WHERE id=%s RETURNING id""", (user_id,), returning=True)
    if changed:
        log_action("unblock_user", "user", user_id)
        flash("Account unblocked.", "success")
    return redirect(request.referrer or url_for("admin.users"))


@admin_bp.route("/subscriptions")
@role_required("admin")
def subscriptions():
    universities = query(
        """SELECT u.*, adm.fullname AS admin_name, adm.email AS admin_email,
                  (SELECT COUNT(*) FROM lecturer_profiles lp WHERE lp.university_id = u.id)
                    AS lecturers,
                  (SELECT COUNT(*) FROM student_profiles sp WHERE sp.university_id = u.id)
                    AS students
             FROM universities u LEFT JOIN users adm ON adm.id = u.admin_user_id
            ORDER BY u.created_at DESC""")
    companies = query(
        """SELECT c.*, u.fullname AS admin_name, u.email AS admin_email,
                  (SELECT COUNT(*) FROM projects p WHERE p.company_id = c.id) AS projects
             FROM companies c JOIN users u ON u.id = c.user_id
            ORDER BY c.created_at DESC""")
    return render_template("admin/subscriptions.html", universities=universities,
                           companies=companies, today=date.today())


@admin_bp.route("/subscriptions/<kind>/<int:record_id>", methods=["POST"])
@role_required("admin")
def set_subscription(kind, record_id):
    if kind not in ("university", "company"):
        abort(404)
    status = request.form.get("status", "")
    if status not in SUB_STATUSES:
        abort(400)

    months = request.form.get("months", "")
    until = None
    if status == "active":
        try:
            days = int(months) * 30 if months else DEFAULT_TERM_DAYS
        except ValueError:
            days = DEFAULT_TERM_DAYS
        until = date.today() + timedelta(days=days)

    table = "universities" if kind == "university" else "companies"
    changed = execute(
        f"""UPDATE {table} SET subscription_status=%s, subscribed_until=%s
             WHERE id=%s RETURNING id""", (status, until, record_id), returning=True)

    if changed:
        log_action("set_subscription", kind, record_id, status)
        flash(f"Subscription set to {status}.", "success")
    return redirect(url_for("admin.subscriptions"))


@admin_bp.route("/activity")
@role_required("admin")
def activity():
    rows = query(
        """SELECT a.*, u.fullname AS actor, u.role AS actor_role
             FROM audit_log a LEFT JOIN users u ON u.id = a.actor_id
            ORDER BY a.created_at DESC LIMIT 300""")
    return render_template("admin/activity.html", rows=rows)

@admin_bp.route("/universities")
@role_required("admin")
def universities():
    rows = query(
        """SELECT u.*, adm.fullname AS admin_name, adm.email AS admin_email,
                  adm.status AS admin_status, adm.id AS admin_id,
                  (SELECT COUNT(*) FROM lecturer_profiles lp WHERE lp.university_id = u.id)
                    AS lecturers,
                  (SELECT COUNT(*) FROM student_profiles sp WHERE sp.university_id = u.id)
                    AS students,
                  (SELECT COUNT(*) FROM passcodes p
                    WHERE p.university_id = u.id AND p.used_at IS NULL
                      AND p.expires_at > NOW()) AS open_codes,
                  (SELECT COUNT(*) FROM assignments a
                     JOIN lecturer_profiles lp2 ON lp2.user_id = a.lecturer_id
                    WHERE lp2.university_id = u.id) AS placements
             FROM universities u
             LEFT JOIN users adm ON adm.id = u.admin_user_id
            ORDER BY u.created_at DESC""")
    return render_template("admin/universities.html", universities=rows,
                           today=date.today())


@admin_bp.route("/companies")
@role_required("admin")
def companies():
    rows = query(
        """SELECT c.*, u.fullname AS admin_name, u.email AS admin_email,
                  u.status AS admin_status, u.id AS admin_id,
                  (SELECT COUNT(*) FROM projects p WHERE p.company_id = c.id) AS projects,
                  (SELECT COUNT(*) FROM projects p
                    WHERE p.company_id = c.id AND p.status = 'open') AS open_projects,
                  (SELECT COUNT(*) FROM assignments a
                     JOIN projects p2 ON p2.id = a.project_id
                    WHERE p2.company_id = c.id) AS placements
             FROM companies c JOIN users u ON u.id = c.user_id
            ORDER BY c.created_at DESC""")
    return render_template("admin/companies.html", companies=rows, today=date.today())


@admin_bp.route("/projects")
@role_required("admin")
def projects():
    status = request.args.get("status", "")
    search = request.args.get("q", "").strip()

    sql = """SELECT p.id, p.title, p.status, p.deadline,
                    p.max_students, p.created_at, f.name AS field_name,
                    c.name AS company_name, c.subscription_status,
                    (SELECT COUNT(*) FROM submissions s
                       JOIN assignments a2 ON a2.id = s.assignment_id
                      WHERE a2.project_id = p.id) AS submissions,
                    (SELECT COUNT(*) FROM assignments asg
                      WHERE asg.project_id = p.id) AS students_on
               FROM projects p
               JOIN fields f ON f.id = p.field_id
               JOIN companies c ON c.id = p.company_id
              WHERE 1=1"""
    params = []
    if status in ("draft", "open", "engaged", "completed", "cancelled"):
        sql += " AND p.status = %s"
        params.append(status)
    if search:
        sql += " AND (p.title ILIKE %s OR c.name ILIKE %s)"
        params += [f"%{search}%", f"%{search}%"]
    sql += " ORDER BY p.created_at DESC LIMIT 200"

    return render_template("admin/projects.html", projects=query(sql, tuple(params)),
                           status=status, search=search, today=date.today())


@admin_bp.route("/projects/<int:project_id>/cancel", methods=["POST"])
@role_required("admin")
def cancel_project(project_id):
    """Moderation: pull a brief that breaches the rules."""
    reason = request.form.get("reason", "").strip() or None
    changed = execute(
        """UPDATE projects SET status = 'cancelled'
            WHERE id = %s AND status IN ('draft','open') RETURNING id""",
        (project_id,), returning=True)

    if changed:
        log_action("cancel_project", "project", project_id, reason)
        flash("Project cancelled.", "success")
    else:
        flash("Only draft or open projects can be cancelled.", "error")
    return redirect(request.referrer or url_for("admin.projects"))


@admin_bp.route("/placements")
@role_required("admin")
def placements():
    rows = query(
        """SELECT asg.id, asg.status, asg.assigned_at,
                  stu.fullname AS student_name, lec.fullname AS lecturer_name,
                  uni.name AS university_name, c.name AS company_name,
                  p.title, p.deadline,
                  (SELECT COUNT(*) FROM milestones m
                    WHERE m.assignment_id = asg.id AND m.status = 'done') AS done,
                  (SELECT COUNT(*) FROM milestones m
                    WHERE m.assignment_id = asg.id) AS total,
                  (SELECT COUNT(*) FROM milestones m
                    WHERE m.assignment_id = asg.id AND m.status <> 'done'
                      AND m.due_date < CURRENT_DATE) AS overdue
             FROM assignments asg
             JOIN users stu ON stu.id = asg.student_id
             JOIN users lec ON lec.id = asg.lecturer_id
             LEFT JOIN lecturer_profiles lp ON lp.user_id = lec.id
             LEFT JOIN universities uni ON uni.id = lp.university_id
             JOIN projects p ON p.id = asg.project_id
             JOIN companies c ON c.id = p.company_id
            ORDER BY p.deadline LIMIT 300""")
    return render_template("admin/placements.html", placements=rows, today=date.today())


@admin_bp.route("/notifications")
@role_required("admin")
def notifications():
    rows = query(
        "SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 100",
        (g.user["id"],))
    execute("UPDATE notifications SET read_at=NOW() WHERE user_id=%s AND read_at IS NULL",
            (g.user["id"],))
    return render_template("shared/notifications.html", rows=rows)
