"""Create a platform admin and print an API token ONCE (runs as app_owner).

    python -m scripts.create_admin --email ops@example.ly --name "مدير المنصة" --role superadmin
    python -m scripts.create_admin --email ops@example.ly --new-token --label laptop   # توكن إضافي
    python -m scripts.create_admin --revoke-all --email ops@example.ly
"""
from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, text

from app.admin.auth import hash_token, new_token


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--name")
    ap.add_argument("--role", choices=["superadmin", "support", "finance"])
    ap.add_argument("--new-token", action="store_true")
    ap.add_argument("--revoke-all", action="store_true")
    ap.add_argument("--label")
    ap.add_argument("--expires-days", type=int, default=180)
    args = ap.parse_args()

    url = os.environ.get("MIGRATIONS_DATABASE_URL")
    if not url:
        print("MIGRATIONS_DATABASE_URL is required", file=sys.stderr)
        return 2

    with create_engine(url).begin() as conn:
        admin_id = conn.execute(text("SELECT id FROM admin_users WHERE lower(email) = lower(:e)"),
                                {"e": args.email}).scalar_one_or_none()
        if args.revoke_all:
            n = conn.execute(text("UPDATE admin_tokens SET revoked_at = now() WHERE admin_id = :a AND revoked_at IS NULL"),
                             {"a": admin_id}).rowcount
            print(f"revoked {n} token(s)")
            return 0
        if admin_id is None:
            if not (args.name and args.role):
                print("--name and --role are required for a new admin", file=sys.stderr)
                return 2
            admin_id = conn.execute(text("""
                INSERT INTO admin_users (email, full_name, role) VALUES (:e, :n, :r) RETURNING id
            """), {"e": args.email, "n": args.name, "r": args.role}).scalar_one()
        elif not args.new_token:
            print("admin exists; use --new-token", file=sys.stderr)
            return 1
        token = new_token()
        conn.execute(text("""
            INSERT INTO admin_tokens (admin_id, token_hash, label, expires_at)
            VALUES (:a, :h, :l, now() + make_interval(days => :d))
        """), {"a": admin_id, "h": hash_token(token), "l": args.label, "d": args.expires_days})
    print("Admin token (shown once, store it in a password manager):")
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
