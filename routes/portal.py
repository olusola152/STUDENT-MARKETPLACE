"""Each university's own corner of the platform, at /u/<slug>.

The university shares this link. Lecturers register here with a passcode,
students register here freely, and both sign in here afterwards. The slug in
the URL binds every account created to that institution.
"""

import re

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from auth_utils import dashboard_for, log_action, sign_in
from db import get_db, query
from workflow import effective_status, subscription_live
from security import clear_failures, login_blocked, record_failure

portal_bp = Blueprint("portal", __name__, url_prefix="/u")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def _load(slug):
    uni = query("SELECT * FROM universities WHERE slug = %s", (slug,), one=True)
    if not uni:
        abort(404)
    return uni


def _demo():
    return current_app.config.get("DEMO_MODE", False)


def _base_checks(form, password, confirm):
    errors = []
    if len(form.get("fullname", "")) < 3:
        errors.append("Enter your full name.")
    if not EMAIL_RE.match(form.get("email", "")):
        errors.append("Enter a valid email address.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if password != confirm:
        errors.append("The two passwords do not match.")
    return errors


@portal_bp.route("/<slug>")
def home(slug):
    uni = _load(slug)
    return render_template(
        "portal/home.html", uni=uni,
        portal_live=subscription_live(uni["subscription_status"], uni["subscribed_until"]),
        status=effective_status(uni["subscription_status"], uni["subscribed_until"]))


@portal_bp.route("/<slug>/login", methods=["GET", "POST"])
def login(slug):
    """Same credentials as the main site, wearing the institution's name."""
    uni = _load(slug)
    email = ""
    if not subscription_live(uni["subscription_status"], uni["subscribed_until"]):
        return render_template(
            "portal/home.html", uni=uni, portal_live=False,
            status=effective_status(uni["subscription_status"],
                                    uni["subscribed_until"])), 403

    if request.method == "POST" and g.get("user"):
        return redirect(dashboard_for(g.user["role"]))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if login_blocked():
            flash("Too many failed attempts. Wait a few minutes and try again.", "error")
            return render_template("portal/login.html", uni=uni, email=email), 429

        user = query(
            "SELECT id, fullname, password, role, status FROM users WHERE email = %s",
            (email,), one=True)

        if not user or not check_password_hash(user["password"], password):
            record_failure()
            flash("Email or password is incorrect.", "error")
            return render_template("portal/login.html", uni=uni, email=email), 401
        if user["status"] == "blocked":
            flash("This account has been blocked. Contact the administrator.", "error")
            return render_template("portal/login.html", uni=uni, email=email), 403

        clear_failures()
        sign_in(user)
        flash(f"Signed in as {user['fullname']}.", "success")
        return redirect(dashboard_for(user["role"]))

    return render_template("portal/login.html", uni=uni, email=email)


@portal_bp.route("/<slug>/student", methods=["GET", "POST"])
def student_signup(slug):
    uni = _load(slug)
    fields = query("SELECT id, name FROM fields ORDER BY name")
    form = {}
    closed = not subscription_live(uni["subscription_status"], uni["subscribed_until"]) and not _demo()

    # Someone already signed in — a university previewing its own portal, say —
    # sees the page with a notice rather than being bounced to their dashboard.
    if request.method == "POST" and g.get("user"):
        flash("You are already signed in. Sign out first to create a new account.",
              "error")
        return redirect(url_for("portal.student_signup", slug=slug))

    if request.method == "POST":
        if closed:
            flash("Registration is closed for this institution.", "error")
            return redirect(url_for("portal.home", slug=slug))

        form = {k: v.strip() for k, v in request.form.items()}
        email = form.get("email", "").lower()
        form["email"] = email
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        errors = _base_checks(form, password, confirm)

        valid = {str(f["id"]) for f in fields}
        if form.get("field_id") not in valid:
            errors.append("Choose your course of study.")
        field_id = int(form["field_id"]) if form.get("field_id") in valid else None

        github = form.get("github_url", "")
        if github and not github.startswith("https://github.com/"):
            errors.append("The GitHub link must start with https://github.com/")

        if not errors and query("SELECT id FROM users WHERE email = %s", (email,), one=True):
            errors.append("That email is already registered. Sign in instead.")

        if errors:
            for m in errors:
                flash(m, "error")
            return render_template("portal/student_signup.html", uni=uni,
                                   fields=fields, form=form, closed=closed), 400

        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO users (fullname, email, password, role)
                       VALUES (%s,%s,%s,'student') RETURNING id""",
                    (form["fullname"], email, generate_password_hash(password)))
                user_id = cur.fetchone()["id"]
                cur.execute(
                    """INSERT INTO student_profiles
                       (user_id, university_id, field_id, department, level,
                        matric_no, github_url, phone)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (user_id, uni["id"], field_id, form.get("department"),
                     form.get("level"), form.get("matric_no"), github or None,
                     form.get("phone")))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        sign_in({"id": user_id, "role": "student"})
        log_action("register_student", "user", user_id, uni["name"])
        flash(f"Account created under {uni['name']}.", "success")
        return redirect(dashboard_for("student"))

    return render_template("portal/student_signup.html", uni=uni, fields=fields,
                           form=form, closed=closed)


@portal_bp.route("/<slug>/lecturer", methods=["GET", "POST"])
def lecturer_signup(slug):
    uni = _load(slug)
    fields = query("SELECT id, name FROM fields ORDER BY name")
    form = {}
    closed = not subscription_live(uni["subscription_status"], uni["subscribed_until"]) and not _demo()

    if request.method == "POST" and g.get("user"):
        flash("You are already signed in. Sign out first to create a new account.",
              "error")
        return redirect(url_for("portal.lecturer_signup", slug=slug))

    if request.method == "POST":
        if closed:
            flash("Registration is closed for this institution.", "error")
            return redirect(url_for("portal.home", slug=slug))

        form = {k: v.strip() for k, v in request.form.items()}
        email = form.get("email", "").lower()
        form["email"] = email
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        code = form.get("passcode", "").upper().replace(" ", "")
        picked = request.form.getlist("fields")

        errors = _base_checks(form, password, confirm)

        valid = {str(f["id"]) for f in fields}
        field_ids = [int(f) for f in picked if f in valid]
        if not field_ids:
            errors.append("Select at least one area of expertise.")

        # Only codes issued by THIS university are considered, so a code from
        # one institution cannot register a lecturer at another.
        match = None
        if code:
            for row in query(
                """SELECT id, code_hash FROM passcodes
                    WHERE university_id = %s AND used_at IS NULL
                      AND expires_at > NOW()""", (uni["id"],)):
                if check_password_hash(row["code_hash"], code):
                    match = row
                    break
        if not match:
            errors.append("That passcode is not valid for this institution, "
                          "has expired, or has already been used.")

        if not errors and query("SELECT id FROM users WHERE email = %s", (email,), one=True):
            errors.append("That email is already registered. Sign in instead.")

        if errors:
            for m in errors:
                flash(m, "error")
            return render_template("portal/lecturer_signup.html", uni=uni,
                                   fields=fields, form=form, closed=closed), 400

        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO users (fullname, email, password, role)
                       VALUES (%s,%s,%s,'lecturer') RETURNING id""",
                    (form["fullname"], email, generate_password_hash(password)))
                user_id = cur.fetchone()["id"]
                cur.execute(
                    """INSERT INTO lecturer_profiles
                       (user_id, university_id, department, staff_no, phone)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (user_id, uni["id"], form.get("department"),
                     form.get("staff_no"), form.get("phone")))
                cur.executemany(
                    "INSERT INTO lecturer_fields (lecturer_id, field_id) VALUES (%s,%s)",
                    [(user_id, fid) for fid in field_ids])
                # Burned in the same transaction, only while still unused.
                cur.execute(
                    """UPDATE passcodes SET used_at = NOW(), used_by = %s
                        WHERE id = %s AND used_at IS NULL""", (user_id, match["id"]))
                if cur.rowcount != 1:
                    raise RuntimeError("passcode consumed")
            conn.commit()
        except RuntimeError:
            conn.rollback()
            flash("That passcode has just been used by someone else.", "error")
            return render_template("portal/lecturer_signup.html", uni=uni,
                                   fields=fields, form=form, closed=closed), 400
        except Exception:
            conn.rollback()
            raise

        sign_in({"id": user_id, "role": "lecturer"})
        log_action("register_lecturer", "user", user_id, uni["name"])
        flash(f"Account created under {uni['name']}.", "success")
        return redirect(dashboard_for("lecturer"))

    return render_template("portal/lecturer_signup.html", uni=uni, fields=fields,
                           form=form, closed=closed)
