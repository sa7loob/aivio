# المرحلة 4: الـ Agent والأدوات وقاعدة المعرفة

> الحالة: مكتملة (كود) — 2026-09-26

## المعمارية

- `app/llm/base.py`: عقود محايدة للمزوّد (ChatMessage, ToolCall, LLMClient, EmbeddingClient). تغيير المزوّد = adapter واحد.
- `app/llm/openai_client.py`: OpenAI SDK (chat + embeddings بـ `dimensions=1024` لمطابقة `vector(1024)`).
- `app/agent/core.py`: حلقة LLM ↔ أدوات (حد 5 دورات، أدوات متوازية، رد احتياطي + إيقاف البوت 12 ساعة عند الفشل).
- `app/agent/tools.py`: `search_packages`، `get_package_details`، `search_knowledge` (hybrid: pgvector + FTS مع RRF)، `create_lead` (UPSERT + إشعار المبيعات كـ outbox في نفس الـ transaction)، `handoff_to_human`.
- `app/agent/prompts.py`: برومبت باللهجة الليبية. قواعد: لا أسعار ولا تواريخ إلا من الأدوات، لا خصومات، لا جوازات في المحادثة.
- `app/worker/reply.py`: ثلاث مراحل: tx1 لقطة، ثم الـ Agent بدون transaction، ثم tx2 حفظ. مهلة الـ Agent = 75% من الـ lease.

## قرارات

- بدون LangChain: الحلقة ~100 سطر، وتحكم كامل في RLS والأخطاء والاختبار.
- إشعار المبيعات = صف في `outbound_messages` (outbox) وليس background task في الذاكرة، فلا يضيع عند تعطل الـ worker.
- Migration 0006: جدول `agent_runs` (توكنز، زمن، أدوات، أخطاء)، ودالة `app_ar_light_stem` (تجريد "ال/وال/بال/لل")، و`app_or_tsquery`.
- قالب `new_lead` (Utility، 4 متغيرات) يجب اعتماده من Meta. تكلفة القوالب على حساب واتساب الوكالة.

## التقييم

- `evals/cases.yaml`: 15 حالة ليبية (سعر، مواعيد، مستندات، استرجاع، حجز متعدد الأدوار، عائلة بأطفال، طلب موظف، تفاوض، prompt injection، إنجليزي).
- `python -m evals.run [--judge] [--model ...]`: فحوص حتمية (أدوات، وسائط، نص الرد، صف الـ lead والإشعار) + LLM judge اختياري (لهجة، فائدة، grounding).

## التحقق (بيئة العمل)

- 58 اختبار unit نجحت (حلقة الـ Agent بـ LLM مُبرمج، الأدوات بقاعدة وهمية، adapter OpenAI، البرومبت، التقطيع، فحوص التقييم).
- كل SQL الأدوات نُفّذ بدور `app_user` على Postgres مع RLS. استثناء: استعلام pgvector الهجين لم يُشغَّل هنا لعدم توفر pgvector في بيئة العمل.
- لم يُشغَّل هنا: استدعاء OpenAI الفعلي و`evals.run`، لأن الوصول للـ API والحزم محجوب في بيئة العمل.
