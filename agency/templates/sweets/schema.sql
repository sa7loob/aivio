-- قالب «محل حلويات»: بيانات البوت في قاعدة العميل (schema public). جداول n8n نفسها في schema n8n.
-- يُطبَّق بمستخدم العميل نفسه (deploy-bot.sh)، وآمن لإعادة التشغيل (IF NOT EXISTS).

-- إعدادات العميل: صف واحد فقط. الأسرار هنا (App Secret و Verify Token) لا تخرج من قاعدة العميل.
CREATE TABLE IF NOT EXISTS bot_settings (
    id                     int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    business_name          text NOT NULL,
    phone_number_id        text NOT NULL,                 -- معرّف رقم واتساب في Meta
    meta_app_secret        text NOT NULL,                 -- للتحقق من توقيع X-Hub-Signature-256
    meta_verify_token      text NOT NULL,                 -- لتحقق Meta عند ربط الـ webhook
    owner_phone            text NOT NULL,                 -- هاتف صاحب المحل للتنبيهات: 2189xxxxxxxx
    owner_template_name    text,                          -- قالب Utility معتمد؛ فارغ = رسالة نصية (داخل نافذة 24 ساعة فقط)
    owner_template_lang    text NOT NULL DEFAULT 'ar',
    opening_hours          text,
    address                text,
    delivery_info          text,                          -- مناطق التوصيل ورسومه
    order_notice           text,                          -- مثل: التورتات تُطلب قبل 24 ساعة
    extra_instructions     text,
    chat_model             text NOT NULL DEFAULT 'gpt-4o',
    transcription_model    text NOT NULL DEFAULT 'gpt-4o-transcribe',
    graph_base_url         text NOT NULL DEFAULT 'https://graph.facebook.com/v23.0',
    openai_base_url        text NOT NULL DEFAULT 'https://api.openai.com/v1',
    debounce_seconds       int  NOT NULL DEFAULT 4  CHECK (debounce_seconds BETWEEN 0 AND 30),
    audio_max_wait_seconds int  NOT NULL DEFAULT 45 CHECK (audio_max_wait_seconds BETWEEN 5 AND 120),
    history_days           int  NOT NULL DEFAULT 90 CHECK (history_days >= 1),
    updated_at             timestamptz NOT NULL DEFAULT now()
);

-- القائمة والأسعار بالدينار الليبي. البوت لا يذكر إلا ما هنا (available = true).
CREATE TABLE IF NOT EXISTS products (
    id          serial PRIMARY KEY,
    category    text NOT NULL,
    name        text NOT NULL UNIQUE,
    unit        text NOT NULL,                             -- كيلو، قطعة، علبة، صينية، تورتة...
    price_lyd   numeric(10,3) NOT NULL CHECK (price_lyd >= 0),
    min_qty     numeric(10,2) CHECK (min_qty > 0),
    description text,
    available   boolean NOT NULL DEFAULT true,
    sort_order  int NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS contacts (
    id              bigserial PRIMARY KEY,
    wa_id           text NOT NULL UNIQUE,                  -- رقم الزبون كما يرسله واتساب: 2189xxxxxxxx
    name            text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_inbound_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id            bigserial PRIMARY KEY,
    contact_id    bigint NOT NULL REFERENCES contacts (id) ON DELETE CASCADE,
    direction     text NOT NULL CHECK (direction IN ('in', 'out')),
    wa_message_id text UNIQUE,                             -- معرّف Meta: تكرار نفس الرسالة يُتجاهل
    type          text NOT NULL CHECK (type IN ('text', 'audio', 'other')),
    wa_type       text,                                    -- النوع الأصلي: image, sticker, location...
    text          text,                                    -- للصوت: نص التفريغ (NULL = لم يُفرَّغ بعد)
    media_failed  boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_contact_idx ON messages (contact_id, id);
CREATE INDEX IF NOT EXISTS messages_created_idx ON messages (created_at);

-- طلبات الزبائن المكتملة (الـ Lead). نفس الطلب لا يُسجَّل ولا يُبلَّغ عنه مرتين (fingerprint).
CREATE TABLE IF NOT EXISTS orders (
    id                  bigserial PRIMARY KEY,
    contact_id          bigint NOT NULL REFERENCES contacts (id) ON DELETE CASCADE,
    status              text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'confirmed', 'done', 'cancelled')),
    customer_name       text,
    phone               text,
    items               jsonb NOT NULL,
    fulfillment         text CHECK (fulfillment IN ('pickup', 'delivery')),
    address             text,
    needed_at           text,
    notes               text,
    estimated_total_lyd numeric(12,3),
    fingerprint         text NOT NULL,
    notified_at         timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (contact_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS workflow_errors (
    id           bigserial PRIMARY KEY,
    workflow     text,
    node         text,
    message      text,
    execution_id text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
