"""Generate login credentials: `uv run python -m myvinyl.credentials`.

Prompts for a password (not echoed) and prints the environment variables to set.
"""

import getpass
import secrets
import sys

from .auth import hash_password

MIN_PASSWORD_LEN = 12


def main() -> int:
    password = getpass.getpass("New password: ")
    if len(password) < MIN_PASSWORD_LEN:
        print(f"Password must be at least {MIN_PASSWORD_LEN} characters.", file=sys.stderr)
        return 1
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1

    print("\nSet these environment variables (keep them out of version control):\n")
    print(f"MYVINYL_SECRET_KEY={secrets.token_urlsafe(48)}")
    print(f"MYVINYL_PASSWORD_HASH={hash_password(password)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
