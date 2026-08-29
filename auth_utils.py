"""Session helpers, route guards and subscription checks."""

import re
import re
import secrets
import string
from functools import wraps

from flask import current_app, flash, g, redirect, request, session, url_for

from db import execute, query

DASHBOARDS = {
    "student": "student.dashboard",
    "lecturer": "lecturer.dashboard",
    "university": "university.dashboard",
    "company": "company.dashboard",
    "admin": "admin.dashboard",
}

ROLE_LABELS = {
    "student": "Student",
    "lecturer": "Lecturer",
    "university": "University",
    "company": "Company",
    "admin": "Administrator",
}


def load_current_user():
    """Runs before every request; puts the signed-in user on g.user."""
    g.user = None
    user_id = session.get("user_id")
    if not user_id:
        return

    user = query(
        """SELECT u.id, u.fullname, u.email, u.role, u.status,
                  c.id AS company_id, c.name AS company_name,
                  c.subscription_status AS company_subscription,
                  c.subscribed_until AS company_until,
                  lp.university_id AS lecturer_university_id,
                  sp.university_id AS student_university_id,
                  sp.field_id AS student_field_id
             FROM users u
             LEFT JOIN companies c ON c.user_id = u.id
             LEFT JOIN lecturer_profiles lp ON lp.user_id = u.id
             LEFT JOIN student_profiles sp ON sp.user_id = u.id
            WHERE u.id = %s""", (user_id,), one=True)

    # A blocked or deleted account loses its session on the next request.
    if user is None or user["status"] == "blocked":
        session.clear()
        return

    g.user = user

    if user["role"] == "university":
        g.university = query(
            """SELECT id, name, slug, subscription_status, subscribed_until
                 FROM universities WHERE admin_user_id = %s""", (user_id,), one=True)
    elif user["role"] == "lecturer":
        g.university = query(
            """SELECT id, name, slug, subscription_status, subscribed_until
                 FROM universities WHERE id = %s""",
            (user["lecturer_university_id"],), one=True)
    elif user["role"] == "student":
        g.university = query(
            """SELECT u.id, u.name, u.slug, u.subscription_status, u.subscribed_until
                 FROM universities u WHERE u.id = %s""",
            (user["student_university_id"],), one=True)
    else:
        g.university = None


def sign_in(user):
    session.clear()
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    execute("UPDATE users SET last_login_at = NOW() WHERE id = %s", (user["id"],))


def dashboard_for(role):
    return url_for(DASHBOARDS.get(role, "auth.login"))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.get("user"):
            flash("Sign in to continue.", "error")
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] not in roles:
                flash("That area belongs to a different account type.", "error")
                return redirect(dashboard_for(g.user["role"]))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def subscription_required(view):
    """Blocks paid actions when the account is not on an active subscription.

    Read-only pages stay open so an expired account can still see its data and
    renew; only the actions that consume the service are gated.
    """

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_app.config.get("DEMO_MODE"):
            return view(*args, **kwargs)

        from workflow import subscription_live

        role = g.user["role"]
        if role == "company" and not subscription_live(
                g.user["company_subscription"], g.user["company_until"]):
            flash("Your company subscription is not active. Renew it to carry on.",
                  "error")
            return redirect(url_for("company.dashboard"))
        if role in ("university", "lecturer", "student"):
            uni = g.get("university")
            if not uni or not subscription_live(
                    uni["subscription_status"], uni["subscribed_until"]):
                flash(f"{uni['name'] if uni else 'Your institution'}'s subscription "
                      "has expired. Nothing can be done here until it is renewed.",
                      "error")
                return redirect(dashboard_for(role))
        return view(*args, **kwargs)

    return wrapped


def slugify(value, existing=None):
    """URL-safe name for a university's own join page."""
    base = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")[:70] or "university"
    slug = base
    n = 2
    while existing and slug in existing:
        slug = f"{base}-{n}"
        n += 1
    return slug


PASSCODE_ALPHABET = string.ascii_uppercase + string.digits
AMBIGUOUS = {"O", "0", "I", "1"}


def generate_passcode(groups=3, size=4):
    """A readable one-time code, e.g. XK4T-9PQR-2MBD.

    Ambiguous characters are excluded because these get read aloud and typed
    by hand from a printed list.
    """
    pool = [c for c in PASSCODE_ALPHABET if c not in AMBIGUOUS]
    return "-".join(
        "".join(secrets.choice(pool) for _ in range(size)) for _ in range(groups))


def log_action(action, target_type=None, target_id=None, detail=None):
    actor = g.user["id"] if g.get("user") else None
    execute(
        """INSERT INTO audit_log (actor_id, action, target_type, target_id, detail)
           VALUES (%s, %s, %s, %s, %s)""",
        (actor, action, target_type, target_id, detail))
