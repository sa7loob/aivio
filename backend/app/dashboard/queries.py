"""SQL for the office dashboard (Live Inbox + Leads CRM). All run inside tenant_session (RLS)."""
from sqlalchemy import text

_CONVERSATION_COLUMNS = """
    c.id, ca.channel, ca.display_name AS channel_name, c.mode, c.bot_paused_until,
    c.assigned_user_id, au.full_name AS assigned_name, c.takeover_by, c.takeover_at,
    c.unread_count, c.last_message_at, c.last_message_preview, c.last_message_direction,
    c.last_inbound_at, ct.id AS contact_id, ct.display_name AS contact_name, ct.phone_e164 AS contact_phone,
    EXISTS (SELECT 1 FROM leads l WHERE l.conversation_id = c.id
             AND l.status IN ('new','contacted','qualified')) AS has_open_lead
"""
_CONVERSATION_FROM = """
      FROM conversations c
      JOIN contacts ct ON ct.tenant_id = c.tenant_id AND ct.id = c.contact_id
      JOIN channel_accounts ca ON ca.tenant_id = c.tenant_id AND ca.id = c.channel_account_id
      LEFT JOIN users au ON au.id = c.assigned_user_id
"""

# ------------------------------------------------------------------ inbox
INBOX_LIST = text(f"""
    SELECT {_CONVERSATION_COLUMNS} {_CONVERSATION_FROM}
     WHERE c.last_message_at IS NOT NULL
       AND CASE CAST(:view AS text)
             WHEN 'open'   THEN c.mode <> 'closed'
             WHEN 'human'  THEN c.mode = 'human'
             WHEN 'bot'    THEN c.mode = 'bot'
             WHEN 'unread' THEN c.unread_count > 0 AND c.mode <> 'closed'
             WHEN 'closed' THEN c.mode = 'closed'
             ELSE true END
       AND (CAST(:channel AS text) IS NULL OR ca.channel = CAST(:channel AS text))
       AND CASE CAST(:assigned AS text)
             WHEN 'me'         THEN c.assigned_user_id = app_current_user_id()
             WHEN 'unassigned' THEN c.assigned_user_id IS NULL
             ELSE true END
       AND (CAST(:q AS text) IS NULL
            OR ct.display_name ILIKE '%' || CAST(:q AS text) || '%'
            OR ct.phone_e164 LIKE '%' || CAST(:q AS text) || '%'
            OR ct.external_user_id LIKE '%' || CAST(:q AS text) || '%')
       AND (CAST(:cursor_ts AS timestamptz) IS NULL
            OR (c.last_message_at, c.id) < (CAST(:cursor_ts AS timestamptz), CAST(:cursor_id AS uuid)))
     ORDER BY c.last_message_at DESC, c.id DESC
     LIMIT :limit
""")

CONVERSATION_DETAIL = text(f"""
    SELECT {_CONVERSATION_COLUMNS}, ct.channel AS contact_channel {_CONVERSATION_FROM}
     WHERE c.id = :id
""")

MESSAGES_PAGE = text("""
    SELECT m.id, m.direction, m.sender_type, m.msg_type, m.text_content, m.created_at, m.client_msg_id,
           o.status AS delivery_status, o.last_error AS delivery_error
      FROM messages m
      LEFT JOIN LATERAL (
            SELECT ob.status, ob.last_error FROM outbound_messages ob
             WHERE ob.tenant_id = m.tenant_id AND ob.message_id = m.id
             ORDER BY ob.created_at DESC LIMIT 1) o ON true
     WHERE m.conversation_id = :conversation_id
       AND (CAST(:cursor_ts AS timestamptz) IS NULL
            OR (m.created_at, m.id) < (CAST(:cursor_ts AS timestamptz), CAST(:cursor_id AS uuid)))
     ORDER BY m.created_at DESC, m.id DESC
     LIMIT :limit
""")

LOCK_FOR_STAFF = text("""
    SELECT c.id, c.mode, c.channel_account_id, c.last_inbound_at, c.bot_paused_until,
           ct.external_user_id AS recipient, ca.channel, ca.status AS channel_status
      FROM conversations c
      JOIN contacts ct ON ct.tenant_id = c.tenant_id AND ct.id = c.contact_id
      JOIN channel_accounts ca ON ca.tenant_id = c.tenant_id AND ca.id = c.channel_account_id
     WHERE c.id = :id
       FOR UPDATE OF c
""")

MARK_READ = text("UPDATE conversations SET unread_count = 0 WHERE id = :id AND unread_count > 0")

# الموظف يستلم المحادثة: البوت يتوقف فوراً، وردوده المعلّقة (لم تبدأ إرسالها) تُلغى
TAKEOVER = text("""
    UPDATE conversations
       SET mode = 'human', takeover_by = app_current_user_id(), takeover_at = now(),
           reply_due_at = NULL, bot_paused_until = NULL,
           assigned_user_id = coalesce(assigned_user_id, app_current_user_id())
     WHERE id = :id
""")

CANCEL_PENDING_BOT_REPLIES = text("""
    UPDATE outbound_messages SET status = 'cancelled', last_error = 'cancelled: human takeover'
     WHERE conversation_id = :id AND purpose = 'reply' AND status = 'pending'
""")

# إرجاع المحادثة للبوت: إذا بقيت رسائل زبون بدون رد، يرد عليها البوت الآن
RELEASE = text("""
    UPDATE conversations c
       SET mode = 'bot', takeover_by = NULL, takeover_at = NULL, bot_paused_until = NULL,
           reply_due_at = CASE WHEN EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id
                                             AND m.direction = 'inbound' AND m.handled_at IS NULL)
                               THEN now() ELSE NULL END
     WHERE c.id = :id
""")

CLOSE = text("""
    UPDATE conversations SET mode = 'closed', reply_due_at = NULL, unread_count = 0 WHERE id = :id
""")

ASSIGN = text("""
    UPDATE conversations c SET assigned_user_id = CAST(:user_id AS uuid)
     WHERE c.id = :id
       AND (CAST(:user_id AS uuid) IS NULL
            OR EXISTS (SELECT 1 FROM memberships m WHERE m.tenant_id = c.tenant_id
                        AND m.user_id = CAST(:user_id AS uuid) AND m.status = 'active'))
    RETURNING c.id
""")

MESSAGE_BY_CLIENT_ID = text("""
    SELECT id, created_at FROM messages WHERE client_msg_id = :client_msg_id
""")

INSERT_STAFF_MESSAGE = text("""
    INSERT INTO messages (tenant_id, conversation_id, channel, direction, sender_type, msg_type,
                          text_content, payload, client_msg_id)
    VALUES (app_current_tenant_id(), :conversation_id, :channel, 'outbound', 'staff', 'text',
            :text_content, jsonb_build_object('user_id', app_current_user_id()), :client_msg_id)
    RETURNING id, created_at
""")

MARK_CONVERSATION_ANSWERED = text("""
    WITH c AS (UPDATE conversations SET last_outbound_at = now() WHERE id = :id RETURNING id)
    UPDATE messages SET handled_at = now()
     WHERE conversation_id = (SELECT id FROM c) AND direction = 'inbound' AND handled_at IS NULL
""")

RETRY_STAFF_MESSAGE = text("""
    UPDATE outbound_messages
       SET status = 'pending', next_attempt_at = now(), attempts = 0, last_error = NULL, locked_until = NULL
     WHERE message_id = :message_id AND purpose = 'staff_reply' AND status = 'failed'
    RETURNING id
""")

# ------------------------------------------------------------------ leads CRM
_LEAD_COLUMNS = """
    l.id, l.status, l.full_name, l.phone_e164, l.city, l.adults, l.children, l.infants,
    l.room_type_pref, l.preferred_period, l.notes, l.lost_reason, l.source_channel, l.collected_by,
    l.package_id, p.title AS package_title, l.departure_id, d.depart_date,
    l.assigned_to, s.full_name AS assigned_name, l.conversation_id,
    l.created_at, l.updated_at, l.notified_at, l.first_contact_at
"""
_LEAD_FROM = """
      FROM leads l
      LEFT JOIN packages p ON p.tenant_id = l.tenant_id AND p.id = l.package_id
      LEFT JOIN package_departures d ON d.tenant_id = l.tenant_id AND d.id = l.departure_id
      LEFT JOIN staff_users s ON s.tenant_id = l.tenant_id AND s.id = l.assigned_to
"""
_LEAD_FILTERS = """
     WHERE (CAST(:status AS text) IS NULL OR l.status = CAST(:status AS text))
       -- نمط + معرّف منفصلان: CAST('any' AS uuid) داخل CASE قد يُقيَّم مبكراً ويفشل
       AND (CAST(:assigned_mode AS text) = 'any'
            OR (CAST(:assigned_mode AS text) = 'unassigned' AND l.assigned_to IS NULL)
            OR (CAST(:assigned_mode AS text) = 'staff' AND l.assigned_to = CAST(:assigned_staff AS uuid)))
       AND (CAST(:package_id AS uuid) IS NULL OR l.package_id = CAST(:package_id AS uuid))
       AND (CAST(:q AS text) IS NULL OR l.full_name ILIKE '%' || CAST(:q AS text) || '%'
            OR l.phone_e164 LIKE '%' || CAST(:q AS text) || '%')
       AND (CAST(:from_date AS date) IS NULL OR l.created_at >= CAST(:from_date AS date))
       AND (CAST(:to_date AS date) IS NULL OR l.created_at < CAST(:to_date AS date) + 1)
"""

LEADS_LIST = text(f"""
    SELECT {_LEAD_COLUMNS} {_LEAD_FROM} {_LEAD_FILTERS}
       AND (CAST(:cursor_ts AS timestamptz) IS NULL
            OR (l.created_at, l.id) < (CAST(:cursor_ts AS timestamptz), CAST(:cursor_id AS uuid)))
     ORDER BY l.created_at DESC, l.id DESC
     LIMIT :limit
""")

LEADS_EXPORT = text(f"""
    SELECT {_LEAD_COLUMNS} {_LEAD_FROM} {_LEAD_FILTERS}
     ORDER BY l.created_at DESC, l.id DESC
     LIMIT 10000
""")

LEAD_DETAIL = text(f"SELECT {_LEAD_COLUMNS} {_LEAD_FROM} WHERE l.id = :id")

LEAD_FOR_UPDATE = text("""
    SELECT id, status, assigned_to, notes, lost_reason, first_contact_at FROM leads WHERE id = :id FOR UPDATE
""")

UPDATE_LEAD = text("""
    UPDATE leads
       SET status = :status, lost_reason = :lost_reason, assigned_to = CAST(:assigned_to AS uuid),
           notes = :notes,
           first_contact_at = CASE WHEN first_contact_at IS NULL AND :status <> 'new' THEN now()
                                   ELSE first_contact_at END
     WHERE id = :id
""")

LEAD_EVENTS = text("""
    SELECT e.id, e.event_type, e.data, e.actor_type, s.full_name AS actor_name, e.created_at
      FROM lead_events e LEFT JOIN staff_users s ON s.tenant_id = e.tenant_id AND s.id = e.actor_staff_id
     WHERE e.lead_id = :id ORDER BY e.created_at, e.id
""")

INSERT_LEAD_EVENT = text("""
    INSERT INTO lead_events (tenant_id, lead_id, event_type, data, actor_type, actor_staff_id)
    VALUES (app_current_tenant_id(), :lead_id, :event_type, CAST(:data AS jsonb), 'staff',
            (SELECT id FROM staff_users WHERE user_id = app_current_user_id() LIMIT 1))
""")

# RLS => موظف من وكالة أخرى غير مرئي = غير موجود
ACTIVE_STAFF_EXISTS = text("SELECT 1 FROM staff_users WHERE id = :id AND is_active")

STAFF_LIST = text("""
    SELECT id, full_name, role, user_id, whatsapp_phone, notify_on_new_lead, is_active
      FROM staff_users ORDER BY is_active DESC, full_name
""")
