"""catalog: travel packages (Hajj/Umrah), aliases, hotels, departures, prices,
knowledge base (pgvector) + Arabic search (tsvector + pg_trgm)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# مرتبط بنموذج الـ Embeddings المختار (مثلاً 1024 لـ Voyage multilingual).
# تغييره لاحقاً = migration جديدة + إعادة توليد الـ embeddings.
EMBEDDING_DIM = 1024

ROOM_TYPES = "('quad','triple','double','single','shared','na')"


def upgrade() -> None:
    # ------------------------------------------------------------ packages
    op.execute("""
    CREATE TABLE packages (
        id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        code           text,                         -- رمز داخلي: UMR-1448-RAMADAN
        kind           text NOT NULL CHECK (kind IN ('umrah','hajj','tourism','visa_only','other')),
        title          text NOT NULL,                -- "عمرة العشر الأواخر - 12 يوم"
        season_label   text,                         -- "رمضان 1448"
        description    text,
        status         text NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft','active','full','archived')),
        duration_days  int  CHECK (duration_days > 0),
        nights_makkah  int  CHECK (nights_makkah >= 0),
        nights_madinah int  CHECK (nights_madinah >= 0),
        departure_city text,                         -- طرابلس / بنغازي / مصراتة
        airline        text,
        includes       jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(includes) = 'array'),
        excludes       jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(excludes) = 'array'),
        requirements   text,                         -- المستندات وشروط التسجيل (يكتبها المكتب)
        booking_terms  text,                         -- العربون، الإلغاء، الاسترجاع
        agent_notes    text,                         -- تعليمات داخلية للبوت عن هذا البرنامج
        -- أعمدة مولّدة للبحث (تتحدث تلقائياً مع كل تعديل)
        title_norm     text GENERATED ALWAYS AS (app_normalize_ar(title)) STORED,
        search_tsv     tsvector GENERATED ALWAYS AS (
            setweight(to_tsvector('simple', app_normalize_ar(coalesce(title, ''))), 'A') ||
            setweight(to_tsvector('simple', app_normalize_ar(
                coalesce(season_label, '') || ' ' || coalesce(departure_city, '') || ' ' ||
                coalesce(airline, ''))), 'B') ||
            setweight(to_tsvector('simple', app_normalize_ar(coalesce(description, ''))), 'C')
        ) STORED,
        created_at     timestamptz NOT NULL DEFAULT now(),
        updated_at     timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, id)
    );
    CREATE UNIQUE INDEX packages_tenant_code_uq ON packages (tenant_id, code) WHERE code IS NOT NULL;
    CREATE INDEX packages_tenant_status_idx ON packages (tenant_id, status, kind);
    -- البحث الكامل (FTS)
    CREATE INDEX packages_search_tsv_gin ON packages USING gin (search_tsv);
    -- البحث التقريبي للأخطاء الإملائية واللهجة ("عمره رمضن")
    CREATE INDEX packages_title_trgm_gin ON packages USING gin (title_norm gin_trgm_ops);
    """)
    op.execute("SELECT app_enable_tenant_rls('packages')")

    # ------------------------------------------------------------ package_aliases
    # كيف يسمّي الزبون الليبي البرنامج فعلياً: "عمرة شهر 12"، "العشر الأواخر"، "عمرة المولد"
    op.execute("""
    CREATE TABLE package_aliases (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        package_id  uuid NOT NULL,
        alias       text NOT NULL CHECK (length(btrim(alias)) > 1),
        alias_norm  text GENERATED ALWAYS AS (app_normalize_ar(alias)) STORED,
        created_at  timestamptz NOT NULL DEFAULT now(),
        UNIQUE (package_id, alias_norm),
        FOREIGN KEY (tenant_id, package_id) REFERENCES packages (tenant_id, id) ON DELETE CASCADE
    );
    CREATE INDEX package_aliases_trgm_gin ON package_aliases USING gin (alias_norm gin_trgm_ops);
    CREATE INDEX package_aliases_package_idx ON package_aliases (tenant_id, package_id);
    """)
    op.execute("SELECT app_enable_tenant_rls('package_aliases')")

    # ------------------------------------------------------------ package_hotels
    op.execute("""
    CREATE TABLE package_hotels (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        package_id    uuid NOT NULL,
        city          text NOT NULL CHECK (city IN ('makkah','madinah','jeddah','other')),
        hotel_name    text NOT NULL,
        stars         smallint CHECK (stars BETWEEN 1 AND 5),
        distance_note text,                          -- "300 متر من الحرم"
        nights        int CHECK (nights >= 0),
        meal_plan     text CHECK (meal_plan IN ('room_only','breakfast','half_board','full_board')),
        sort_order    smallint NOT NULL DEFAULT 0,
        FOREIGN KEY (tenant_id, package_id) REFERENCES packages (tenant_id, id) ON DELETE CASCADE
    );
    CREATE INDEX package_hotels_package_idx ON package_hotels (tenant_id, package_id, sort_order);
    """)
    op.execute("SELECT app_enable_tenant_rls('package_hotels')")

    # ------------------------------------------------------------ package_departures
    # البرنامج الواحد له عدة مواعيد انطلاق، ولكل موعد مقاعده وحالته
    op.execute("""
    CREATE TABLE package_departures (
        id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id             uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        package_id            uuid NOT NULL,
        depart_date           date NOT NULL,
        return_date           date NOT NULL,
        registration_deadline date,
        seats_total           int CHECK (seats_total >= 0),
        seats_left            int CHECK (seats_left >= 0),
        status                text NOT NULL DEFAULT 'open'
                              CHECK (status IN ('open','few_left','full','closed','cancelled')),
        flight_details        text,
        notes                 text,
        created_at            timestamptz NOT NULL DEFAULT now(),
        updated_at            timestamptz NOT NULL DEFAULT now(),
        CHECK (return_date >= depart_date),
        CHECK (registration_deadline IS NULL OR registration_deadline <= depart_date),
        CHECK (seats_left IS NULL OR seats_total IS NULL OR seats_left <= seats_total),
        UNIQUE (tenant_id, id),
        UNIQUE (tenant_id, package_id, id),          -- هدف للمفتاح المركّب في الأسعار والـ leads
        FOREIGN KEY (tenant_id, package_id) REFERENCES packages (tenant_id, id) ON DELETE CASCADE
    );
    CREATE INDEX package_departures_upcoming_idx
        ON package_departures (tenant_id, package_id, depart_date);
    """)
    op.execute("SELECT app_enable_tenant_rls('package_departures')")

    # ------------------------------------------------------------ package_prices
    # السعر = (برنامج [+ موعد اختياري]) × نوع الغرفة × فئة المسافر
    op.execute(f"""
    CREATE TABLE package_prices (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        package_id    uuid NOT NULL,
        departure_id  uuid,                          -- NULL = نفس السعر لكل المواعيد
        room_type     text NOT NULL CHECK (room_type IN {ROOM_TYPES}),
        traveler_type text NOT NULL DEFAULT 'adult' CHECK (traveler_type IN ('adult','child','infant')),
        amount        numeric(12,2) NOT NULL CHECK (amount >= 0),
        currency      char(3) NOT NULL DEFAULT 'LYD',
        notes         text,                          -- "الطفل أقل من 12 سنة بدون سرير"
        valid_until   date,
        created_at    timestamptz NOT NULL DEFAULT now(),
        updated_at    timestamptz NOT NULL DEFAULT now(),  -- البوت يتجنب تأكيد سعر قديم
        UNIQUE NULLS NOT DISTINCT (package_id, departure_id, room_type, traveler_type),
        FOREIGN KEY (tenant_id, package_id) REFERENCES packages (tenant_id, id) ON DELETE CASCADE,
        -- يضمن أن الموعد يتبع نفس البرنامج ونفس الوكالة
        FOREIGN KEY (tenant_id, package_id, departure_id)
            REFERENCES package_departures (tenant_id, package_id, id) ON DELETE CASCADE
    );
    CREATE INDEX package_prices_package_idx ON package_prices (tenant_id, package_id);
    """)
    op.execute("SELECT app_enable_tenant_rls('package_prices')")

    # ------------------------------------------------------------ knowledge_chunks (RAG)
    # للسياسات والأسئلة العامة فقط. الأسعار والمواعيد تأتي من الجداول أعلاه، لا من RAG.
    op.execute(f"""
    CREATE TABLE knowledge_chunks (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        source_type     text NOT NULL CHECK (source_type IN ('faq','policy','package','document','manual')),
        source_ref      text,
        package_id      uuid,
        title           text,
        content         text NOT NULL,
        content_tsv     tsvector GENERATED ALWAYS AS (
            to_tsvector('simple', app_normalize_ar(coalesce(title, '') || ' ' || content))
        ) STORED,
        embedding       vector({EMBEDDING_DIM}),
        embedding_model text,
        is_active       boolean NOT NULL DEFAULT true,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, package_id) REFERENCES packages (tenant_id, id) ON DELETE CASCADE
    );
    CREATE INDEX knowledge_chunks_tenant_idx ON knowledge_chunks (tenant_id, source_type) WHERE is_active;
    CREATE INDEX knowledge_chunks_tsv_gin ON knowledge_chunks USING gin (content_tsv);
    """)
    # ملاحظة: مع RLS يُطبّق فلتر الوكالة بعد البحث التقريبي في HNSW.
    # عند نمو البيانات فعّل في الجلسة: SET hnsw.iterative_scan = relaxed_order (pgvector >= 0.8)
    op.execute("""
    CREATE INDEX knowledge_chunks_embedding_hnsw
        ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
    """)
    op.execute("SELECT app_enable_tenant_rls('knowledge_chunks')")

    for t in ("packages", "package_departures", "package_prices", "knowledge_chunks"):
        op.execute(f"""
        CREATE TRIGGER {t}_set_updated_at BEFORE UPDATE ON {t}
            FOR EACH ROW EXECUTE FUNCTION app_set_updated_at();
        """)

    # ==================================================================
    # عرض العروض الحالية للـ Agent
    # security_invoker = true إلزامي: بدونه يعمل الـ View بصلاحيات مالكه ويتجاوز RLS!
    # ==================================================================
    op.execute("""
    CREATE VIEW v_package_offers WITH (security_invoker = true) AS
    SELECT
        p.id AS package_id, p.tenant_id, p.kind, p.title, p.season_label, p.status,
        d.id AS departure_id, d.depart_date, d.return_date, d.registration_deadline,
        d.seats_left, d.status AS departure_status,
        (SELECT min(pp.amount) FROM package_prices pp
          WHERE pp.package_id = p.id
            AND pp.traveler_type = 'adult'
            AND (pp.departure_id = d.id OR pp.departure_id IS NULL)
            AND (pp.valid_until IS NULL OR pp.valid_until >= current_date)) AS price_from,
        (SELECT max(pp.updated_at) FROM package_prices pp WHERE pp.package_id = p.id) AS prices_updated_at
    FROM packages p
    LEFT JOIN package_departures d
           ON d.package_id = p.id
          AND d.depart_date >= current_date
          AND d.status IN ('open','few_left')
    WHERE p.status = 'active';
    """)

    # ==================================================================
    # أداة البحث التي يستدعيها الـ Agent: search_packages(query)
    # - SECURITY INVOKER (الافتراضي) => RLS تُطبّق تلقائياً على الوكالة الحالية
    # - تجمع: تشابه الـ trigram مع العنوان والمرادفات + ترتيب FTS
    # - word_similarity في الاتجاهين: الاستعلام داخل العنوان، والمرادف داخل رسالة الزبون
    #   ("قداش عمرة رمضان للعيلة" تحتوي المرادف "عمره رمضان")
    # ==================================================================
    op.execute(r"""
    CREATE FUNCTION search_packages(p_query text, p_limit int DEFAULT 5, p_min_score real DEFAULT 0.3)
    RETURNS TABLE (package_id uuid, title text, kind text, score real, matched_alias text)
    LANGUAGE sql STABLE AS $$
        WITH input AS (
            SELECT app_normalize_ar(p_query) AS nq
        ), q AS (
            SELECT nq,
                   to_tsquery('simple', coalesce(nullif(array_to_string(ARRAY(
                       SELECT quote_literal(w) || ':*'
                       -- إزالة علامات الترقيم ورموز tsquery قبل بناء الاستعلام (OR بين الكلمات)
                       FROM regexp_split_to_table(
                           regexp_replace(nq, E'[''"\\\\&|!():*<>,.;؟?،]', ' ', 'g'),
                           E'\\s+') AS w
                       WHERE char_length(w) >= 2), ' | '), ''), 'a & !a')) AS tsq
            FROM input
        ), pkg AS (
            SELECT p.id, p.title, p.kind,
                   greatest(word_similarity(q.nq, p.title_norm),
                            word_similarity(p.title_norm, q.nq)) AS title_sim,
                   ts_rank(p.search_tsv, q.tsq) AS fts
            FROM packages p CROSS JOIN q
            WHERE p.status IN ('active','full')
        ), ali AS (
            SELECT DISTINCT ON (a.package_id)
                   a.package_id,
                   a.alias,
                   greatest(word_similarity(q.nq, a.alias_norm),
                            word_similarity(a.alias_norm, q.nq)) AS alias_sim
            FROM package_aliases a CROSS JOIN q
            ORDER BY a.package_id, 3 DESC
        ), scored AS (
            SELECT pkg.id, pkg.title, pkg.kind,
                   greatest(pkg.title_sim, coalesce(ali.alias_sim, 0)) AS sim,
                   pkg.fts,
                   CASE WHEN ali.alias_sim > pkg.title_sim THEN ali.alias END AS matched_alias
            FROM pkg LEFT JOIN ali ON ali.package_id = pkg.id
        )
        SELECT id, title, kind,
               (0.75 * sim + 0.25 * least(fts * 10, 1))::real AS score,
               matched_alias
        FROM scored
        WHERE sim >= p_min_score OR fts > 0
        ORDER BY score DESC, title
        LIMIT p_limit
    $$;
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS search_packages(text, int, real);
    DROP VIEW IF EXISTS v_package_offers;
    DROP TABLE IF EXISTS knowledge_chunks;
    DROP TABLE IF EXISTS package_prices;
    DROP TABLE IF EXISTS package_departures;
    DROP TABLE IF EXISTS package_hotels;
    DROP TABLE IF EXISTS package_aliases;
    DROP TABLE IF EXISTS packages;
    """)
