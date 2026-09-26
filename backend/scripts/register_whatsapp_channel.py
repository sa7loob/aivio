"""Register (or update) a tenant and its WhatsApp number — admin task, runs as app_owner.

    python -m scripts.register_whatsapp_channel \
        --tenant-slug noor-travel --tenant-name "وكالة النور" \
        --phone-number-id 1234567890 --waba-id 9876543210 --test

التوكن يُقرأ من المتغير WHATSAPP_ACCESS_TOKEN (لا تمرره في سطر الأوامر: يبقى في history).
"""
from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, text

from app.core.crypto import encrypt_token


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant-slug", required=True)
    ap.add_argument("--tenant-name", required=True)
    ap.add_argument("--phone-number-id", required=True)
    ap.add_argument("--waba-id")
    ap.add_argument("--display-name")
    ap.add_argument("--test", action="store_true", help="رقم اختبار (Meta test number)")
    args = ap.parse_args()

    token = os.environ.get("WHATSAPP_ACCESS_TOKEN")
    owner_url = os.environ.get("MIGRATIONS_DATABASE_URL")
    if not token or not owner_url:
        print("WHATSAPP_ACCESS_TOKEN and MIGRATIONS_DATABASE_URL are required", file=sys.stderr)
        return 2

    engine = create_engine(owner_url)
    with engine.begin() as conn:
        tenant_id = conn.execute(text("""
            INSERT INTO tenants (slug, name) VALUES (:slug, :name)
            ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
        """), {"slug": args.tenant_slug, "name": args.tenant_name}).scalar_one()

        conn.execute(text("""
            INSERT INTO channel_accounts (tenant_id, channel, external_id, waba_id, display_name,
                                          access_token_enc, is_test, status)
            VALUES (:tenant_id, 'whatsapp', :external_id, :waba_id, :display_name, :token, :is_test, 'active')
            ON CONFLICT (channel, external_id) DO UPDATE
               SET access_token_enc = EXCLUDED.access_token_enc,
                   waba_id = coalesce(EXCLUDED.waba_id, channel_accounts.waba_id),
                   display_name = coalesce(EXCLUDED.display_name, channel_accounts.display_name),
                   is_test = EXCLUDED.is_test,
                   status = 'active'
             WHERE channel_accounts.tenant_id = EXCLUDED.tenant_id
        """), {
            "tenant_id": tenant_id, "external_id": args.phone_number_id, "waba_id": args.waba_id,
            "display_name": args.display_name, "token": encrypt_token(token), "is_test": args.test,
        })
    print(f"tenant {args.tenant_slug} ({tenant_id}) -> whatsapp {args.phone_number_id} registered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
