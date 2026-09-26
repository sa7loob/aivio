"""All SQL used by the webhook API and the worker, in one place.

- تُستخدم CAST(:x AS type) وليس :x::type (الصيغة الثانية تكسر ربط المتغيرات في SQLAlchemy text()).
- الاستعلامات التي تحمل (SYSTEM) تعمل بدون سياق وكالة؛ الباقي داخل tenant_session فقط.
"""
from sqlalchemy import text

# ------------------------------------------------------------------ webhook ingest (SYSTEM)
INSERT_WEBHOOK_EVENT = text("""
    INSERT INTO webhook_events (provider, object_type, payload)
    VALUES (:provider, :object_type, CAST(:payload AS jsonb))
    RETURNING id
""")

CLAIM_WEBHOOK_EVENTS = text("""
    UPDATE webhook_events e
       SET status = 'processing',
           locked_until = now() + make_interval(secs => :lease_seconds),
           attempts = e.attempts + 1
     WHERE e.id IN (
           SELECT id FROM webhook_events
            WHERE status IN ('pending', 'processing')
              AND next_attempt_at <= now()
              AND (locked_until IS NULL OR locked_until < now())
            ORDER BY id
            LIMIT :batch
            FOR UPDATE SKIP LOCKED)
    RETURNING e.id, e.object_type, e.payload, e.attempts
""")

FINISH_WEBHOOK_EVENT = text("""
    UPDATE webhook_events
       SET status = :status, processed_at = now(), locked_until = NULL, last_error = :error
     WHERE id = :id
""")

RETRY_WEBHOOK_EVENT = text("""
    UPDATE webhook_events
       SET status = 'pending', locked_until = NULL, last_error = :error,
           next_attempt_at = now() + make_interval(secs => :delay_seconds)
     WHERE id = :id
""")

# ------------------------------------------------------------------ routing (SYSTEM)
RESOLVE_CHANNEL_ACCOUNT = text("""
    SELECT channel_account_id, tenant_id, status
      FROM resolve_channel_account(:channel, :external_id)
""")

CLAIM_DUE_CONVERSATIONS = text("""
    SELECT conversation_id, tenant_id, reply_due_at
      FROM claim_due_conversations(:batch, make_interval(secs => :lease_seconds))
""")

CLAIM_PENDING_OUTBOUND = text("""
    SELECT outbound_id, tenant_id
      FROM claim_pending_outbound(:batch, make_interval(secs => :lease_seconds))
""")

# ------------------------------------------------------------------ inbound (TENANT)
UPSERT_CONTACT = text("""
    INSERT INTO contacts (tenant_id, channel, external_user_id, display_name, phone_e164)
    VALUES (app_current_tenant_id(), :channel, :external_user_id, :display_name, :phone_e164)
    ON CONFLICT (tenant_id, channel, external_user_id) DO UPDATE
       SET display_name = coalesce(EXCLUDED.display_name, contacts.display_name),
           phone_e164   = coalesce(contacts.phone_e164, EXCLUDED.phone_e164),
           last_seen_at = greatest(contacts.last_seen_at, now())
    RETURNING id
""")

# ذرّي: يعيد المحادثة المفتوحة أو ينشئ واحدة (يعتمد على الفهرس الجزئي conversations_one_open_uq)
UPSERT_OPEN_CONVERSATION = text("""
    INSERT INTO conversations (tenant_id, contact_id, channel_account_id)
    VALUES (app_current_tenant_id(), :contact_id, :channel_account_id)
    ON CONFLICT (tenant_id, contact_id, channel_account_id) WHERE mode <> 'closed'
    DO UPDATE SET updated_at = now()
    RETURNING id, mode
""")

INSERT_INBOUND_MESSAGE = text("""
    INSERT INTO messages (tenant_id, conversation_id, channel, direction, sender_type,
                          external_message_id, msg_type, text_content, payload, platform_ts)
    VALUES (app_current_tenant_id(), :conversation_id, :channel, 'inbound', 'customer',
            :external_message_id, :msg_type, :text_content, CAST(:payload AS jsonb), :platform_ts)
    ON CONFLICT (tenant_id, channel, external_message_id) DO NOTHING
    RETURNING id
""")

# Debounce: كل رسالة جديدة تؤجل موعد الرد
BUMP_CONVERSATION_ON_INBOUND = text("""
    UPDATE conversations
       SET last_inbound_at = greatest(coalesce(last_inbound_at, '-infinity'), :platform_ts),
           reply_due_at = CASE WHEN mode = 'bot'
                               THEN now() + make_interval(secs => :debounce_seconds)
                               ELSE reply_due_at END
     WHERE id = :conversation_id
""")

# ------------------------------------------------------------------ reply (TENANT)
LOCK_CONVERSATION = text("""
    SELECT c.id, c.mode, c.bot_paused_until, c.reply_due_at, c.channel_account_id,
           c.last_inbound_at, c.contact_id, ct.external_user_id AS recipient,
           ct.display_name AS contact_name, ct.phone_e164 AS contact_phone, ca.channel
      FROM conversations c
      JOIN contacts ct ON ct.tenant_id = c.tenant_id AND ct.id = c.contact_id
      JOIN channel_accounts ca ON ca.tenant_id = c.tenant_id AND ca.id = c.channel_account_id
     WHERE c.id = :conversation_id
       FOR UPDATE OF c
""")

PENDING_INBOUND_MESSAGES = text("""
    SELECT id, msg_type, text_content
      FROM messages
     WHERE conversation_id = :conversation_id
       AND direction = 'inbound'
       AND handled_at IS NULL
     ORDER BY created_at, id
       FOR UPDATE
""")

MARK_MESSAGES_HANDLED = text("""
    UPDATE messages SET handled_at = now() WHERE id = ANY(:ids)
""")

INSERT_OUTBOUND_MESSAGE_RECORD = text("""
    INSERT INTO messages (tenant_id, conversation_id, channel, direction, sender_type,
                          msg_type, text_content)
    VALUES (app_current_tenant_id(), :conversation_id, :channel, 'outbound', 'bot', 'text', :text_content)
    RETURNING id
""")

ENQUEUE_OUTBOUND = text("""
    INSERT INTO outbound_messages (tenant_id, channel_account_id, recipient, purpose,
                                   conversation_id, message_id, kind, body)
    VALUES (app_current_tenant_id(), :channel_account_id, :recipient, :purpose,
            :conversation_id, :message_id, :kind, CAST(:body AS jsonb))
    RETURNING id
""")

# يمسح موعد الرد فقط إذا لم تصل رسالة جديدة أثناء المعالجة
CLEAR_CONVERSATION_DUE = text("""
    UPDATE conversations
       SET reply_due_at = CASE WHEN reply_due_at = :claimed_due THEN NULL ELSE reply_due_at END,
           reply_lease_until = NULL
     WHERE id = :conversation_id
""")

RELEASE_CONVERSATION_LEASE = text("""
    UPDATE conversations SET reply_lease_until = NULL WHERE id = :conversation_id
""")

# ------------------------------------------------------------------ send (TENANT)
LOAD_OUTBOUND = text("""
    SELECT o.id, o.kind, o.body, o.recipient, o.purpose, o.attempts, o.message_id, o.channel_account_id,
           o.conversation_id, ca.channel, ca.external_id AS account_external_id,
           ca.access_token_enc, ca.status AS account_status, ca.config AS account_config,
           c.last_inbound_at
      FROM outbound_messages o
      JOIN channel_accounts ca ON ca.tenant_id = o.tenant_id AND ca.id = o.channel_account_id
      LEFT JOIN conversations c ON c.tenant_id = o.tenant_id AND c.id = o.conversation_id
     WHERE o.id = :outbound_id AND o.status = 'sending'
""")

MARK_OUTBOUND_SENT = text("""
    WITH o AS (
        UPDATE outbound_messages
           SET status = 'sent', sent_at = now(), locked_until = NULL, last_error = NULL,
               external_message_id = :external_message_id
         WHERE id = :outbound_id
     RETURNING message_id, conversation_id, lead_id
    ), m AS (
        UPDATE messages SET external_message_id = :external_message_id
         WHERE id = (SELECT message_id FROM o)
    ), l AS (
        UPDATE leads SET notified_at = coalesce(notified_at, now())
         WHERE id = (SELECT lead_id FROM o)
    )
    UPDATE conversations SET last_outbound_at = now()
     WHERE id = (SELECT conversation_id FROM o)
""")

MARK_OUTBOUND_RETRY = text("""
    UPDATE outbound_messages
       SET status = 'pending', locked_until = NULL, last_error = :error,
           next_attempt_at = now() + make_interval(secs => :delay_seconds)
     WHERE id = :outbound_id
""")

MARK_OUTBOUND_FAILED = text("""
    UPDATE outbound_messages
       SET status = :status, locked_until = NULL, last_error = :error
     WHERE id = :outbound_id
""")

# ================================================================== agent (TENANT)
LOAD_TENANT_PROFILE = text("""
    SELECT id, name, timezone, default_currency, settings FROM tenants
     WHERE id = app_current_tenant_id()
""")

LOAD_CONTACT = text("""
    SELECT id, channel, display_name, phone_e164 FROM contacts WHERE id = :contact_id
""")

# آخر N رسالة (الأحدث أولاً ثم نعكسها في Python)
LOAD_HISTORY = text("""
    SELECT direction, sender_type, msg_type, text_content, created_at
      FROM messages
     WHERE conversation_id = :conversation_id
     ORDER BY created_at DESC, id DESC
     LIMIT :limit
""")

INSERT_AGENT_RUN = text("""
    INSERT INTO agent_runs (tenant_id, conversation_id, reply_message_id, status, model, iterations,
                            input_tokens, output_tokens, latency_ms, tool_calls, handed_off, lead_id, error)
    VALUES (app_current_tenant_id(), :conversation_id, :reply_message_id, :status, :model, :iterations,
            :input_tokens, :output_tokens, :latency_ms, CAST(:tool_calls AS jsonb), :handed_off,
            :lead_id, :error)
""")

PAUSE_BOT = text("""
    UPDATE conversations
       SET bot_paused_until = now() + make_interval(hours => :hours)
     WHERE id = :conversation_id
""")

# ---- tool: search_packages
TOOL_SEARCH_PACKAGES = text("""
    SELECT s.package_id, s.title, s.kind, round(s.score::numeric, 2) AS score, s.matched_alias,
           o.next_departure, o.price_from
      FROM search_packages(:query, 20) s
      LEFT JOIN LATERAL (
            SELECT min(v.depart_date) AS next_departure, min(v.price_from) AS price_from
              FROM v_package_offers v WHERE v.package_id = s.package_id) o ON true
     WHERE CAST(:kind AS text) IS NULL OR s.kind = CAST(:kind AS text)
     ORDER BY s.score DESC
     LIMIT :limit
""")

TOOL_LIST_ACTIVE_PACKAGES = text("""
    SELECT id AS package_id, title, kind FROM packages
     WHERE status = 'active' ORDER BY kind, title LIMIT 15
""")

# ---- tool: get_package_details
TOOL_PACKAGE = text("""
    SELECT id, kind, title, season_label, description, status, duration_days, nights_makkah,
           nights_madinah, departure_city, airline, includes, excludes, requirements,
           booking_terms, agent_notes
      FROM packages WHERE id = :package_id AND status <> 'draft'
""")

TOOL_PACKAGE_HOTELS = text("""
    SELECT city, hotel_name, stars, distance_note, nights, meal_plan
      FROM package_hotels WHERE package_id = :package_id ORDER BY sort_order, city
""")

TOOL_PACKAGE_DEPARTURES = text("""
    SELECT id, depart_date, return_date, registration_deadline, status
      FROM package_departures
     WHERE package_id = :package_id AND depart_date >= current_date
       AND status IN ('open','few_left','full')
     ORDER BY depart_date LIMIT 6
""")

TOOL_PACKAGE_PRICES = text("""
    SELECT p.room_type, p.traveler_type, p.amount, p.currency, p.notes, p.updated_at,
           d.depart_date AS departure_date
      FROM package_prices p
      LEFT JOIN package_departures d ON d.id = p.departure_id
     WHERE p.package_id = :package_id
       AND (p.valid_until IS NULL OR p.valid_until >= current_date)
       AND (d.id IS NULL OR d.depart_date >= current_date)
     ORDER BY p.traveler_type, p.amount
""")

SET_HNSW_ITERATIVE_SCAN = text("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)")

# ---- tool: search_knowledge (hybrid: vector + FTS، دمج بـ Reciprocal Rank Fusion)
TOOL_KNOWLEDGE_HYBRID = text("""
    WITH v AS (
        SELECT id, row_number() OVER () AS r FROM (
            SELECT id FROM knowledge_chunks
             WHERE is_active AND embedding IS NOT NULL
             ORDER BY embedding <=> CAST(CAST(:qvec AS text) AS vector)
             LIMIT 8) x
    ), f AS (
        SELECT id, row_number() OVER (ORDER BY rank DESC) AS r FROM (
            SELECT id, ts_rank(content_tsv, app_or_tsquery(:question)) AS rank
              FROM knowledge_chunks
             WHERE is_active AND content_tsv @@ app_or_tsquery(:question)
             ORDER BY rank DESC LIMIT 8) y
    )
    SELECT k.title, k.content, k.source_type, sum(1.0 / (60 + x.r)) AS score
      FROM (SELECT id, r FROM v UNION ALL SELECT id, r FROM f) x
      JOIN knowledge_chunks k ON k.id = x.id
     GROUP BY k.id, k.title, k.content, k.source_type
     ORDER BY score DESC
     LIMIT :k
""")

TOOL_KNOWLEDGE_FTS = text("""
    SELECT title, content, source_type, ts_rank(content_tsv, app_or_tsquery(:question)) AS score
      FROM knowledge_chunks
     WHERE is_active AND content_tsv @@ app_or_tsquery(:question)
     ORDER BY score DESC
     LIMIT :k
""")

# ---- tool: create_lead
TOOL_PACKAGE_TITLE = text("""
    SELECT title FROM packages WHERE id = :package_id
""")

TOOL_DEPARTURE_BELONGS = text("""
    SELECT 1 FROM package_departures WHERE id = :departure_id AND package_id = :package_id
""")

# UPSERT: Lead مفتوح واحد لكل زبون/برنامج. (xmax = 0) => صف جديد
TOOL_UPSERT_LEAD = text("""
    INSERT INTO leads (tenant_id, contact_id, conversation_id, package_id, departure_id, full_name,
                       phone_e164, city, adults, children, infants, room_type_pref,
                       preferred_period, notes, source_channel, collected_by)
    VALUES (app_current_tenant_id(), :contact_id, :conversation_id, :package_id, :departure_id,
            :full_name, :phone_e164, :city, :adults, :children, :infants, :room_type_pref,
            :preferred_period, :notes, :source_channel, 'bot')
    ON CONFLICT (tenant_id, contact_id, package_id) WHERE status IN ('new','contacted','qualified')
    DO UPDATE SET full_name = EXCLUDED.full_name,
                  phone_e164 = EXCLUDED.phone_e164,
                  departure_id = coalesce(EXCLUDED.departure_id, leads.departure_id),
                  city = coalesce(EXCLUDED.city, leads.city),
                  adults = EXCLUDED.adults, children = EXCLUDED.children, infants = EXCLUDED.infants,
                  room_type_pref = coalesce(EXCLUDED.room_type_pref, leads.room_type_pref),
                  preferred_period = coalesce(EXCLUDED.preferred_period, leads.preferred_period),
                  notes = coalesce(EXCLUDED.notes, leads.notes),
                  conversation_id = EXCLUDED.conversation_id
    RETURNING id, (xmax = 0) AS inserted
""")

TOOL_INSERT_LEAD_EVENT = text("""
    INSERT INTO lead_events (tenant_id, lead_id, event_type, data, actor_type)
    VALUES (app_current_tenant_id(), :lead_id, :event_type, CAST(:data AS jsonb), :actor_type)
""")

TOOL_STAFF_TO_NOTIFY = text("""
    SELECT id, full_name, whatsapp_phone FROM staff_users
     WHERE is_active AND notify_on_new_lead AND whatsapp_phone IS NOT NULL
""")

# رقم واتساب الوكالة الذي يُرسل منه الإشعار (الحقيقي قبل رقم الاختبار)
TOOL_NOTIFY_SENDER_ACCOUNT = text("""
    SELECT id FROM channel_accounts
     WHERE channel = 'whatsapp' AND status = 'active'
     ORDER BY is_test, created_at LIMIT 1
""")

# ---- tool: handoff_to_human
TOOL_HANDOFF = text("""
    UPDATE conversations
       SET bot_paused_until = now() + make_interval(hours => :hours)
     WHERE id = :conversation_id
""")

# توكن ملغى/منتهي (Meta error 190) => القناة تحتاج إعادة ربط، وتظهر في لوحة الإدارة
MARK_CHANNEL_NEEDS_REAUTH = text("""
    UPDATE channel_accounts SET status = 'needs_reauth', last_error = :error, last_checked_at = now()
     WHERE id = :channel_account_id AND status = 'active'
""")

# ================================================================== billing (SYSTEM)
SUBSCRIPTION_LIFECYCLE_TICK = text("""
    SELECT tenant_id, old_status, new_status FROM subscription_lifecycle_tick()
""")

# ================================================================== echo / human takeover (TENANT)
# رسالة موجودة مسبقاً بنفس المعرّف (غالباً ردنا نحن بعد تسجيل الإرسال)
MESSAGE_EXISTS = text("""
    SELECT 1 FROM messages WHERE channel = :channel AND external_message_id = :external_message_id
""")

# إرسال من النظام (البوت أو رد موظف من اللوحة) لنفس الزبون خلال آخر دقيقة لم يُسجَّل معرّفه بعد
# => الـ echo غالباً لرسالتنا (لا نكررها في الـ Inbox ولا نعتبرها رداً من تطبيق الهاتف)
RECENT_BOT_SEND_TO = text("""
    SELECT 1 FROM outbound_messages
     WHERE channel_account_id = :channel_account_id AND recipient = :recipient
       AND purpose IN ('reply','staff_reply')
       AND status IN ('sending','sent') AND created_at > now() - interval '60 seconds'
       AND (external_message_id IS NULL OR external_message_id = :external_message_id)
     LIMIT 1
""")

INSERT_STAFF_ECHO_MESSAGE = text("""
    INSERT INTO messages (tenant_id, conversation_id, channel, direction, sender_type,
                          external_message_id, msg_type, text_content, payload, platform_ts)
    VALUES (app_current_tenant_id(), :conversation_id, :channel, 'outbound', 'staff',
            :external_message_id, :msg_type, :text_content, CAST(:payload AS jsonb), :platform_ts)
    ON CONFLICT (tenant_id, channel, external_message_id) DO NOTHING
    RETURNING id
""")

# الموظف رد من خارج النظام => البوت يتوقف في هذه المحادثة، ورسائل الزبون المعلّقة تُعتبر مُجابة
HUMAN_TAKEOVER_BY_ECHO = text("""
    WITH c AS (
        UPDATE conversations
           SET bot_paused_until = greatest(coalesce(bot_paused_until, now()),
                                           now() + make_interval(hours => :hours)),
               last_outbound_at = now(), reply_due_at = NULL
         WHERE id = :conversation_id
     RETURNING id
    )
    UPDATE messages SET handled_at = now()
     WHERE conversation_id = (SELECT id FROM c) AND direction = 'inbound' AND handled_at IS NULL
""")
