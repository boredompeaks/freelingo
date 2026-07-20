import argparse
import asyncio

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.user import User
from app.models.user_language import UserLanguage


async def create_user(
    username: str,
    email: str,
    display_name: str,
    password_plain: str,
    role: str,
    native_lang: str,
    target_lang: str,
):
    try:
        # Validate inputs
        if not username or not password_plain:
            print("Error: Username and Password are required.")
            return

        async with AsyncSessionLocal() as session:
            # Check if user already exists
            from sqlalchemy import select

            existing_user = await session.execute(select(User).where(User.username == username))
            if existing_user.scalar_one_or_none():
                print(f"Error: Username '{username}' is already taken.")
                return

            if email:
                existing_email = await session.execute(select(User).where(User.email == email))
                if existing_email.scalar_one_or_none():
                    print(f"Error: Email '{email}' is already taken.")
                    return

            user = User(
                username=username,
                email=email,
                display_name=display_name or username,
                hashed_password=hash_password(password_plain),
                native_language=native_lang,
                target_language=target_lang,
                role=role,
                is_active=True,
                is_verified=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            # Add target language record
            user_lang = UserLanguage(
                user_id=user.id,
                language=target_lang,
                is_active=True,
            )
            session.add(user_lang)
            await session.commit()
            print(f"Success: Created user '{username}' (Role: {role}) in database.")
    except Exception as e:
        print(f"Error during user creation: {e}")


def main():
    try:
        parser = argparse.ArgumentParser(
            description="Forcefully create a user in FreeLingo database."
        )
        parser.add_argument("--username", required=True, help="Username for the new user")
        parser.add_argument("--password", required=True, help="Plaintext password")
        parser.add_argument("--email", default="", help="Email address")
        parser.add_argument("--display-name", default="", help="Display Name")
        parser.add_argument(
            "--role", default="user", choices=["user", "admin"], help="User role (user or admin)"
        )
        parser.add_argument("--native-lang", default="en-US", help="Native language (BCP-47 code)")
        parser.add_argument("--target-lang", default="en-GB", help="Target language (BCP-47 code)")

        args = parser.parse_args()
        asyncio.run(
            create_user(
                username=args.username,
                email=args.email,
                display_name=args.display_name,
                password_plain=args.password,
                role=args.role,
                native_lang=args.native_lang,
                target_lang=args.target_lang,
            )
        )
    except Exception as e:
        print(f"Error running main script: {e}")


if __name__ == "__main__":
    main()
