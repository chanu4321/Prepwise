"""PrepWise admin commands. Run from the project root (or /app in the container):

    python backend/admin_cli.py make-admin you@outlook.com
"""
import argparse
import sys


def make_admin(email: str, store, input_fn=input, out=print) -> bool:
    """Grants admin to the account that signed in with `email`, after confirmation."""
    matches = store.find_by_email(email)
    if not matches:
        out("No account with that email has signed in yet. Sign in to PrepWise once, then run this again.")
        return False

    if len(matches) == 1:
        target = matches[0]
        answer = input_fn(f"Make {target.name or target.email} (id {target.id}) an admin? [y/N] ")
        if answer.strip().lower() != "y":
            out("Cancelled.")
            return False
    else:
        out("Several accounts use this email (e.g. a work and a personal account):")
        for user in matches:
            created = user.created_at.strftime("%Y-%m-%d") if user.created_at else "?"
            out(f"  id {user.id}: {user.name or '-'} (tenant {user.ms_tid}, joined {created})")
        choice = input_fn("Enter the id to make admin (blank to cancel): ").strip()
        target = next((user for user in matches if str(user.id) == choice), None)
        if target is None:
            out("Cancelled.")
            return False

    store.update(target.id, role="admin", verified=True)
    out(f"{target.email} is now an admin.")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PrepWise admin commands")
    commands = parser.add_subparsers(dest="command", required=True)
    make_admin_parser = commands.add_parser("make-admin", help="Grant admin to an account that has signed in")
    make_admin_parser.add_argument("email")
    args = parser.parse_args(argv)

    if args.command == "make-admin":
        from auth.users import PostgresUserStore
        return 0 if make_admin(args.email, PostgresUserStore()) else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
