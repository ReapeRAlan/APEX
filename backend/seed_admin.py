"""
Bootstrap script — Create the first admin user for APEX.

Usage:
    cd D:\MACOV\APEX
    python -m backend.seed_admin
"""

import sys
import os

# Ensure we load the backend .env
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend.db.session import init_db, SessionLocal
from backend.db.models import User
from backend.services.auth_service import hash_password


def seed():
    init_db()

    with SessionLocal() as session:
        existing = session.query(User).filter(User.email == "admin@apex.local").first()
        if existing:
            print(f"Admin user already exists: id={existing.id}, email={existing.email}")
            return

        admin = User(
            email="admin@apex.local",
            hashed_password=hash_password("Admin123!"),
            role="admin",
            full_name="Administrador APEX",
            is_active=True,
        )
        session.add(admin)
        session.commit()
        session.refresh(admin)
        print(f"Admin user created: id={admin.id}, email={admin.email}")
        print("Login with: admin@apex.local / Admin123!")


if __name__ == "__main__":
    seed()
