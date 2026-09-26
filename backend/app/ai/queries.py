"""SQL for phase 7a: ai_jobs queue, voice transcription, catalog imports, staff answers as knowledge.

كل الاستعلامات داخل tenant_session (RLS) عدا CLAIM_AI_JOBS (دالة SECURITY DEFINER تعيد معرّفات فقط).
"""
from sqlalchemy import text

# ------------------------------------------------------------------ ai_jobs (queue)
CLAIM_AI_JOBS = text("""
    SELECT job_id, tenant_id, kind
      FROM claim_ai_jobs(CAST(:kinds AS text[]), :batch, make_interval(secs => :lease_seconds))
""")

LOAD_AI_JOB = text("""
    SELECT id, kind, attempts, message_id, catalog_import_id, knowledge_chunk_id, created_at
      FROM ai_jobs WHERE id = :job_id AND status = 'processing'
""")

FINISH_AI_JOB = text("""
    UPDATE ai_jobs
       SET status = :status, locked_until = NULL, finished_at = now(), last_error = :error,
           model = :model, input_tokens = :input_tokens, output_tokens = :output_tokens,
           duration_ms = :duration_ms
     WHERE id = :job_id
""")

RETRY_AI_JOB = text("""
    UPDATE ai_jobs
       SET status = 'pending', locked_until = NULL, last_error = :error,
           next_attempt_at = now() + make_interval(secs => :delay_seconds)
     WHERE id = :job_id
""")

ENQUEUE_TRANSCRIPTION = text("""
    INSERT INTO ai_jobs (tenant_id, kind, message_id)
    VALUES (app_current_tenant_id(), 'transcribe', :message_id)
    ON CONFLICT DO NOTHING
""")

ENQUEUE_CATALOG_EXTRACTION = text("""
    INSERT INTO ai_jobs (tenant_id, kind, catalog_import_id)
    VALUES (app_current_tenant_id(), 'catalog_extract', :catalog_import_id)
    ON CONFLICT DO NOTHING
""")

ENQUEUE_EMBEDDING = text("""
    INSERT INTO ai_jobs (tenant_id, kind, knowledge_chunk_id)
    VALUES (app_current_tenant_id(), 'embed_knowledge', :knowledge_chunk_id)
    ON CONFLICT DO NOTHING
""")

# ------------------------------------------------------------------ voice transcription
TRANSCRIPTION_SOURCE = text("""
    SELECT m.id, m.conversation_id, m.channel, m.msg_type, m.text_content, m.payload, m.created_at,
           ca.access_token_enc
      FROM messages m
      JOIN conversations c ON c.tenant_id = m.tenant_id AND c.id = m.conversation_id
      JOIN channel_accounts ca ON ca.tenant_id = c.tenant_id AND ca.id = c.channel_account_id
     WHERE m.id = :message_id
""")

# كلمات الوكالة لتحسين التفريغ: أسماء البرامج أولاً ثم المرادفات والفنادق ومدن الانطلاق
TENANT_VOCABULARY = text("""
    SELECT term FROM (
        SELECT p.title AS term, 1 AS pri, p.updated_at AS ts FROM packages p WHERE p.status <> 'archived'
        UNION ALL
        SELECT a.alias, 2, a.created_at FROM package_aliases a
          JOIN packages p ON p.tenant_id = a.tenant_id AND p.id = a.package_id
         WHERE p.status <> 'archived'
        UNION ALL
        SELECT h.hotel_name, 3, NULL FROM package_hotels h
        UNION ALL
        SELECT p.departure_city, 4, NULL FROM packages p WHERE p.departure_city IS NOT NULL
    ) t
    ORDER BY pri, ts DESC NULLS LAST
    LIMIT 80
""")

# النص يُكتب مرة واحدة فقط (إعادة تنفيذ المهمة لا تغيّره)
SET_MESSAGE_TRANSCRIPT = text("""
    UPDATE messages SET text_content = :text
     WHERE id = :message_id AND text_content IS NULL
    RETURNING conversation_id, created_at
""")

# معاينة القائمة تتحدث فقط إن كانت الرسالة الصوتية هي آخر رسالة في المحادثة
PREVIEW_TRANSCRIPT = text("""
    UPDATE conversations SET last_message_preview = left(:preview, 140)
     WHERE id = :conversation_id AND last_message_at = CAST(:created_at AS timestamptz)
""")

# رسائل صوتية معلّقة لم يُكتب نصها بعد => رد البوت ينتظر (حتى حد أقصى)
PENDING_TRANSCRIPTIONS = text("""
    SELECT j.message_id, j.created_at
      FROM ai_jobs j
      JOIN messages m ON m.tenant_id = j.tenant_id AND m.id = j.message_id
     WHERE j.kind = 'transcribe' AND j.status IN ('pending','processing')
       AND j.message_id = ANY (CAST(:ids AS uuid[]))
       AND m.text_content IS NULL
""")

DEFER_REPLY = text("""
    UPDATE conversations
       SET reply_due_at = now() + make_interval(secs => :seconds), reply_lease_until = NULL
     WHERE id = :conversation_id AND reply_due_at = CAST(:claimed_due AS timestamptz)
""")

# ------------------------------------------------------------------ catalog imports
FIND_IMPORT_BY_SHA = text("""
    SELECT id, status, created_at FROM catalog_imports
     WHERE sha256 = :sha256 AND status <> 'failed'
     ORDER BY created_at DESC LIMIT 1
""")

INSERT_CATALOG_IMPORT = text("""
    INSERT INTO catalog_imports (tenant_id, created_by, filename, mime_type, size_bytes, sha256, file_data)
    VALUES (app_current_tenant_id(), app_current_user_id(), :filename, :mime_type, :size_bytes, :sha256,
            :file_data)
    RETURNING id, status, created_at
""")

_IMPORT_COLUMNS = """
    i.id, i.filename, i.mime_type, i.size_bytes, i.status, i.error, i.warnings, i.packages_created,
    i.created_at, i.finished_at, u.full_name AS created_by_name
"""

LIST_IMPORTS = text(f"""
    SELECT {_IMPORT_COLUMNS} FROM catalog_imports i LEFT JOIN users u ON u.id = i.created_by
     ORDER BY i.created_at DESC, i.id DESC LIMIT 50
""")

IMPORT_DETAIL = text(f"""
    SELECT {_IMPORT_COLUMNS} FROM catalog_imports i LEFT JOIN users u ON u.id = i.created_by
     WHERE i.id = :id
""")

IMPORT_FOR_JOB = text("SELECT id, status, mime_type, file_data FROM catalog_imports WHERE id = :id")

MARK_IMPORT_PROCESSING = text("""
    UPDATE catalog_imports SET status = 'processing' WHERE id = :id AND status = 'pending'
""")

FINISH_IMPORT = text("""
    UPDATE catalog_imports
       SET status = :status, error = :error, warnings = CAST(:warnings AS jsonb),
           packages_created = :packages_created, file_data = NULL, finished_at = now()
     WHERE id = :id
""")

INSERT_DRAFT_PACKAGE = text("""
    INSERT INTO packages (tenant_id, kind, title, season_label, description, status, duration_days,
                          nights_makkah, nights_madinah, departure_city, airline, includes, excludes,
                          requirements, booking_terms, source_import_id)
    VALUES (app_current_tenant_id(), :kind, :title, :season_label, :description, 'draft', :duration_days,
            :nights_makkah, :nights_madinah, :departure_city, :airline, CAST(:includes AS jsonb),
            CAST(:excludes AS jsonb), :requirements, :booking_terms, :source_import_id)
    RETURNING id
""")

INSERT_PACKAGE_ALIAS = text("""
    INSERT INTO package_aliases (tenant_id, package_id, alias)
    VALUES (app_current_tenant_id(), :package_id, :alias)
    ON CONFLICT DO NOTHING
""")

INSERT_PACKAGE_HOTEL = text("""
    INSERT INTO package_hotels (tenant_id, package_id, city, hotel_name, stars, distance_note, nights, sort_order)
    VALUES (app_current_tenant_id(), :package_id, :city, :hotel_name, :stars, :distance_note, :nights, :sort_order)
""")

# seats_left يبقى فارغاً: رقم البروشور هو العدد الكلي وليس المتبقي (لا ندرة غير صادقة)
INSERT_PACKAGE_DEPARTURE = text("""
    INSERT INTO package_departures (tenant_id, package_id, depart_date, return_date, seats_total, notes)
    VALUES (app_current_tenant_id(), :package_id, CAST(:depart_date AS date), CAST(:return_date AS date),
            :seats_total, :notes)
    RETURNING id
""")

# العملة ثابتة LYD (الدفع ليبي 100%)؛ التطبيع يرفض أي عملة أخرى قبل الوصول هنا
INSERT_PACKAGE_PRICE = text("""
    INSERT INTO package_prices (tenant_id, package_id, departure_id, room_type, traveler_type, amount,
                                currency, notes)
    VALUES (app_current_tenant_id(), :package_id, CAST(:departure_id AS uuid), :room_type, :traveler_type,
            CAST(:amount AS numeric), 'LYD', :notes)
""")

# ------------------------------------------------------------------ catalog review / publish
LIST_PACKAGES = text("""
    SELECT p.id, p.kind, p.title, p.season_label, p.status, p.duration_days, p.departure_city,
           p.source_import_id, p.created_at, p.updated_at,
           (SELECT count(*) FROM package_departures d WHERE d.package_id = p.id) AS departures,
           (SELECT count(*) FROM package_prices pp WHERE pp.package_id = p.id) AS prices,
           (SELECT min(pp.amount) FROM package_prices pp
             WHERE pp.package_id = p.id AND pp.traveler_type = 'adult') AS price_from
      FROM packages p
     WHERE (CAST(:status AS text) IS NULL OR p.status = CAST(:status AS text))
       AND (CAST(:import_id AS uuid) IS NULL OR p.source_import_id = CAST(:import_id AS uuid))
     ORDER BY (p.status = 'draft') DESC, p.updated_at DESC, p.id
     LIMIT 200
""")

PACKAGE_DETAIL = text("""
    SELECT id, kind, title, season_label, description, status, duration_days, nights_makkah, nights_madinah,
           departure_city, airline, includes, excludes, requirements, booking_terms, agent_notes,
           source_import_id, created_at, updated_at
      FROM packages WHERE id = :id
""")

PACKAGE_ALIASES = text("SELECT id, alias FROM package_aliases WHERE package_id = :id ORDER BY created_at, id")

PACKAGE_HOTELS = text("""
    SELECT id, city, hotel_name, stars, distance_note, nights, meal_plan
      FROM package_hotels WHERE package_id = :id ORDER BY sort_order, id
""")

PACKAGE_DEPARTURES = text("""
    SELECT id, depart_date, return_date, registration_deadline, seats_total, seats_left, status, notes
      FROM package_departures WHERE package_id = :id ORDER BY depart_date, id
""")

PACKAGE_PRICES = text("""
    SELECT id, departure_id, room_type, traveler_type, amount, currency, notes, valid_until, updated_at
      FROM package_prices WHERE package_id = :id
     ORDER BY departure_id NULLS FIRST, traveler_type, amount, id
""")

PACKAGE_FOR_PUBLISH = text("""
    SELECT p.id, p.status,
           (SELECT count(*) FROM package_prices pp
             WHERE pp.package_id = p.id AND pp.traveler_type = 'adult') AS adult_prices
      FROM packages p WHERE p.id = :id
       FOR UPDATE
""")

PUBLISH_PACKAGE = text("UPDATE packages SET status = 'active' WHERE id = :id AND status = 'draft' RETURNING id")

# الاعتماد تأكيد بشري للأسعار => تُحسب «حديثة» من الآن (تنبيه السعر القديم يعتمد على updated_at)
CONFIRM_PACKAGE_PRICES = text("UPDATE package_prices SET updated_at = now() WHERE package_id = :id")

DELETE_DRAFT_PACKAGE = text("DELETE FROM packages WHERE id = :id AND status = 'draft' RETURNING id")

# التصحيح قبل الاعتماد فقط (التحرير الكامل للبرامج المعتمدة في Agent Studio)
PRICE_FOR_UPDATE = text("""
    SELECT pp.id, pp.package_id, p.status AS package_status
      FROM package_prices pp
      JOIN packages p ON p.tenant_id = pp.tenant_id AND p.id = pp.package_id
     WHERE pp.id = :id
       FOR UPDATE OF pp
""")

UPDATE_PRICE = text("""
    UPDATE package_prices
       SET amount = coalesce(CAST(:amount AS numeric), amount),
           notes = CASE WHEN CAST(:set_notes AS boolean) THEN CAST(:notes AS text) ELSE notes END
     WHERE id = :id
    RETURNING id, package_id, departure_id, room_type, traveler_type, amount, currency, notes, updated_at
""")

DELETE_PRICE = text("DELETE FROM package_prices WHERE id = :id")

DEPARTURE_FOR_UPDATE = text("""
    SELECT d.id, d.package_id, p.status AS package_status
      FROM package_departures d
      JOIN packages p ON p.tenant_id = d.tenant_id AND p.id = d.package_id
     WHERE d.id = :id
       FOR UPDATE OF d
""")

DELETE_DEPARTURE = text("DELETE FROM package_departures WHERE id = :id")

# ------------------------------------------------------------------ staff answers as knowledge
CONVERSATION_EXISTS = text("SELECT 1 FROM conversations WHERE id = :id")

CONVERSATION_RECENT_MESSAGES = text("""
    SELECT id, direction, sender_type, msg_type, text_content, created_at
      FROM messages WHERE conversation_id = :conversation_id
     ORDER BY created_at DESC, id DESC
     LIMIT 60
""")

KNOWLEDGE_BY_SOURCE_REF = text("""
    SELECT id, created_at FROM knowledge_chunks WHERE source_ref = :source_ref AND is_active LIMIT 1
""")

INSERT_STAFF_KNOWLEDGE = text("""
    INSERT INTO knowledge_chunks (tenant_id, source_type, source_ref, title, content, created_by)
    VALUES (app_current_tenant_id(), 'faq', :source_ref, :title, :content, app_current_user_id())
    RETURNING id, created_at
""")

LIST_KNOWLEDGE = text("""
    SELECT k.id, k.source_type, k.source_ref, k.title, k.content, k.is_active,
           (k.embedding IS NOT NULL) AS embedded, k.created_at, u.full_name AS created_by_name
      FROM knowledge_chunks k LEFT JOIN users u ON u.id = k.created_by
     WHERE (NOT CAST(:only_staff AS boolean) OR k.source_ref LIKE 'conversation:%')
       AND (CAST(:include_inactive AS boolean) OR k.is_active)
     ORDER BY k.created_at DESC, k.id DESC
     LIMIT 200
""")

DEACTIVATE_KNOWLEDGE = text("""
    UPDATE knowledge_chunks SET is_active = false WHERE id = :id AND is_active RETURNING id
""")

KNOWLEDGE_FOR_EMBEDDING = text("SELECT id, title, content, is_active FROM knowledge_chunks WHERE id = :id")

SET_KNOWLEDGE_EMBEDDING = text("""
    UPDATE knowledge_chunks
       SET embedding = CAST(CAST(:embedding AS text) AS vector), embedding_model = :model
     WHERE id = :id
""")
