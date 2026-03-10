"""Create initial admin user. Run once after migration 011.

Usage:
    python -m scripts.create_admin_user
    python -m scripts.create_admin_user --username admin --password your-secure-password
"""

import asyncio
import argparse
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.db.session import async_session_factory
from app.db.models import User, UserRole
from app.services.auth_service import hash_password


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="admin", help="Admin username")
    parser.add_argument("--password", default=None, help="Admin password (prompt if not set)")
    parser.add_argument("--email", default="", help="Admin email (optional)")
    args = parser.parse_args()

    password = args.password
    if not password:
        import getpass
        password = getpass.getpass("Enter admin password: ")
        if not password or len(password) < 8:
            print("Password must be at least 8 characters")
            sys.exit(1)

    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.username == args.username))
        existing = result.scalar_one_or_none()
        if existing:
            print(f"User '{args.username}' already exists")
            return
        user = User(
            username=args.username,
            email=args.email or None,
            password_hash=hash_password(password),
            role=UserRole.ADMIN.value,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        print(f"Created admin user: {args.username}")


if __name__ == "__main__":
    asyncio.run(main())
