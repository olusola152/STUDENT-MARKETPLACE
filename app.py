import os

from flask import Flask, g, jsonify, redirect, render_template, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from auth_utils import ROLE_LABELS, dashboard_for, load_current_user
from config import Config
from db import close_db


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    if not app.config["SECRET_KEY"]:
        raise RuntimeError(
            "SECRET_KEY is not set. Generate one with "
            '`python -c "import secrets; print(secrets.token_hex(32))"` '
            "and put it in .env or the host's environment.")

    # Behind a load balancer the real client address and scheme arrive in
    # headers; without this, redirects and rate limiting see the proxy.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.teardown_appcontext(close_db)
    app.before_request(load_current_user)

    @app.context_processor
    def inject_notifications():
        from workflow import subscription_live, unread_count
        if not g.get("user"):
            return {}
        uni = g.get("university")
        return {
            "unread": unread_count(g.user["id"]),
            "sub_live": (subscription_live(uni.get("subscription_status"),
                                           uni.get("subscribed_until"))
                         if uni else True),
        }

    from routes.admin import admin_bp
    from routes.auth import auth_bp
    from routes.company import company_bp
    from routes.lecturer import lecturer_bp
    from routes.portal import portal_bp
    from routes.student import student_bp
    from routes.university import university_bp

    for bp in (auth_bp, portal_bp, student_bp, lecturer_bp, university_bp,
               company_bp, admin_bp):
        app.register_blueprint(bp)

    @app.context_processor
    def inject_labels():
        return {"role_labels": ROLE_LABELS}

    @app.route("/")
    def home():
        if g.get("user"):
            return redirect(dashboard_for(g.user["role"]))
        from db import query
        from workflow import expire_lapsed
        expire_lapsed()
        return render_template(
            "public/landing.html",
            universities=query(
                """SELECT name, slug, city FROM universities
                    WHERE subscription_status='active' ORDER BY name LIMIT 12"""))

    @app.route("/healthz")
    def healthz():
        """Liveness probe for the host. Does not touch the database."""
        return jsonify(status="ok")

    @app.errorhandler(413)
    def too_large(_):
        return render_template("error.html", code=413,
                               message="That upload is too large."), 413

    @app.errorhandler(429)
    def too_many(_):
        return render_template("error.html", code=429,
                               message="Too many attempts. Try again shortly."), 429

    @app.errorhandler(403)
    def forbidden(_):
        return render_template("error.html", code=403,
                               message="You do not have access to that."), 403

    @app.errorhandler(404)
    def not_found(_):
        return render_template("error.html", code=404,
                               message="That page does not exist."), 404

    @app.errorhandler(500)
    def server_error(_):
        return render_template("error.html", code=500,
                               message="Something broke on our side. Try again."), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
