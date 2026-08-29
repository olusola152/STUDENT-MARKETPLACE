"""Create the first platform administrator.

    python create_admin.py
"""

from getpass import getpass

from werkzeug.security import generate_password_hash

from app import app
from db import execute, query

with app.app_context():
    fullname = input("Admin full name: ").strip()
    email = input("Admin email: ").strip().lower()
    password = getpass("Password (min 8 chars): ")

    if len(password) < 8:
        raise SystemExit("Password too short.")
    if getpass("Repeat password: ") != password:
        raise SystemExit("Passwords do not match.")
    if query("SELECT id FROM users WHERE email = %s", (email,), one=True):
        raise SystemExit("That email already exists.")

    new_id = execute(
        """INSERT INTO users (fullname, email, password, role)
           VALUES (%s, %s, %s, 'admin') RETURNING id""",
        (fullname, email, generate_password_hash(password)), returning=True)
    print(f"Admin created: {email} (user id {new_id})")
