"""Local stand-in for graph.facebook.com + api.openai.com — للتطوير والتجربة فقط (لا يُستخدم في الإنتاج).

يسمح بتشغيل اللوحة كاملة محلياً (webhook -> worker -> Agent -> إرسال) بدون حساب Meta أو مفتاح OpenAI:

    python -m scripts.dev_mock_upstream 8099
    # ثم شغّل الـ API والـ worker مع:
    META_GRAPH_BASE_URL=http://127.0.0.1:8099 OPENAI_BASE_URL=http://127.0.0.1:8099/v1 OPENAI_API_KEY=sk-mock

- POST /{ver}/{phone_id}/messages  => إرسال واتساب ناجح (wamid.MOCK...)، أو 400/131026 إذا فُعّل فشل الإرسال
- GET  /{ver}/{id}                 => فحص صحة القناة ناجح
- POST /v1/chat/completions        => رد باللهجة الليبية؛ «احجز» في آخر رسالة زبون => استدعاء create_lead
- POST /v1/embeddings              => متجهات ثابتة بالطول المطلوب
- GET  /__mock/stats               => {"chat_calls": n, "sends": n}
- POST /__mock/fail-sends {"on": true|false}
"""
from __future__ import annotations

import json
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_lock = threading.Lock()
STATE: dict[str, Any] = {"chat_calls": 0, "sends": 0, "fail_sends": False}

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
    else:
        message["content"] = "هلا بيك! عندنا برامج عمرة وحج. شن البرنامج اللي يهمك؟"
    return {"id": f"chatcmpl-{uuid.uuid4().hex[:10]}", "object": "chat.completion", "created": 0,
            "model": body.get("model", "gpt-4o"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("mock: " + (fmt % args) + "\n")

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/__mock/stats":
            with _lock:
                return self._json(200, {k: v for k, v in STATE.items()})
        self._json(200, {"id": self.path.rsplit("/", 1)[-1].split("?")[0],
                         "display_phone_number": "+218 91 000 0000", "verified_name": "Mock"})

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/__mock/fail-sends":
            with _lock:
                STATE["fail_sends"] = bool(body.get("on"))
            return self._json(200, {"fail_sends": STATE["fail_sends"]})
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
