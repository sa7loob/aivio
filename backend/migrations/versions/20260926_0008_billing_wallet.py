"""Libyan-native billing: LYD wallet + ledger, plans, prepaid subscriptions, receipts,
platform vouchers, manual payments (bank / balance transfer), lifecycle.

قواعد المال (تُفرض داخل قاعدة البيانات، لا في كود التطبيق فقط):
  1. العملة الوحيدة: الدينار الليبي، numeric(14,3) (الدينار = 1000 درهم). لا float إطلاقاً.
  2. لا يوجد دور يستطيع UPDATE على wallets أو INSERT في wallet_ledger مباشرة.
     كل حركة مال = wallet_apply() فقط (SECURITY DEFINER): قفل صف المحفظة، قيد في الدفتر،
     تحقق من عدم السالب، ومنع التكرار عبر (source, reference_id).
  3. الدفتر (wallet_ledger) append-only؛ الرصيد = مجموع القيود (يُتحقق منه باختبار).
  4. السند (receipt) يُرقَّم تسلسلياً بدون فجوات: RC-2026-000001.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-26
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

SOURCES = ("('voucher','bank_transfer','libyana_balance','almadar_balance','plutu','cash',"
           "'admin_adjustment','subscription','refund')")

# التعريف الأصلي من 0002 (يُستعاد عند downgrade)
CLAIM_DUE_ORIGINAL = """
CREATE OR REPLACE FUNCTION claim_due_conversations(p_batch int DEFAULT 20, p_lease interval DEFAULT '2 minutes')
RETURNS TABLE (conversation_id uuid, tenant_id uuid, reply_due_at timestamptz)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    WITH due AS (
        SELECT c.id FROM conversations c
        WHERE c.reply_due_at <= now() AND c.mode = 'bot'
          AND (c.bot_paused_until IS NULL OR c.bot_paused_until < now())
          AND (c.reply_lease_until IS NULL OR c.reply_lease_until < now())
        ORDER BY c.reply_due_at LIMIT p_batch FOR UPDATE SKIP LOCKED
    )
    UPDATE conversations c SET reply_lease_until = now() + p_lease
      FROM due WHERE c.id = due.id
    RETURNING c.id, c.tenant_id, c.reply_due_at
$$;
"""


def upgrade() -> None:
    # ================================================================ plans (كتالوج عام)
    op.execute("""
    CREATE TABLE plans (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        code        text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9_-]{2,40}$'),
        name        text NOT NULL,
        description text,
        -- {"conversations_month": 1000, "seats": 3, "channels": 2, "knowledge_mb": 50}
        limits      jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(limits) = 'object'),
        is_active   boolean NOT NULL DEFAULT true,
        sort_order  smallint NOT NULL DEFAULT 0,
        created_at  timestamptz NOT NULL DEFAULT now(),
        updated_at  timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE plan_prices (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        plan_id       uuid NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
        period_months smallint NOT NULL CHECK (period_months IN (1, 3, 6, 12)),
        price_lyd     numeric(14,3) NOT NULL CHECK (price_lyd > 0),
        is_active     boolean NOT NULL DEFAULT true,
        created_at    timestamptz NOT NULL DEFAULT now(),
        UNIQUE (plan_id, period_months)
    );
    CREATE TRIGGER plans_set_updated_at BEFORE UPDATE ON plans
        FOR EACH ROW EXECUTE FUNCTION app_set_updated_at();
    """)
    # الوكالات تقرأ الخطط فقط
    op.execute("REVOKE INSERT, UPDATE, DELETE ON plans, plan_prices FROM app_user")

    # ================================================================ wallet + ledger
    op.execute(f"""
    CREATE TABLE wallets (
        tenant_id   uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
        balance_lyd numeric(14,3) NOT NULL DEFAULT 0 CHECK (balance_lyd >= 0),
        updated_at  timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE wallet_ledger (
        id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        direction     text NOT NULL CHECK (direction IN ('credit','debit')),
        amount_lyd    numeric(14,3) NOT NULL CHECK (amount_lyd > 0),
        balance_after numeric(14,3) NOT NULL CHECK (balance_after >= 0),
        source        text NOT NULL CHECK (source IN {SOURCES}),
        reference_id  uuid,                 -- قسيمة / تحويل / فترة اشتراك / مفتاح idempotency
        note          text,
        actor_type    text NOT NULL CHECK (actor_type IN ('admin','system','tenant_user')),
        actor_id      uuid,
        created_at    timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, id)
    );
    -- نفس المرجع لا يُقيَّد مرتين (نقرة مزدوجة، إعادة إرسال webhook، موافقة مكررة)
    CREATE UNIQUE INDEX wallet_ledger_idempotency_uq
        ON wallet_ledger (source, reference_id) WHERE reference_id IS NOT NULL;
    CREATE INDEX wallet_ledger_tenant_time_idx ON wallet_ledger (tenant_id, created_at DESC);
    """)

    # ================================================================ receipts (سندات القبض)
    op.execute(f"""
    CREATE TABLE receipt_counters (
        year       int PRIMARY KEY,
        last_value int NOT NULL
    );

    CREATE TABLE receipts (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        number      text NOT NULL UNIQUE,
        amount_lyd  numeric(14,3) NOT NULL CHECK (amount_lyd > 0),
        method      text NOT NULL CHECK (method IN {SOURCES}),
        reference   text,
        ledger_id   bigint NOT NULL UNIQUE,
        issued_by   uuid,                    -- admin_users.id (NULL = النظام)
        issued_at   timestamptz NOT NULL DEFAULT now(),
        pdf_key     text,                    -- المرحلة 7: PDF في Object Storage
        FOREIGN KEY (tenant_id, ledger_id) REFERENCES wallet_ledger (tenant_id, id)
    );
    CREATE INDEX receipts_tenant_idx ON receipts (tenant_id, issued_at DESC);
    """)

    # ================================================================ subscriptions
    op.execute("""
    CREATE TABLE subscriptions (
        id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id            uuid NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
        plan_id              uuid NOT NULL REFERENCES plans(id),
        status               text NOT NULL
                             CHECK (status IN ('trialing','active','grace','suspended','cancelled')),
        current_period_start timestamptz NOT NULL DEFAULT now(),
        current_period_end   timestamptz NOT NULL,            -- الخدمة مدفوعة حتى هذا الوقت
        grace_until          timestamptz,
        grace_days           smallint NOT NULL DEFAULT 3 CHECK (grace_days BETWEEN 0 AND 30),
        auto_renew           boolean NOT NULL DEFAULT true,   -- تجديد تلقائي من رصيد المحفظة
        renewal_price_id     uuid REFERENCES plan_prices(id),
        created_at           timestamptz NOT NULL DEFAULT now(),
        updated_at           timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, id)
    );
    CREATE INDEX subscriptions_due_idx ON subscriptions (current_period_end)
        WHERE status IN ('trialing','active');
    CREATE INDEX subscriptions_grace_idx ON subscriptions (grace_until) WHERE status = 'grace';
    CREATE TRIGGER subscriptions_set_updated_at BEFORE UPDATE ON subscriptions
        FOR EACH ROW EXECUTE FUNCTION app_set_updated_at();

    CREATE TABLE subscription_periods (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       uuid NOT NULL,
        subscription_id uuid NOT NULL,
        plan_price_id   uuid REFERENCES plan_prices(id),
        period_start    timestamptz NOT NULL,
        period_end      timestamptz NOT NULL CHECK (period_end > period_start),
        amount_lyd      numeric(14,3) NOT NULL CHECK (amount_lyd >= 0),
        ledger_id       bigint,
        source          text NOT NULL CHECK (source IN ('renewal','manual','trial','admin_grant')),
        created_by      uuid,
        created_at      timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, subscription_id) REFERENCES subscriptions (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, ledger_id) REFERENCES wallet_ledger (tenant_id, id)
    );
    CREATE INDEX subscription_periods_tenant_idx ON subscription_periods (tenant_id, period_start DESC);
    """)

    # ================================================================ vouchers (قسائم المنصة)
    op.execute("""
    CREATE TABLE voucher_batches (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        distributor_name text NOT NULL,
        denomination_lyd numeric(14,3) NOT NULL CHECK (denomination_lyd > 0),
        quantity         int NOT NULL CHECK (quantity BETWEEN 1 AND 5000),
        expires_at       timestamptz,
        status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','void')),
        void_reason      text,
        created_by       uuid REFERENCES admin_users(id),
        created_at       timestamptz NOT NULL DEFAULT now(),
        voided_at        timestamptz
    );

    CREATE TABLE vouchers (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        batch_id           uuid NOT NULL REFERENCES voucher_batches(id) ON DELETE CASCADE,
        code_hash          text NOT NULL UNIQUE,     -- HMAC-SHA256(pepper, code): لا يُخزَّن الرمز أبداً
        last4              char(4) NOT NULL,
        amount_lyd         numeric(14,3) NOT NULL CHECK (amount_lyd > 0),
        status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active','redeemed','void')),
        redeemed_tenant_id uuid REFERENCES tenants(id),
        redeemed_at        timestamptz,
        redeemed_ledger_id bigint,
        created_at         timestamptz NOT NULL DEFAULT now(),
        CHECK ((status = 'redeemed') = (redeemed_tenant_id IS NOT NULL))
    );
    CREATE INDEX vouchers_batch_idx ON vouchers (batch_id, status);

    -- لحماية القسائم من التخمين: محاولات فاشلة لكل وكالة
    CREATE TABLE voucher_redeem_attempts (
        id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        success    boolean NOT NULL,
        voucher_id uuid,
        ip         inet,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX voucher_attempts_tenant_idx ON voucher_redeem_attempts (tenant_id, created_at DESC);
    """)
    # جداول القسائم لا تُقرأ من دور التشغيل؛ الاسترداد عبر redeem_voucher() فقط
    op.execute("REVOKE ALL ON voucher_batches, vouchers FROM app_user")

    # ================================================================ manual payments
    op.execute("""
    CREATE TABLE manual_payments (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        method           text NOT NULL CHECK (method IN ('bank_transfer','libyana_balance','almadar_balance','cash')),
        amount_lyd       numeric(14,3) NOT NULL CHECK (amount_lyd > 0),
        sender_name      text,
        sender_phone     text,
        bank_reference   text,
        proof_url        text,                  -- المرحلة 6: رفع الملف إلى Object Storage
        note             text,
        status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
        submitted_by     text NOT NULL DEFAULT 'admin' CHECK (submitted_by IN ('admin','tenant_user')),
        reviewed_by      uuid,
        reviewed_at      timestamptz,
        rejection_reason text,
        ledger_id        bigint,
        created_at       timestamptz NOT NULL DEFAULT now(),
        CHECK (status <> 'rejected' OR rejection_reason IS NOT NULL),
        CHECK ((status = 'approved') = (ledger_id IS NOT NULL)),
        FOREIGN KEY (tenant_id, ledger_id) REFERENCES wallet_ledger (tenant_id, id)
    );
    CREATE INDEX manual_payments_pending_idx ON manual_payments (created_at) WHERE status = 'pending';
    CREATE INDEX manual_payments_tenant_idx  ON manual_payments (tenant_id, created_at DESC);
    """)

    # ================================================================ RLS
    for t in ("wallets", "wallet_ledger", "receipts", "subscriptions", "subscription_periods",
              "voucher_redeem_attempts", "manual_payments"):
        op.execute(f"SELECT app_enable_tenant_rls('{t}')")

    # الوكالة تقرأ ماليتها فقط؛ أي تعديل يمر عبر دوال
    op.execute("""
    REVOKE INSERT, UPDATE, DELETE ON wallets, wallet_ledger, receipts, subscriptions,
                                     subscription_periods, voucher_redeem_attempts
      FROM app_user;
    REVOKE UPDATE, DELETE ON manual_payments FROM app_user;   -- الوكالة ترفع طلب تحويل فقط
    REVOKE ALL ON receipt_counters FROM app_user;
    -- حتى المشرف لا يعدّل الأرصدة والدفتر والسندات مباشرة
    REVOKE INSERT, UPDATE, DELETE ON wallets, wallet_ledger, receipts, receipt_counters,
                                     subscription_periods, voucher_redeem_attempts
      FROM app_admin;
    """)

    # ================================================================ money functions
    op.execute("""
    CREATE FUNCTION wallet_apply(
        p_tenant uuid, p_direction text, p_amount numeric, p_source text,
        p_reference_id uuid, p_note text, p_actor_type text, p_actor_id uuid
    ) RETURNS wallet_ledger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE
        w wallets;
        e wallet_ledger;
        new_balance numeric(14,3);
    BEGIN
        IF p_amount IS NULL OR p_amount <= 0 OR p_amount <> round(p_amount, 3) THEN
            RAISE EXCEPTION 'invalid_amount' USING ERRCODE = 'BL003';
        END IF;

        INSERT INTO wallets (tenant_id) VALUES (p_tenant) ON CONFLICT DO NOTHING;
        SELECT * INTO w FROM wallets WHERE tenant_id = p_tenant FOR UPDATE;   -- تسلسل حركات المحفظة

        -- Idempotency: نفس المرجع => نعيد القيد الأصلي بدل قيد ثانٍ
        IF p_reference_id IS NOT NULL THEN
            SELECT * INTO e FROM wallet_ledger WHERE source = p_source AND reference_id = p_reference_id;
            IF FOUND THEN
                IF e.tenant_id <> p_tenant OR e.amount_lyd <> p_amount OR e.direction <> p_direction THEN
                    RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = 'BL002';
                END IF;
                RETURN e;
            END IF;
        END IF;

        new_balance := CASE p_direction
                           WHEN 'credit' THEN w.balance_lyd + p_amount
                           WHEN 'debit'  THEN w.balance_lyd - p_amount
                       END;
        IF new_balance IS NULL THEN
            RAISE EXCEPTION 'invalid_direction' USING ERRCODE = 'BL003';
        END IF;
        IF new_balance < 0 THEN
            RAISE EXCEPTION 'insufficient_funds'
                USING ERRCODE = 'BL001', DETAIL = format('balance=%s required=%s', w.balance_lyd, p_amount);
        END IF;

        UPDATE wallets SET balance_lyd = new_balance, updated_at = now() WHERE tenant_id = p_tenant;
        INSERT INTO wallet_ledger (tenant_id, direction, amount_lyd, balance_after, source,
                                   reference_id, note, actor_type, actor_id)
        VALUES (p_tenant, p_direction, p_amount, new_balance, p_source,
                p_reference_id, p_note, p_actor_type, p_actor_id)
        RETURNING * INTO e;
        RETURN e;
    END $$;

    -- رقم سند تسلسلي بدون فجوات (قفل صف السنة يسلسل الإصدار؛ rollback لا يترك فجوة)
    CREATE FUNCTION next_receipt_number() RETURNS text
    LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        INSERT INTO receipt_counters (year, last_value)
        VALUES (extract(year FROM now() AT TIME ZONE 'Africa/Tripoli')::int, 1)
        ON CONFLICT (year) DO UPDATE SET last_value = receipt_counters.last_value + 1
        RETURNING 'RC-' || year || '-' || lpad(last_value::text, 6, '0')
    $$;

    CREATE FUNCTION issue_receipt(p_ledger_id bigint, p_reference text, p_issued_by uuid)
    RETURNS receipts
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE e wallet_ledger; r receipts;
    BEGIN
        SELECT * INTO e FROM wallet_ledger WHERE id = p_ledger_id;
        IF NOT FOUND OR e.direction <> 'credit' THEN
            RAISE EXCEPTION 'receipt_requires_credit_entry' USING ERRCODE = 'BL003';
        END IF;
        SELECT * INTO r FROM receipts WHERE ledger_id = p_ledger_id;
        IF FOUND THEN RETURN r; END IF;                       -- idempotent
        INSERT INTO receipts (tenant_id, number, amount_lyd, method, reference, ledger_id, issued_by)
        VALUES (e.tenant_id, next_receipt_number(), e.amount_lyd, e.source, p_reference, e.id, p_issued_by)
        RETURNING * INTO r;
        RETURN r;
    END $$;
    """)

    # ================================================================ subscription functions
    op.execute("""
    -- تجديد مدفوع من المحفظة: الخصم + الفترة + تحديث الاشتراك في transaction واحدة
    -- p_period_id = مفتاح idempotency من العميل (نقرة مزدوجة لا تخصم مرتين)
    CREATE FUNCTION subscription_renew(p_tenant uuid, p_price_id uuid, p_actor_type text, p_actor_id uuid,
                                       p_period_id uuid DEFAULT NULL)
    RETURNS subscription_periods
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE
        s subscriptions; pp plan_prices; pl plans; e wallet_ledger; per subscription_periods;
        v_start timestamptz; v_end timestamptz; v_period_id uuid := coalesce(p_period_id, gen_random_uuid());
    BEGIN
        SELECT * INTO s FROM subscriptions WHERE tenant_id = p_tenant FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'subscription_not_found' USING ERRCODE = 'BL004'; END IF;
        SELECT * INTO per FROM subscription_periods WHERE id = v_period_id;
        IF FOUND THEN
            IF per.tenant_id <> p_tenant THEN RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = 'BL002'; END IF;
            RETURN per;                                   -- إعادة نفس الطلب
        END IF;
        IF s.status = 'cancelled' THEN RAISE EXCEPTION 'subscription_cancelled' USING ERRCODE = 'BL004'; END IF;

        SELECT * INTO pp FROM plan_prices WHERE id = p_price_id AND is_active;
        SELECT * INTO pl FROM plans WHERE id = pp.plan_id AND is_active;
        IF pp.id IS NULL OR pl.id IS NULL THEN
            RAISE EXCEPTION 'plan_price_unavailable' USING ERRCODE = 'BL005';
        END IF;

        -- التجديد المبكر يُضاف بعد نهاية الفترة الحالية؛ المتأخر يبدأ من الآن
        v_start := greatest(now(), s.current_period_end);
        v_end   := v_start + make_interval(months => pp.period_months);

        e := wallet_apply(p_tenant, 'debit', pp.price_lyd, 'subscription', v_period_id,
                          format('اشتراك %s - %s شهر', pl.name, pp.period_months), p_actor_type, p_actor_id);

        INSERT INTO subscription_periods (id, tenant_id, subscription_id, plan_price_id, period_start,
                                          period_end, amount_lyd, ledger_id, source, created_by)
        VALUES (v_period_id, p_tenant, s.id, pp.id, v_start, v_end, pp.price_lyd, e.id,
                CASE WHEN p_actor_type = 'system' THEN 'renewal' ELSE 'manual' END, p_actor_id)
        RETURNING * INTO per;

        UPDATE subscriptions
           SET plan_id = pp.plan_id,
               status = 'active',
               current_period_start = CASE WHEN s.status IN ('trialing','active') AND s.current_period_end > now()
                                           THEN s.current_period_start ELSE v_start END,
               current_period_end = v_end,
               grace_until = NULL,
               renewal_price_id = pp.id
         WHERE id = s.id;
        RETURN per;
    END $$;

    -- منحة مجانية من المشرف (العميل التجريبي، تعويض عن انقطاع...) بدون خصم
    CREATE FUNCTION subscription_grant(p_tenant uuid, p_plan_id uuid, p_days int, p_admin uuid)
    RETURNS subscription_periods
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE s subscriptions; per subscription_periods; v_start timestamptz;
    BEGIN
        IF p_days IS NULL OR p_days NOT BETWEEN 1 AND 366 THEN
            RAISE EXCEPTION 'invalid_days' USING ERRCODE = 'BL003';
        END IF;
        SELECT * INTO s FROM subscriptions WHERE tenant_id = p_tenant FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'subscription_not_found' USING ERRCODE = 'BL004'; END IF;
        v_start := greatest(now(), s.current_period_end);
        INSERT INTO subscription_periods (tenant_id, subscription_id, period_start, period_end,
                                          amount_lyd, source, created_by)
        VALUES (p_tenant, s.id, v_start, v_start + make_interval(days => p_days), 0, 'admin_grant', p_admin)
        RETURNING * INTO per;
        UPDATE subscriptions
           SET plan_id = coalesce(p_plan_id, plan_id),
               status = CASE WHEN status = 'trialing' THEN 'trialing' ELSE 'active' END,
               current_period_end = per.period_end,
               grace_until = NULL
         WHERE id = s.id;
        RETURN per;
    END $$;

    -- بعد أي شحن: إذا كان الاشتراك منتهياً/في المهلة والتجديد التلقائي مفعّلاً والرصيد يكفي => جدّد
    CREATE FUNCTION subscription_try_autorenew(p_tenant uuid)
    RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE s subscriptions;
    BEGIN
        SELECT * INTO s FROM subscriptions WHERE tenant_id = p_tenant;
        IF NOT FOUND OR NOT s.auto_renew OR s.renewal_price_id IS NULL
           OR s.status NOT IN ('grace','suspended')
           AND NOT (s.status IN ('trialing','active') AND s.current_period_end <= now()) THEN
            RETURN false;
        END IF;
        BEGIN
            PERFORM subscription_renew(p_tenant, s.renewal_price_id, 'system', NULL);
            RETURN true;
        EXCEPTION WHEN SQLSTATE 'BL001' THEN     -- رصيد غير كافٍ
            RETURN false;
        END;
    END $$;

    -- مهمة دورية (الـ worker كل ساعة): تجديد تلقائي / مهلة / إيقاف
    CREATE FUNCTION subscription_lifecycle_tick()
    RETURNS TABLE (tenant_id uuid, old_status text, new_status text)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE s subscriptions; renewed boolean;
    BEGIN
        FOR s IN
            SELECT * FROM subscriptions x
             WHERE (x.status IN ('trialing','active') AND x.current_period_end <= now())
                OR (x.status = 'grace' AND x.grace_until <= now())
             ORDER BY x.current_period_end
             FOR UPDATE SKIP LOCKED
        LOOP
            renewed := false;
            IF s.auto_renew AND s.renewal_price_id IS NOT NULL THEN
                BEGIN
                    PERFORM subscription_renew(s.tenant_id, s.renewal_price_id, 'system', NULL);
                    renewed := true;
                EXCEPTION WHEN SQLSTATE 'BL001' OR SQLSTATE 'BL005' THEN
                    renewed := false;
                END;
            END IF;

            IF renewed THEN
                tenant_id := s.tenant_id; old_status := s.status; new_status := 'active';
            ELSIF s.status IN ('trialing','active') THEN
                UPDATE subscriptions SET status = 'grace',
                       grace_until = now() + make_interval(days => s.grace_days)
                 WHERE id = s.id;
                tenant_id := s.tenant_id; old_status := s.status; new_status := 'grace';
            ELSE
                UPDATE subscriptions SET status = 'suspended' WHERE id = s.id;
                tenant_id := s.tenant_id; old_status := s.status; new_status := 'suspended';
            END IF;
            RETURN NEXT;
        END LOOP;
    END $$;
    """)

    # ================================================================ voucher redemption
    op.execute("""
    -- يُستدعى داخل سياق وكالة (app.tenant_id). لا يرمي استثناءً عند الرمز الخاطئ حتى يبقى
    -- سجل المحاولة الفاشلة محفوظاً (وإلا يُلغى مع rollback ويسقط حد المحاولات).
    CREATE FUNCTION redeem_voucher(p_code_hash text, p_ip inet, p_actor_type text, p_actor_id uuid)
    RETURNS TABLE (ok boolean, reason text, amount_lyd numeric, balance_lyd numeric,
                   ledger_id bigint, voucher_id uuid)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE
        t uuid := app_current_tenant_id();
        fails int;
        rv vouchers;
        e wallet_ledger;
    BEGIN
        IF t IS NULL THEN RAISE EXCEPTION 'tenant_context_required' USING ERRCODE = 'BL004'; END IF;

        SELECT count(*) INTO fails FROM voucher_redeem_attempts a
         WHERE a.tenant_id = t AND NOT a.success AND a.created_at > now() - interval '15 minutes';
        IF fails >= 5 THEN
            RETURN QUERY SELECT false, 'too_many_attempts'::text, NULL::numeric, NULL::numeric, NULL::bigint, NULL::uuid;
            RETURN;
        END IF;

        UPDATE vouchers v
           SET status = 'redeemed', redeemed_tenant_id = t, redeemed_at = now()
          FROM voucher_batches b
         WHERE v.code_hash = p_code_hash AND v.status = 'active'
           AND b.id = v.batch_id AND b.status = 'active'
           AND (b.expires_at IS NULL OR b.expires_at > now())
        RETURNING v.* INTO rv;

        IF rv.id IS NULL THEN
            -- نقرة مزدوجة من نفس الوكالة => نجاح idempotent بدل خطأ مربك
            SELECT * INTO rv FROM vouchers v WHERE v.code_hash = p_code_hash AND v.redeemed_tenant_id = t;
            IF rv.id IS NOT NULL THEN
                RETURN QUERY SELECT true, 'already_redeemed_by_you'::text, rv.amount_lyd,
                    (SELECT w.balance_lyd FROM wallets w WHERE w.tenant_id = t), rv.redeemed_ledger_id, rv.id;
                RETURN;
            END IF;
            INSERT INTO voucher_redeem_attempts (tenant_id, success, ip) VALUES (t, false, p_ip);
            RETURN QUERY SELECT false, 'invalid_or_used'::text, NULL::numeric, NULL::numeric, NULL::bigint, NULL::uuid;
            RETURN;
        END IF;

        e := wallet_apply(t, 'credit', rv.amount_lyd, 'voucher', rv.id,
                          'قسيمة ****' || rv.last4, p_actor_type, p_actor_id);
        UPDATE vouchers SET redeemed_ledger_id = e.id WHERE id = rv.id;
        INSERT INTO voucher_redeem_attempts (tenant_id, success, voucher_id, ip) VALUES (t, true, rv.id, p_ip);
        RETURN QUERY SELECT true, NULL::text, rv.amount_lyd, e.balance_after, e.id, rv.id;
    END $$;
    """)

    # ================================================================ grants
    op.execute("""
    REVOKE ALL ON FUNCTION wallet_apply(uuid, text, numeric, text, uuid, text, text, uuid) FROM PUBLIC;
    REVOKE ALL ON FUNCTION next_receipt_number() FROM PUBLIC;
    REVOKE ALL ON FUNCTION issue_receipt(bigint, text, uuid) FROM PUBLIC;
    REVOKE ALL ON FUNCTION subscription_renew(uuid, uuid, text, uuid, uuid) FROM PUBLIC;
    REVOKE ALL ON FUNCTION subscription_grant(uuid, uuid, int, uuid) FROM PUBLIC;
    REVOKE ALL ON FUNCTION subscription_try_autorenew(uuid) FROM PUBLIC;
    REVOKE ALL ON FUNCTION subscription_lifecycle_tick() FROM PUBLIC;
    REVOKE ALL ON FUNCTION redeem_voucher(text, inet, text, uuid) FROM PUBLIC;

    -- المشرف: كل عمليات المال عبر الدوال
    GRANT EXECUTE ON FUNCTION wallet_apply(uuid, text, numeric, text, uuid, text, text, uuid) TO app_admin;
    GRANT EXECUTE ON FUNCTION issue_receipt(bigint, text, uuid) TO app_admin;
    GRANT EXECUTE ON FUNCTION subscription_renew(uuid, uuid, text, uuid, uuid) TO app_admin;
    GRANT EXECUTE ON FUNCTION subscription_grant(uuid, uuid, int, uuid) TO app_admin;
    GRANT EXECUTE ON FUNCTION subscription_try_autorenew(uuid) TO app_admin;
    GRANT EXECUTE ON FUNCTION redeem_voucher(text, inet, text, uuid) TO app_admin;
    GRANT EXECUTE ON FUNCTION subscription_lifecycle_tick() TO app_admin;

    -- دور التشغيل: استرداد القسيمة من لوحة الوكالة (المرحلة 6) + المهمة الدورية للـ worker
    GRANT EXECUTE ON FUNCTION redeem_voucher(text, inet, text, uuid) TO app_user;
    GRANT EXECUTE ON FUNCTION subscription_try_autorenew(uuid) TO app_user;
    GRANT EXECUTE ON FUNCTION subscription_lifecycle_tick() TO app_user;
    """)

    # ================================================================ enforcement in the bot
    # الوكالة الموقوفة: الرسائل تُخزَّن كالمعتاد، لكن البوت لا يرد
    op.execute("""
    CREATE OR REPLACE FUNCTION claim_due_conversations(p_batch int DEFAULT 20, p_lease interval DEFAULT '2 minutes')
    RETURNS TABLE (conversation_id uuid, tenant_id uuid, reply_due_at timestamptz)
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        WITH due AS (
            SELECT c.id FROM conversations c
            WHERE c.reply_due_at <= now() AND c.mode = 'bot'
              AND (c.bot_paused_until IS NULL OR c.bot_paused_until < now())
              AND (c.reply_lease_until IS NULL OR c.reply_lease_until < now())
              AND NOT EXISTS (SELECT 1 FROM subscriptions s
                               WHERE s.tenant_id = c.tenant_id AND s.status IN ('suspended','cancelled'))
            ORDER BY c.reply_due_at LIMIT p_batch FOR UPDATE SKIP LOCKED
        )
        UPDATE conversations c SET reply_lease_until = now() + p_lease
          FROM due WHERE c.id = due.id
        RETURNING c.id, c.tenant_id, c.reply_due_at
    $$;
    """)


def downgrade() -> None:
    op.execute(CLAIM_DUE_ORIGINAL)
    op.execute("""
    DROP FUNCTION IF EXISTS redeem_voucher(text, inet, text, uuid);
    DROP FUNCTION IF EXISTS subscription_lifecycle_tick();
    DROP FUNCTION IF EXISTS subscription_try_autorenew(uuid);
    DROP FUNCTION IF EXISTS subscription_grant(uuid, uuid, int, uuid);
    DROP FUNCTION IF EXISTS subscription_renew(uuid, uuid, text, uuid, uuid);
    DROP FUNCTION IF EXISTS issue_receipt(bigint, text, uuid);
    DROP FUNCTION IF EXISTS next_receipt_number();
    DROP FUNCTION IF EXISTS wallet_apply(uuid, text, numeric, text, uuid, text, text, uuid);
    DROP TABLE IF EXISTS manual_payments;
    DROP TABLE IF EXISTS voucher_redeem_attempts;
    DROP TABLE IF EXISTS vouchers;
    DROP TABLE IF EXISTS voucher_batches;
    DROP TABLE IF EXISTS subscription_periods;
    DROP TABLE IF EXISTS subscriptions;
    DROP TABLE IF EXISTS receipts;
    DROP TABLE IF EXISTS receipt_counters;
    DROP TABLE IF EXISTS wallet_ledger;
    DROP TABLE IF EXISTS wallets;
    DROP TABLE IF EXISTS plan_prices;
    DROP TABLE IF EXISTS plans;
    """)
