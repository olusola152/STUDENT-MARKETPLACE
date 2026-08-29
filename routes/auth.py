import re

from flask import (Blueprint, current_app, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from auth_utils import dashboard_for, log_action, sign_in, slugify
from db import get_db, query
from security import clear_failures, login_blocked, record_failure

auth_bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")

# Only organisations sign up on the main page. Students and lecturers join
# through their own university's page.
SIGNUP_ROLES = ("company", "university")


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


def _website_ok(value):
    return not value or value.startswith(("http://", "https://"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Organisation signup only — company or university."""
    if g.get("user"):
        return redirect(dashboard_for(g.user["role"]))

    form = {"role": "company"}

    if request.method == "POST":
        form = {k: v.strip() for k, v in request.form.items()}
        role = form.get("role", "")
        email = form.get("email", "").lower()
        form["email"] = email
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        errors = _base_checks(form, password, confirm)
        if role not in SIGNUP_ROLES:
            errors.append("Choose an account type.")

        website = form.get("website", "")
        if not _website_ok(website):
            errors.append("The website must start with https://")

        if role == "company" and not form.get("company_name"):
            errors.append("Enter the company name.")
        if role == "university" and not form.get("university_name"):
            errors.append("Enter the institution name.")

        if not errors and query("SELECT id FROM users WHERE email = %s", (email,), one=True):
            errors.append("That email is already registered. Sign in instead.")

        if role == "university" and form.get("university_name"):
            clash = query("SELECT id FROM universities WHERE lower(name) = lower(%s)",
                          (form["university_name"],), one=True)
            if clash:
                errors.append("That institution is already registered.")

        if errors:
            for m in errors:
                flash(m, "error")
            return render_template("auth/register.html", form=form), 400

        conn = get_db()
        new_slug = None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO users (fullname, email, password, role)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (form["fullname"], email, generate_password_hash(password), role))
                user_id = cur.fetchone()["id"]

                if role == "company":
                    cur.execute(
                        """INSERT INTO companies (user_id, name, website, phone)
                           VALUES (%s, %s, %s, %s)""",
                        (user_id, form["company_name"], website or None, form.get("phone")))
                else:
                    cur.execute("SELECT slug FROM universities")
                    taken = {r["slug"] for r in cur.fetchall()}
                    new_slug = slugify(form["university_name"], taken)
                    cur.execute(
                        """INSERT INTO universities
                           (name, slug, website, city, contact_email, admin_user_id)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (form["university_name"], new_slug, website or None,
                         form.get("city"), email, user_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        sign_in({"id": user_id, "role": role})
        log_action("register", "user", user_id, role)
        flash("Account created. An administrator activates your subscription "
              "once payment clears.", "success")

        if role == "university":
            # Show the institution its own portal first — that link is the
            # thing it will be sharing with staff and students.
            return redirect(url_for("portal.home", slug=new_slug))
        return redirect(dashboard_for(role))

    return render_template("auth/register.html", form=form)


# ------------------------------------------------------------------ joining

@auth_bp.route("/join")
def join_index():
    """Directory of institution portals, for anyone who lost their link."""
    if current_app.config.get("DEMO_MODE"):
        unis = query(
            """SELECT id, name, slug, city, website, subscription_status
                 FROM universities ORDER BY name""")
    else:
        unis = query(
            """SELECT id, name, slug, city, website, subscription_status
                 FROM universities
                WHERE subscription_status = 'active' ORDER BY name""")
    return render_template("auth/join_index.html", universities=unis)


# ------------------------------------------------------------------ session

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user"):
        return redirect(dashboard_for(g.user["role"]))

    email = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if login_blocked():
            flash("Too many failed attempts. Wait a few minutes and try again.", "error")
            return render_template("auth/login.html", email=email), 429

        user = query(
            """SELECT id, fullname, password, role, status
                 FROM users WHERE email = %s""", (email,), one=True)

        if not user or not check_password_hash(user["password"], password):
            record_failure()
            flash("Email or password is incorrect.", "error")
            return render_template("auth/login.html", email=email), 401

        if user["status"] == "blocked":
            flash("This account has been blocked. Contact the administrator.", "error")
            return render_template("auth/login.html", email=email), 403

        clear_failures()
        sign_in(user)
        flash(f"Signed in as {user['fullname']}.", "success")

        nxt = request.args.get("next")
        if nxt and nxt.startswith("/") and not nxt.startswith("//"):
            return redirect(nxt)
        return redirect(dashboard_for(user["role"]))

    return render_template("auth/login.html", email=email)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("auth.login"))
