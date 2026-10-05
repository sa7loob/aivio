#!/usr/bin/env python3
"""يبني workflows قالب «محل حلويات» (workflows/*.json) من المصادر في src/.

المصدر هو src/ (منطق Code nodes بـ JavaScript، والـ prompt، والـ schema). لا تعدّل workflows/*.json يدوياً.
إذا عدّلت في محرر n8n، انقل التعديل إلى src/ ثم أعد البناء:  python3 build.py
المعرّفات ثابتة (workflows و credentials)، لأن كل عميل في نسخة n8n مستقلة؛ هذا يجعل إعادة النشر تحديثاً لا نسخة جديدة.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
OUT = HERE / "workflows"
NS = uuid.UUID("6f1c1d0e-5a59-4c55-9d2c-0b6f2a1e7a11")

WF_MAIN, WF_ERRORS, WF_CLEANUP = "botWhatsAppFlow1", "botErrorsFlow001", "botCleanupFlow01"
PG = {"postgres": {"id": "botPostgres00001", "name": "DB"}}
WA = {"httpHeaderAuth": {"id": "botWhatsApp00001", "name": "WhatsApp"}}
OAI = {"httpHeaderAuth": {"id": "botOpenAI0000001", "name": "OpenAI"}}

S = "$('Settings').first().json"           # إعدادات العميل من قاعدة بياناته


def src(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def node(name, type_, version, pos, params, **extra) -> dict:
    return {"id": str(uuid.uuid5(NS, name)), "name": name, "type": f"n8n-nodes-base.{type_}",
            "typeVersion": version, "position": list(pos), "parameters": params, **extra}


def code(name, pos, file, each=False, **extra):
    js = src(file)
    if "__SYSTEM_PROMPT__" in js:
        js = js.replace("__SYSTEM_PROMPT__", json.dumps(src("system_prompt.txt"), ensure_ascii=False))
    if "__CONTEXT_PROMPT__" in js:
        js = js.replace("__CONTEXT_PROMPT__", json.dumps(src("context_prompt.txt"), ensure_ascii=False))
    if "__RESPONSE_SCHEMA__" in js:
        schema = json.loads(src("response_schema.json"))
        js = js.replace("__RESPONSE_SCHEMA__", json.dumps(schema, ensure_ascii=False))
    mode = "runOnceForEachItem" if each else "runOnceForAllItems"
    return node(name, "code", 2, pos, {"mode": mode, "language": "javaScript", "jsCode": js}, **extra)


def sql(name, pos, query, params=None, **extra):
    options = {"queryReplacement": "={{ [" + ", ".join(params) + "] }}"} if params else {}
    return node(name, "postgres", 2.6, pos,
                {"operation": "executeQuery", "query": query.strip(), "options": options},
                credentials=PG, **extra)


def http(name, pos, url, *, cred, method="GET", json_body=None, multipart=None, file_response=False,
         timeout=30000, **extra):
    params = {"method": method, "url": url, "authentication": "genericCredentialType",
              "genericAuthType": "httpHeaderAuth", "options": {"timeout": timeout}}
    if json_body is not None:
        params.update({"sendBody": True, "specifyBody": "json", "jsonBody": json_body})
    if multipart is not None:
        params.update({"sendBody": True, "contentType": "multipart-form-data",
                       "bodyParameters": {"parameters": multipart}})
    if file_response:
        params["options"]["response"] = {"response": {"responseFormat": "file", "outputPropertyName": "data"}}
    return node(name, "httpRequest", 4.2, pos, params, credentials=cred, **extra)


def if_true(name, pos, expression):
    cond = {"id": str(uuid.uuid5(NS, name + ":cond")), "leftValue": "={{ " + expression + " }}",
            "rightValue": "", "operator": {"type": "boolean", "operation": "true", "singleValue": True}}
    return node(name, "if", 2.2, pos, {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict",
                                   "version": 2},
                       "conditions": [cond], "combinator": "and"},
        "options": {}})


def wait(name, pos, seconds_expr):
    return node(name, "wait", 1.1, pos, {"amount": seconds_expr, "unit": "seconds"},
                webhookId=str(uuid.uuid5(NS, name + ":webhook")))


def note(name, pos, text, width=420, height=200, color=7):
    return node(name, "stickyNote", 1, pos, {"content": text, "width": width, "height": height, "color": color})


def connect(pairs):
    """pairs: (from, to) أو (from, output_index, to)."""
    conns: dict = {}
    for p in pairs:
        src_name, out, dst = (p[0], 0, p[1]) if len(p) == 2 else p
        outs = conns.setdefault(src_name, {"main": []})["main"]
        while len(outs) <= out:
            outs.append([])
        outs[out].append({"node": dst, "type": "main", "index": 0})
    return conns


RETRY = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 2000}
ON_ERROR_OUTPUT = {"onError": "continueErrorOutput"}
INBOUND_ROW = "id AS message_id, contact_id, (SELECT wa_id FROM contacts c WHERE c.id = messages.contact_id) AS wa_id"
WA_SEND_URL = "={{ " + S + ".graph_base_url }}/{{ " + S + ".phone_number_id }}/messages"


def main_workflow() -> dict:
    y0, y1, y2 = 0, 360, 720
    nodes = [
        note("ملاحظة: التحقق", (-40, -560), "## 1. ربط Meta\nGET /webhook/whatsapp يعيد hub.challenge إذا طابق "
             "Verify Token المحفوظ في bot_settings.", 640, 160, 4),
        node("Webhook GET", "webhook", 2.1, (0, -380), {
            "httpMethod": "GET", "path": "whatsapp", "responseMode": "responseNode", "options": {}},
            webhookId=str(uuid.uuid5(NS, "webhook-get"))),
        sql("Verify token", (220, -380), "SELECT meta_verify_token FROM bot_settings WHERE id = 1"),
        code("Check verify token", (440, -380), "check_verify.js"),
        node("Respond", "respondToWebhook", 1.5, (660, -380), {
            "respondWith": "text", "responseBody": "={{ $json.challenge }}",
            "options": {"responseCode": "={{ $json.ok ? 200 : 403 }}"}}),

        note("ملاحظة: الاستقبال", (-40, -180), "## 2. استقبال الرسالة\nرد 200 فوري لـ Meta، ثم: التحقق من التوقيع، "
             "وتجاهل المكرر (wa_message_id)، والصوت يُنزَّل ويُفرَّغ. فشل التفريغ لا يوقف الرد.", 1960, 160, 5),
        node("Webhook POST", "webhook", 2.1, (0, y0), {
            "httpMethod": "POST", "path": "whatsapp", "responseMode": "onReceived",
            "options": {"rawBody": True}}, webhookId=str(uuid.uuid5(NS, "webhook-post"))),
        code("Read body", (220, y0), "read_body.js"),
        sql("Settings", (440, y0), """
SELECT s.*,
       (SELECT string_agg(p.name, '، ' ORDER BY p.sort_order, p.id) FROM products p WHERE p.available) AS product_names
FROM bot_settings s WHERE s.id = 1"""),
        code("Verify & extract", (660, y0), "verify_extract.js"),
        sql("Save inbound", (880, y0), """
WITH c AS (
    INSERT INTO contacts (wa_id, name) VALUES ($1, $2)
    ON CONFLICT (wa_id) DO UPDATE
        SET name = COALESCE(EXCLUDED.name, contacts.name), last_inbound_at = now()
    RETURNING id
)
INSERT INTO messages (contact_id, direction, wa_message_id, type, wa_type, text)
SELECT c.id, 'in', $3, $4, $5, $6 FROM c
ON CONFLICT (wa_message_id) DO NOTHING
RETURNING id AS message_id, contact_id, type, CAST($7 AS text) AS media_id, CAST($1 AS text) AS wa_id""",
            ["$json.wa_id", "$json.name ?? null", "$json.wa_message_id", "$json.type", "$json.wa_type ?? null",
             "$json.text ?? null", "$json.media_id ?? null"]),
        if_true("New message?", (1100, y0), "Boolean($json.message_id)"),
        if_true("Voice?", (1320, y0), "$json.type === 'audio' && Boolean($json.media_id)"),
        http("Get media URL", (1540, y0 - 140), "={{ " + S + ".graph_base_url }}/{{ $json.media_id }}",
             cred=WA, timeout=20000, **RETRY, **ON_ERROR_OUTPUT),
        http("Download audio", (1760, y0 - 140), "={{ $json.url }}", cred=WA, file_response=True,
             timeout=30000, **RETRY, **ON_ERROR_OUTPUT),
        code("Name audio file", (1980, y0 - 140), "name_audio.js", each=True),
        http("Transcribe", (2200, y0 - 140), "={{ " + S + ".openai_base_url }}/audio/transcriptions", cred=OAI,
             method="POST", timeout=60000, multipart=[
                 {"parameterType": "formBinaryData", "name": "file", "inputDataFieldName": "data"},
                 {"parameterType": "formData", "name": "model", "value": "={{ " + S + ".transcription_model }}"},
                 {"parameterType": "formData", "name": "language", "value": "ar"},
                 {"parameterType": "formData", "name": "response_format", "value": "json"},
                 {"parameterType": "formData", "name": "prompt",
                  "value": "={{ ('رسالة صوتية من زبون ليبي لمحل حلويات. كلمات قد ترد: ' + (" + S
                           + ".product_names || '') + '، كيلو، نص كيلو، صينية، تورتة، علبة، توصيل، استلام، "
                           "قداش، نبي، باهي، توا، طرابلس، بنغازي، مصراتة').slice(0, 800) }}"},
             ], **RETRY, **ON_ERROR_OUTPUT),
        sql("Save transcript", (2420, y0 - 140), f"""
UPDATE messages SET text = CAST($1 AS text), media_failed = (CAST($1 AS text) IS NULL)
WHERE id = CAST($2 AS bigint)
RETURNING {INBOUND_ROW}""",
            ["(String($json.text ?? '').trim().slice(0, 4000)) || null",
             "$('Save inbound').item.json.message_id"]),
        sql("Mark audio failed", (2420, y0 + 60), f"""
UPDATE messages SET media_failed = true WHERE id = CAST($1 AS bigint)
RETURNING {INBOUND_ROW}""", ["$('Save inbound').item.json.message_id"]),

        note("ملاحظة: الدور", (-40, y1 - 180), "## 3. ننتظر الزبون يكمل كلامه\nبعد debounce_seconds يرد البوت فقط "
             "إذا كانت هذه آخر رسالة من الزبون، وبعد انتهاء تفريغ أي رسالة صوتية (حتى audio_max_wait_seconds).",
             1300, 160, 6),
        wait("Debounce", (0, y1), "={{ " + S + ".debounce_seconds }}"),
        sql("Check turn", (220, y1), """
SELECT CAST($1 AS bigint) AS contact_id,
       CAST($2 AS bigint) AS message_id,
       CAST($3 AS text) AS wa_id,
       (SELECT max(id) FROM messages WHERE contact_id = CAST($1 AS bigint) AND direction = 'in')
           = CAST($2 AS bigint) AS is_latest,
       EXISTS (SELECT 1 FROM messages
               WHERE contact_id = CAST($1 AS bigint) AND direction = 'in' AND type = 'audio'
                 AND text IS NULL AND NOT media_failed
                 AND created_at > now() - make_interval(secs => CAST($4 AS int))) AS audio_pending,
       (SELECT count(*) FROM messages
        WHERE contact_id = CAST($1 AS bigint) AND direction = 'out'
          AND created_at > now() - make_interval(hours => 1)) < CAST($5 AS int) AS under_limit""",
            ["$json.contact_id", "$json.message_id", "$json.wa_id", S + ".audio_max_wait_seconds",
             S + ".max_replies_per_hour"]),
        if_true("Latest message?", (440, y1), "$json.is_latest === true"),
        node("Newer message will answer", "noOp", 1, (660, y1 + 180), {}),
        if_true("Audio still transcribing?", (660, y1), "$json.audio_pending === true"),
        wait("Poll", (880, y1 - 160), "2"),
        # حماية من الإزعاج والتكلفة: زبون يرسل بلا توقف لا يستهلك أكثر من max_replies_per_hour رداً في الساعة
        if_true("Under hourly limit?", (880, y1), "$json.under_limit === true"),
        node("Hourly limit reached", "noOp", 1, (1100, y1 + 180), {}),

        note("ملاحظة: الرد", (1040, y1 - 180), "## 4. الرد\nطلب «المنيو» = القائمة من القاعدة بدون OpenAI. غير ذلك: "
             "القائمة (ثابتة، تُخزَّن عند OpenAI) + السياق والطلب الجاري + آخر الرسائل → OpenAI (JSON) → رد للزبون. "
             "فشل OpenAI = اعتذار قصير + تنبيه صاحب المحل.", 1640, 160, 4),
        # آخر history_messages رسالة خلال history_hours فقط (محادثة قديمة لا تُرسل)، والطلب الجاري يحفظ ما قبلها
        sql("Load context", (1100, y1), """
SELECT c.id AS contact_id, c.wa_id, c.name AS contact_name,
       CASE WHEN c.current_order_at > now() - make_interval(hours => CAST($2 AS int))
            THEN c.current_order END AS current_order,
       COALESCE((SELECT json_agg(p ORDER BY p.sort_order, p.id) FROM (
                    SELECT id, category, name, unit, price_lyd, min_qty, description, sort_order
                    FROM products WHERE available) p), CAST('[]' AS json)) AS products,
       COALESCE((SELECT json_agg(h ORDER BY h.id) FROM (
                    SELECT id, direction, type, wa_type, text, media_failed
                    FROM messages
                    WHERE contact_id = c.id AND created_at > now() - make_interval(hours => CAST($2 AS int))
                    ORDER BY id DESC LIMIT CAST($3 AS int)) h), CAST('[]' AS json)) AS history
FROM contacts c WHERE c.id = CAST($1 AS bigint)""",
            ["$json.contact_id", S + ".history_hours", S + ".history_messages"]),
        code("Build request", (1320, y1), "build_request.js", each=True),
        if_true("Menu request?", (1540, y1), "$json.menu_request === true"),
        code("Menu messages", (1760, y1 + 200), "menu_messages.js"),
        http("Send menu", (1980, y1 + 200), WA_SEND_URL, cred=WA, method="POST", timeout=20000,
             json_body="={{ JSON.stringify($json.body) }}", **RETRY),
        sql("Save menu reply", (2200, y1 + 200), """
INSERT INTO messages (contact_id, direction, wa_message_id, type, text)
SELECT CAST($1 AS bigint), 'out', $2, 'text', $3 WHERE CAST($4 AS int) = 1""", [
            "$('Menu messages').item.json.contact_id", "$json.messages?.[0]?.id ?? null",
            "$('Menu messages').item.json.marker", "$('Menu messages').item.json.part"]),
        http("OpenAI", (1760, y1), "={{ " + S + ".openai_base_url }}/chat/completions", cred=OAI, method="POST",
             json_body="={{ JSON.stringify($json.request) }}", timeout=90000,
             retryOnFail=True, maxTries=3, waitBetweenTries=3000, **ON_ERROR_OUTPUT),
        code("Parse reply", (1980, y1), "parse_reply.js", each=True),
        http("Send reply", (2200, y1), WA_SEND_URL, cred=WA, method="POST", timeout=20000, json_body=(
            "={{ JSON.stringify({ messaging_product: 'whatsapp', recipient_type: 'individual', to: $json.wa_id, "
            "type: 'text', text: { preview_url: false, body: $json.reply } }) }}"), **RETRY),
        # الرد + استهلاك التوكنز، وآخر صورة للطلب الجاري (تبقى كما هي إذا تعطّل OpenAI)
        sql("Save reply", (2420, y1), """
WITH m AS (
    INSERT INTO messages (contact_id, direction, wa_message_id, type, text,
                          ai_model, prompt_tokens, cached_tokens, completion_tokens)
    VALUES (CAST($1 AS bigint), 'out', $2, 'text', $3, $4, CAST($5 AS int), CAST($6 AS int), CAST($7 AS int))
    RETURNING id
), c AS (
    UPDATE contacts
    SET current_order = CASE WHEN CAST($9 AS boolean) THEN CAST($8 AS jsonb) ELSE current_order END,
        current_order_at = CASE WHEN CAST($9 AS boolean) THEN now() ELSE current_order_at END
    WHERE id = CAST($1 AS bigint)
)
SELECT id FROM m""", [
            "$('Parse reply').item.json.contact_id", "$json.messages?.[0]?.id ?? null",
            "$('Parse reply').item.json.reply",
            "$('Parse reply').item.json.usage.model ?? null",
            "$('Parse reply').item.json.usage.prompt_tokens ?? null",
            "$('Parse reply').item.json.usage.cached_tokens ?? null",
            "$('Parse reply').item.json.usage.completion_tokens ?? null",
            "$('Parse reply').item.json.order_state ?? null",
            "$('Parse reply').item.json.ai_ok === true"]),

        note("ملاحظة: التنبيه", (-40, y2 - 180), "## 5. تنبيه صاحب المحل\nطلب مكتمل جديد (لا يُكرَّر بفضل fingerprint) "
             "أو زبون يحتاج رداً بشرياً → واتساب لهاتف المالك (قالب معتمد، أو نص داخل نافذة 24 ساعة).",
             1540, 160, 3),
        if_true("Order complete?", (0, y2), "$('Parse reply').item.json.order_complete === true"),
        sql("Save order", (220, y2), """
INSERT INTO orders (contact_id, customer_name, phone, items, fulfillment, address, needed_at, notes,
                    estimated_total_lyd, fingerprint)
VALUES (CAST($1 AS bigint), $2, $3, CAST($4 AS jsonb), $5, $6, $7, $8, CAST($9 AS numeric), $10)
ON CONFLICT (contact_id, fingerprint) DO NOTHING
RETURNING id AS order_id""", [
            "$('Parse reply').item.json.contact_id",
            "$('Parse reply').item.json.order.customer_name ?? null",
            "$('Parse reply').item.json.order.phone ?? null",
            "JSON.stringify($('Parse reply').item.json.order.items)",
            "$('Parse reply').item.json.order.fulfillment ?? null",
            "$('Parse reply').item.json.order.address ?? null",
            "$('Parse reply').item.json.order.needed_at ?? null",
            "$('Parse reply').item.json.order.notes ?? null",
            "$('Parse reply').item.json.order.estimated_total_lyd ?? null",
            "$('Parse reply').item.json.fingerprint"]),
        if_true("New order?", (440, y2), "Boolean($json.order_id)"),
        if_true("Handoff?", (440, y2 + 200), "$('Parse reply').item.json.handoff_needed === true"),
        code("Owner alert", (660, y2), "owner_alert.js", each=True),
        http("Notify owner", (880, y2), WA_SEND_URL, cred=WA, method="POST", timeout=20000,
             json_body="={{ JSON.stringify($json.body) }}", **RETRY),
        sql("Mark notified", (1100, y2), "UPDATE orders SET notified_at = now() WHERE id = CAST($1 AS bigint)",
            ["$('Owner alert').item.json.order_id ?? null"]),
    ]
    connections = connect([
        ("Webhook GET", "Verify token"), ("Verify token", "Check verify token"), ("Check verify token", "Respond"),
        ("Webhook POST", "Read body"), ("Read body", "Settings"), ("Settings", "Verify & extract"),
        ("Verify & extract", "Save inbound"), ("Save inbound", "New message?"), ("New message?", "Voice?"),
        ("Voice?", "Get media URL"), ("Voice?", 1, "Debounce"),
        ("Get media URL", "Download audio"), ("Get media URL", 1, "Mark audio failed"),
        ("Download audio", "Name audio file"), ("Download audio", 1, "Mark audio failed"),
        ("Name audio file", "Transcribe"),
        ("Transcribe", "Save transcript"), ("Transcribe", 1, "Mark audio failed"),
        ("Save transcript", "Debounce"), ("Mark audio failed", "Debounce"),
        ("Debounce", "Check turn"), ("Check turn", "Latest message?"),
        ("Latest message?", "Audio still transcribing?"), ("Latest message?", 1, "Newer message will answer"),
        ("Audio still transcribing?", "Poll"), ("Audio still transcribing?", 1, "Under hourly limit?"),
        ("Poll", "Check turn"),
        ("Under hourly limit?", "Load context"), ("Under hourly limit?", 1, "Hourly limit reached"),
        ("Load context", "Build request"), ("Build request", "Menu request?"),
        ("Menu request?", "Menu messages"), ("Menu request?", 1, "OpenAI"),
        ("Menu messages", "Send menu"), ("Send menu", "Save menu reply"),
        ("OpenAI", "Parse reply"), ("OpenAI", 1, "Parse reply"),
        ("Parse reply", "Send reply"), ("Send reply", "Save reply"),
        ("Save reply", "Order complete?"), ("Save reply", "Handoff?"),
        ("Order complete?", "Save order"), ("Save order", "New order?"), ("New order?", "Owner alert"),
        ("Handoff?", "Owner alert"),
        ("Owner alert", "Notify owner"), ("Notify owner", "Mark notified"),
    ])
    return {"id": WF_MAIN, "name": "[WA] رسائل واتساب", "nodes": nodes, "connections": connections,
            "settings": {"executionOrder": "v1", "errorWorkflow": WF_ERRORS, "timezone": "Africa/Tripoli"},
            "active": False, "pinData": {}}


def errors_workflow() -> dict:
    nodes = [
        node("Error Trigger", "errorTrigger", 1, (0, 0), {}),
        sql("Log error", (220, 0), """
INSERT INTO workflow_errors (workflow, node, message, execution_id) VALUES ($1, $2, $3, $4)""", [
            "$json.workflow?.name ?? null",
            "$json.execution?.lastNodeExecuted ?? null",
            "String($json.execution?.error?.message ?? $json.trigger?.error?.message ?? 'unknown').slice(0, 2000)",
            "$json.execution?.id != null ? String($json.execution.id) : null"]),
    ]
    return {"id": WF_ERRORS, "name": "[System] الأخطاء", "nodes": nodes,
            "connections": connect([("Error Trigger", "Log error")]),
            "settings": {"executionOrder": "v1"}, "active": False, "pinData": {}}


def cleanup_workflow() -> dict:
    nodes = [
        node("Every night", "scheduleTrigger", 1.2, (0, 0),
             {"rule": {"interval": [{"field": "days", "daysInterval": 1, "triggerAtHour": 3}]}}),
        # للتشغيل اليدوي من سطر الأوامر (n8n execute) أو من workflow آخر
        node("Run manually", "executeWorkflowTrigger", 1.1, (0, 200), {"inputSource": "passthrough"}),
        sql("Delete old data", (220, 0), """
WITH s AS (SELECT history_days FROM bot_settings WHERE id = 1),
     m AS (DELETE FROM messages
           WHERE created_at < now() - make_interval(days => COALESCE((SELECT history_days FROM s), 90))
           RETURNING 1),
     e AS (DELETE FROM workflow_errors WHERE created_at < now() - make_interval(days => 30) RETURNING 1)
SELECT (SELECT count(*) FROM m) AS messages_deleted, (SELECT count(*) FROM e) AS errors_deleted"""),
    ]
    return {"id": WF_CLEANUP, "name": "[System] تنظيف يومي", "nodes": nodes,
            "connections": connect([("Every night", "Delete old data"), ("Run manually", "Delete old data")]),
            "settings": {"executionOrder": "v1", "errorWorkflow": WF_ERRORS, "timezone": "Africa/Tripoli"},
            "active": False, "pinData": {}}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for file, wf in (("whatsapp.json", main_workflow()), ("errors.json", errors_workflow()),
                     ("cleanup.json", cleanup_workflow())):
        names = [n["name"] for n in wf["nodes"]]
        assert len(names) == len(set(names)), f"duplicate node names in {file}"
        for src_name, outs in wf["connections"].items():
            assert src_name in names, src_name
            for out in outs["main"]:
                for c in out:
                    assert c["node"] in names, c["node"]
        (OUT / file).write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{file}: {len(wf['nodes'])} nodes")


if __name__ == "__main__":
    main()
