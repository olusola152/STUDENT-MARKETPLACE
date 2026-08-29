import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))


def _flag(name, default=False):
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes")


class Config:
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    SECRET_KEY = os.environ.get("SECRET_KEY", "")

    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Set SECURE_COOKIES=true once the site is behind HTTPS. Leave it off for
    # local development or the session cookie is never sent back.
    SESSION_COOKIE_SECURE = _flag("SECURE_COOKIES")
    PREFERRED_URL_SCHEME = "https" if _flag("SECURE_COOKIES") else "http"

    # Demo mode lets lecturers and students register, and lecturers work, on a
    # university that has not paid yet. Never leave this on in production.
    DEMO_MODE = _flag("DEMO_MODE")

    # DEMO MODE — opens registration and treats every subscription as active,
    # so the whole flow can be walked through before any money changes hands.
    # A banner appears on every page while it is on. Never enable in production.
    DEMO_MODE = _flag("DEMO_MODE")

    # How many failed sign-ins from one address before it is made to wait.
    LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", 8))
    LOGIN_LOCKOUT_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_SECONDS", 300))
