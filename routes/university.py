from datetime import datetime, timedelta, timezone

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, url_for)
from werkzeug.security import generate_password_hash

from auth_utils import (generate_passcode, log_action, role_required,
                        subscription_required)
from db import execute, query, scalar
from workflow import (chain_people, history, notify, notify_many,
                      record_review, submission_for)

university_bp = Blueprint("university", __name__, url_prefix="/university")

PASSCODE_DAYS = 14


def _my_university():
    uni = g.get("university")
    if not uni:
        abort(404)
    return uni


@university_bp.route("/dashboard")
@role_required("university")
def dashboard():
    uni = _my_university()
    stats = {
        "lecturers": scalar(
            "SELECT COUNT(*) FROM lecturer_profiles WHERE university_id = %s", (uni["id"],)),
        "students": scalar(
            "SELECT COUNT(*) FROM student_profiles WHERE university_id = %s", (uni["id"],)),
        "open_codes": scalar(
            """SELECT COUNT(*) FROM passcodes
                WHERE university_id = %s AND used_at IS NULL AND expires_at > NOW()""",
            (uni["id"],)),
        "waiting": scalar(
            """SELECT COUNT(*) FROM submissions s
               JOIN assignments a ON a.id = s.assignment_id
               JOIN lecturer_profiles lp ON lp.user_id = a.lecturer_id
              WHERE lp.university_id = %s AND s.stage = 'with_university'""",
            (uni["id"],)),
        "placements": scalar(
            """SELECT COUNT(*) FROM assignments a
               JOIN lecturer_profiles lp ON lp.user_id = a.lecturer_id
              WHERE lp.university_id = %s""", (uni["id"],)),
        "completed": scalar(
            """SELECT COUNT(*) FROM assignments a
               JOIN lecturer_profiles lp ON lp.user_id = a.lecturer_id
              WHERE lp.university_id = %s AND a.status = 'completed'""", (uni["id"],)),
    }
    return render_template("university/dashboard.html", uni=uni, stats=stats)


@university_bp.route("/lecturers")
@role_required("university")
def lecturers():
    uni = _my_university()
    rows = query(
        """SELECT u.id, u.fullname, u.email, u.status, u.created_at, lp.department,
                  (SELECT string_agg(f.name, ', ' ORDER BY f.name)
                     FROM lecturer_fields lf JOIN fields f ON f.id = lf.field_id
                    WHERE lf.lecturer_id = u.id) AS expertise,
                  (SELECT COUNT(*) FROM assignments a WHERE a.lecturer_id = u.id)
                    AS placements
             FROM lecturer_profiles lp JOIN users u ON u.id = lp.user_id
            WHERE lp.university_id = %s ORDER BY u.fullname""", (uni["id"],))
    return render_template("university/lecturers.html", uni=uni, lecturers=rows)


@university_bp.route("/students")
@role_required("university")
def students():
    uni = _my_university()
    rows = query(
        """SELECT u.id, u.fullname, u.email, u.status, sp.level, sp.department,
                  sp.matric_no, sp.github_url, f.name AS field_name,
                  (SELECT COUNT(*) FROM assignments a WHERE a.student_id = u.id)
                    AS placements
             FROM student_profiles sp
             JOIN users u ON u.id = sp.user_id
             LEFT JOIN fields f ON f.id = sp.field_id
            WHERE sp.university_id = %s ORDER BY u.fullname""", (uni["id"],))
    return render_template("university/students.html", uni=uni, students=rows)


@university_bp.route("/passcodes", methods=["GET", "POST"])
@role_required("university")
def passcodes():
    uni = _my_university()
    issued = None

    if request.method == "POST":
        if uni["subscription_status"] != "active" and not current_app.config.get("DEMO_MODE"):
            flash("Activate your subscription before issuing lecturer codes.", "error")
            return redirect(url_for("university.passcodes"))

        try:
            count = int(request.form.get("count") or 1)
        except ValueError:
            count = 0
        if not 1 <= count <= 25:
            flash("Issue between 1 and 25 codes at a time.", "error")
            return redirect(url_for("university.passcodes"))

        label = request.form.get("label", "").strip() or None
        expires = datetime.now(timezone.utc) + timedelta(days=PASSCODE_DAYS)

        issued = []
        for _ in range(count):
            code = generate_passcode()
            # Only the hash is stored, so a leaked database gives no working codes.
            execute(
                """INSERT INTO passcodes (university_id, code_hash, code_hint, label,
                   issued_by, expires_at) VALUES (%s,%s,%s,%s,%s,%s)""",
                (uni["id"], generate_password_hash(code), code[:4], label,
                 g.user["id"], expires))
            issued.append(code)

        log_action("issue_passcodes", "university", uni["id"], f"{count} codes")
        flash(f"{count} code(s) issued. They are shown once — copy them now.", "success")

    rows = query(
        """SELECT p.id, p.code_hint, p.label, p.expires_at, p.used_at,
                  u.fullname AS used_by_name
             FROM passcodes p LEFT JOIN users u ON u.id = p.used_by
            WHERE p.university_id = %s ORDER BY p.created_at DESC LIMIT 60""",
        (uni["id"],))

    return render_template("university/passcodes.html", uni=uni, codes=rows,
                           issued=issued, days=PASSCODE_DAYS,
                           now=datetime.now(timezone.utc))


@university_bp.route("/passcodes/<int:passcode_id>/revoke", methods=["POST"])
@role_required("university")
def revoke_passcode(passcode_id):
    uni = _my_university()
    changed = execute(
        """UPDATE passcodes SET expires_at = NOW()
            WHERE id = %s AND university_id = %s AND used_at IS NULL
        RETURNING id""", (passcode_id, uni["id"]), returning=True)
    flash("Code revoked." if changed else "That code could not be revoked.",
          "success" if changed else "error")
    return redirect(url_for("university.passcodes"))


@university_bp.route("/inbox")
@role_required("university")
def inbox():
    """Work forwarded by this institution's lecturers, awaiting sign-off."""
    uni = _my_university()
    rows = query(
        """SELECT s.id, s.version, s.repo_url, s.notes, s.stage, s.updated_at,
                  stu.fullname AS student_name, lec.fullname AS lecturer_name,
                  p.title, c.name AS company_name
             FROM submissions s
             JOIN assignments a ON a.id = s.assignment_id
             JOIN users stu ON stu.id = a.student_id
             JOIN users lec ON lec.id = a.lecturer_id
             JOIN lecturer_profiles lp ON lp.user_id = a.lecturer_id
             JOIN projects p ON p.id = a.project_id
             JOIN companies c ON c.id = p.company_id
            WHERE lp.university_id = %s
            ORDER BY CASE s.stage WHEN 'with_university' THEN 0 ELSE 1 END,
                     s.updated_at DESC""", (uni["id"],))
    return render_template("university/inbox.html", uni=uni, submissions=rows)


@university_bp.route("/submissions/<int:submission_id>", methods=["GET", "POST"])
@role_required("university")
def review(submission_id):
    uni = _my_university()
    sub = submission_for(submission_id)
    if not sub or sub["university_id"] != uni["id"]:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action", "")
        comment = request.form.get("comment", "").strip()

        if sub["stage"] != "with_university":
            flash("This submission is not with you at the moment.", "error")
            return redirect(url_for("university.review", submission_id=submission_id))

        if action == "forward":
            record_review(submission_id, "forward", comment, "with_company")
            notify(sub["company_user_id"],
                   f"{uni['name']} forwarded work on \"{sub['title']}\" for your review.",
                   url_for("company.review", submission_id=submission_id))
            notify_many([sub["student_id"], sub["lecturer_id"]],
                        f"{uni['name']} passed the work on \"{sub['title']}\" to "
                        f"{sub['company_name']}.",
                        url_for("student.work", assignment_id=sub["assignment_id"]))
            flash("Sent to the company.", "success")

        elif action == "request_correction":
            if len(comment) < 10:
                flash("Say what needs correcting — at least 10 characters.", "error")
                return redirect(url_for("university.review", submission_id=submission_id))
            record_review(submission_id, "request_correction", comment, "revision")
            execute("UPDATE assignments SET status='revision' WHERE id=%s",
                    (sub["assignment_id"],))
            notify_many([sub["student_id"], sub["lecturer_id"]],
                        f"{uni['name']} asked for corrections on \"{sub['title']}\".",
                        url_for("student.work", assignment_id=sub["assignment_id"]))
            flash("Correction requested.", "success")
        else:
            abort(400)

        return redirect(url_for("university.inbox"))

    return render_template("university/review.html", uni=uni, sub=sub,
                           timeline=history(submission_id))


@university_bp.route("/placements")
@role_required("university")
@subscription_required
def placements():
    uni = _my_university()
    rows = query(
        """SELECT asg.id, asg.status, stu.fullname AS student_name,
                  lec.fullname AS lecturer_name, p.title, p.deadline,
                  c.name AS company_name,
                  (SELECT COUNT(*) FROM milestones m
                    WHERE m.assignment_id = asg.id AND m.status='done') AS done,
                  (SELECT COUNT(*) FROM milestones m WHERE m.assignment_id = asg.id) AS total
             FROM assignments asg
             JOIN users stu ON stu.id = asg.student_id
             JOIN users lec ON lec.id = asg.lecturer_id
             JOIN lecturer_profiles lp ON lp.user_id = lec.id
             JOIN projects p ON p.id = asg.project_id
             JOIN companies c ON c.id = p.company_id
            WHERE lp.university_id = %s ORDER BY p.deadline""", (uni["id"],))
    return render_template("university/placements.html", uni=uni, placements=rows)


@university_bp.route("/notifications")
@role_required("university")
def notifications():
    rows = query(
        "SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 100",
        (g.user["id"],))
    execute("UPDATE notifications SET read_at=NOW() WHERE user_id=%s AND read_at IS NULL",
            (g.user["id"],))
    return render_template("shared/notifications.html", rows=rows)
