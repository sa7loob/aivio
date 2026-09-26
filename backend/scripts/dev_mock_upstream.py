"""Local stand-in for graph.facebook.com + api.openai.com — للتطوير والتجربة فقط (لا يُستخدم في الإنتاج).

يسمح بتشغيل اللوحة كاملة محلياً (webhook -> worker -> Agent -> إرسال) بدون حساب Meta أو مفتاح OpenAI:

    python -m scripts.dev_mock_upstream 8099
    # ثم شغّل الـ API والـ worker مع:
    META_GRAPH_BASE_URL=http://127.0.0.1:8099 OPENAI_BASE_URL=http://127.0.0.1:8099/v1 OPENAI_API_KEY=sk-mock

Meta Graph:
- POST /{ver}/{phone_id}/messages  => إرسال واتساب ناجح (wamid.MOCK...)، أو 400/131026 إذا فُعّل فشل الإرسال
- GET  /{ver}/MOCKMEDIA...         => رابط وسائط صوتية (المعرّف الذي فيه "expired" => 404 كوسائط منتهية)
- GET  /__media/{id}               => ملف صوت (OggS) للتنزيل
- GET  /{ver}/{id}                 => فحص صحة القناة ناجح
OpenAI:
- POST /v1/chat/completions        => رد باللهجة الليبية؛ «احجز» في آخر رسالة زبون => استدعاء create_lead
                                      مع response_format (استخراج بروشور) => كتالوج ثابت فيه حالات للتحذيرات
- POST /v1/audio/transcriptions    => النص المضبوط عبر /__mock/transcript
- POST /v1/embeddings              => متجهات ثابتة بالطول المطلوب
تحكم:
- GET  /__mock/stats               => عدادات + آخر prompt/لغة/اسم ملف للتفريغ
- POST /__mock/fail-sends {"on": true|false}
- POST /__mock/transcript {"text": "...", "delay_seconds": 0}   (التأخير لتجربة انتظار البوت للتفريغ)
"""
from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from datetime import date, timedelta
from email.parser import BytesParser
from email.policy import default as default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_lock = threading.Lock()
STATE: dict[str, Any] = {
    "chat_calls": 0, "sends": 0, "fail_sends": False, "extractions": 0, "transcriptions": 0,
    "media_downloads": 0, "transcript_text": "السلام عليكم، قداش عمرة رمضان للعيلة؟ نبو نسافروا من طرابلس",
    "last_transcription": None, "transcript_delay_seconds": 0.0,
}
FAKE_OGG = b"OggS" + b"\x00" * 4092          # يكفي لاجتياز فحص الصيغة؛ المحتوى لا يُفرَّغ فعلاً

LEAD_ARGS = {"full_name": "سالم الورفلي", "adults": 2, "children": 1, "room_type_pref": "triple",
             "preferred_period": "عمرة رمضان", "city": "مصراتة", "notes": "يبي فندق قريب من الحرم"}


def chat_reply(body: dict[str, Any]) -> dict[str, Any]:
    msgs = body.get("messages", [])
    last = msgs[-1] if msgs else {}
    user_text = next((m.get("content") or "" for m in reversed(msgs) if m.get("role") == "user"), "")
    tools = {t["function"]["name"] for t in body.get("tools", [])}
    message: dict[str, Any] = {"role": "assistant", "content": None}
    finish = "stop"
    if last.get("role") == "tool":
        message["content"] = "تمام، سجلنا طلبك وحيتواصل معاك موظف من المكتب قريب إن شاء الله."
    elif "احجز" in user_text and "create_lead" in tools:
        finish = "tool_calls"
        message["tool_calls"] = [{"id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                                  "function": {"name": "create_lead",
                                               "arguments": json.dumps(LEAD_ARGS, ensure_ascii=False)}}]
    elif "رسالة صوتية" in user_text:
        message["content"] = "وصلتني رسالتك الصوتية 👍 عمرة رمضان تبدأ من 4500 دينار للفرد في الرباعي."
    else:
        message["content"] = "هلا بيك! عندنا برامج عمرة وحج. شن البرنامج اللي يهمك؟"
    return _completion(body, message, finish)


def catalog_extraction() -> dict[str, Any]:
    """كتالوج ثابت يغطي حالات التطبيع: سعر بالدولار (يُحذف)، عملة غير مكتوبة (تحذير)،
    تاريخ عودة محسوب من المدة، موعد بدون تاريخ ميلادي (يُحذف)."""
    d1 = date.today() + timedelta(days=60)
    d2 = date.today() + timedelta(days=90)
    return {"packages": [
        {"title": "عمرة رمضان - 15 يوم", "kind": "umrah", "season_label": "رمضان",
         "description": "برنامج عمرة رمضان مع إقامة في مكة والمدينة", "duration_days": 15,
         "nights_makkah": 10, "nights_madinah": 4, "departure_city": "طرابلس", "airline": "الخطوط الليبية",
         "includes": ["تذكرة الطيران", "التأشيرة", "الفندق مع الإفطار"], "excludes": ["المصاريف الشخصية"],
         "requirements": "جواز ساري 6 أشهر وصورتين", "booking_terms": "عربون 1000 دينار، والباقي قبل السفر بأسبوعين",
         "aliases": ["عمرة شهر رمضان"],
         "hotels": [{"city": "makkah", "hotel_name": "فندق دار الإيمان", "stars": 4,
                     "distance_note": "300 متر من الحرم", "nights": 10},
                    {"city": "madinah", "hotel_name": "فندق المختارة", "stars": 4, "distance_note": None,
                     "nights": 4}],
         "departures": [{"ref": "d1", "depart_date": d1.isoformat(),
                         "return_date": (d1 + timedelta(days=14)).isoformat(), "seats_total": 45, "notes": None},
                        {"ref": "d2", "depart_date": d2.isoformat(), "return_date": None, "seats_total": None,
                         "notes": None}],
         "prices": [{"room_type": "quad", "traveler_type": "adult", "amount": 4500, "currency": "د.ل",
                     "departure_ref": None, "notes": None},
                    {"room_type": "triple", "traveler_type": "adult", "amount": 5200, "currency": "د.ل",
                     "departure_ref": None, "notes": None},
                    {"room_type": "double", "traveler_type": "adult", "amount": 6100, "currency": "دينار",
                     "departure_ref": None, "notes": None},
                    {"room_type": "quad", "traveler_type": "child", "amount": 3500, "currency": "د.ل",
                     "departure_ref": None, "notes": "أقل من 12 سنة"},
                    {"room_type": "single", "traveler_type": "adult", "amount": 1500, "currency": "USD",
                     "departure_ref": None, "notes": None}]},
        {"title": "عمرة المولد النبوي", "kind": "umrah", "season_label": "ربيع الأول", "description": None,
         "duration_days": 10, "nights_makkah": None, "nights_madinah": None, "departure_city": "بنغازي",
         "airline": None, "includes": [], "excludes": [], "requirements": None, "booking_terms": None,
         "aliases": [], "hotels": [],
         "departures": [{"ref": "d1", "depart_date": None, "return_date": None, "seats_total": None,
                         "notes": "12 ربيع الأول"}],
         "prices": [{"room_type": "quad", "traveler_type": "adult", "amount": 3900, "currency": None,
                     "departure_ref": None, "notes": None}]},
    ], "notes": "الأسعار شاملة الطيران من طرابلس وبنغازي"}


def _completion(body: dict[str, Any], message: dict[str, Any], finish: str) -> dict[str, Any]:
    return {"id": f"chatcmpl-{uuid.uuid4().hex[:10]}", "object": "chat.completion", "created": 0,
            "model": body.get("model", "gpt-4o"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}


def _multipart_fields(content_type: str, raw: bytes) -> dict[str, Any]:
    msg = BytesParser(policy=default_policy).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + raw)
    fields: dict[str, Any] = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        fields[name] = {"filename": filename, "size": len(payload)} if filename else payload.decode("utf-8")
    return fields


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, raw: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, code: int, body: dict[str, Any]) -> None:
        self._send(code, json.dumps(body, ensure_ascii=False).encode(), "application/json")

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("mock: " + (fmt % args) + "\n")

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/__mock/stats":
            with _lock:
                return self._json(200, dict(STATE))
        if path.startswith("/__media/"):
            with _lock:
                STATE["media_downloads"] += 1
            return self._send(200, FAKE_OGG, "audio/ogg")
        last = path.rsplit("/", 1)[-1]
        if last.startswith("MOCKMEDIA"):
            if "expired" in last:
                return self._json(404, {"error": {"message": "media not found (mock)", "code": 100}})
            host = self.headers.get("Host", "127.0.0.1:8099")
            return self._json(200, {"id": last, "url": f"http://{host}/__media/{last}",
                                    "mime_type": "audio/ogg; codecs=opus", "file_size": len(FAKE_OGG)})
        self._json(200, {"id": last, "display_phone_number": "+218 91 000 0000", "verified_name": "Mock"})

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if self.path.endswith("/audio/transcriptions"):
            fields = _multipart_fields(self.headers.get("Content-Type", ""), raw)
            with _lock:
                STATE["transcriptions"] += 1
                STATE["last_transcription"] = {"model": fields.get("model"), "language": fields.get("language"),
                                               "prompt": fields.get("prompt"), "file": fields.get("file")}
                text = STATE["transcript_text"]
                delay = STATE["transcript_delay_seconds"]
            time.sleep(delay)
            return self._json(200, {"text": text, "usage": {"type": "tokens", "input_tokens": 50,
                                                             "output_tokens": 12, "total_tokens": 62}})
        body = json.loads(raw or b"{}")
        if self.path == "/__mock/fail-sends":
            with _lock:
                STATE["fail_sends"] = bool(body.get("on"))
            return self._json(200, {"fail_sends": STATE["fail_sends"]})
        if self.path == "/__mock/transcript":
            with _lock:
                if "text" in body:
                    STATE["transcript_text"] = str(body.get("text") or "")
                STATE["transcript_delay_seconds"] = float(body.get("delay_seconds") or 0)
                out = {"transcript_text": STATE["transcript_text"],
                       "delay_seconds": STATE["transcript_delay_seconds"]}
            return self._json(200, out)
        if self.path.endswith("/messages"):
            with _lock:
                STATE["sends"] += 1
                fail = STATE["fail_sends"]
            if fail:
                return self._json(400, {"error": {"code": 131026, "message": "Message undeliverable (mock)"}})
            return self._json(200, {"messaging_product": "whatsapp", "contacts": [{"wa_id": body.get("to")}],
                                    "messages": [{"id": f"wamid.MOCK{uuid.uuid4().hex}"}]})
        if self.path.endswith("/embeddings"):
            dims = int(body.get("dimensions") or 1024)
            inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
            return self._json(200, {"object": "list", "model": body.get("model"),
                                    "data": [{"object": "embedding", "index": i, "embedding": [0.01] * dims}
                                             for i in range(len(inputs))],
                                    "usage": {"prompt_tokens": 1, "total_tokens": 1}})
        if self.path.endswith("/chat/completions"):
            if (body.get("response_format") or {}).get("type") == "json_schema":
                with _lock:
                    STATE["extractions"] += 1
                message = {"role": "assistant",
                           "content": json.dumps(catalog_extraction(), ensure_ascii=False), "refusal": None}
                return self._json(200, _completion(body, message, "stop"))
            with _lock:
                STATE["chat_calls"] += 1
            return self._json(200, chat_reply(body))
        self._json(404, {"error": {"message": "not mocked", "code": 100}})


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print(f"dev mock (Meta Graph + OpenAI) on http://127.0.0.1:{port}", file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
