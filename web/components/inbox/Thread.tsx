"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { IconBack, IconSend } from "@/components/icons";
import { Alert, Avatar, Badge, ErrorState, LoadingState, Spinner } from "@/components/ui";
import { ApiError, api, errorMessage, qs } from "@/lib/api";
import { useDebounced, useLiveEvents } from "@/lib/events";
import { CHANNEL_LABEL, clockTime, dayLabel, fullTime, initials, localPhone, sameDay } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ConversationDetail, Message, Page, Staff } from "@/lib/types";

const PAGE = 50;

interface LocalMessage extends Message {
  local?: boolean;
}

function newUuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  // متصفحات قديمة / http بدون crypto.randomUUID
  return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) =>
    (Number(c) ^ (Math.random() * 16) >> (Number(c) / 4)).toString(16),
  );
}

function mergeMessages(current: LocalMessage[], fresh: Message[]): LocalMessage[] {
  const byId = new Map<string, LocalMessage>();
  for (const m of current) if (!m.local) byId.set(m.id, m);
  for (const m of fresh) byId.set(m.id, m);
  const serverClientIds = new Set(Array.from(byId.values()).map((m) => m.client_msg_id).filter(Boolean));
  const locals = current.filter((m) => m.local && !serverClientIds.has(m.client_msg_id));
  return [...Array.from(byId.values()), ...locals].sort((a, b) =>
    a.created_at === b.created_at ? a.id.localeCompare(b.id) : a.created_at.localeCompare(b.created_at),
  );
}

function senderLabel(m: Message): string {
  if (m.direction === "inbound") return "";
  if (m.sender_type === "bot") return "البوت";
  if (m.sender_type === "staff") return "موظف";
  return "";
}

function DeliveryMark({ m, onRetry }: { m: LocalMessage; onRetry: (m: LocalMessage) => void }) {
  if (m.direction !== "outbound") return null;
  switch (m.delivery_status) {
    case "pending":
    case "sending":
      return <span title="قيد الإرسال">🕓</span>;
    case "sent":
      return <span title="تم الإرسال">✓</span>;
    case "cancelled":
      return <span title="أُلغي (استلم موظف المحادثة)">⊘</span>;
    case "failed":
      return (
        <span style={{ color: "var(--danger)" }}>
          فشل الإرسال{" "}
          {(
            <button className="btn btn-sm btn-ghost" style={{ height: 22, padding: "0 6px" }} onClick={() => onRetry(m)}>
              إعادة
            </button>
          )}
        </span>
      );
    default:
      return null;
  }
}

export function Thread({ conversationId, onBack }: { conversationId: string; onBack: () => void }) {
  const { me, atLeast } = useSession();
  const [conv, setConv] = useState<ConversationDetail | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [olderCursor, setOlderCursor] = useState<string | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [staff, setStaff] = useState<Staff[]>([]);
  const [text, setText] = useState("");
  const [loadingOlder, setLoadingOlder] = useState(false);

  const scroller = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const keepOffset = useRef<number | null>(null);
  const markingRead = useRef(false);

  const refresh = useCallback(
    async (initial = false) => {
      try {
        const [detail, page] = await Promise.all([
          api<ConversationDetail>(`/api/v1/conversations/${conversationId}`),
          api<Page<Message>>(`/api/v1/conversations/${conversationId}/messages${qs({ limit: PAGE })}`),
        ]);
        setConv(detail);
        setMessages((cur) => mergeMessages(initial ? [] : cur, page.items));
        if (initial) setOlderCursor(page.older_cursor ?? null);
        setStatus("ready");
        if (detail.unread_count > 0 && detail.mode !== "closed" && !markingRead.current) {
          markingRead.current = true;
          api(`/api/v1/conversations/${conversationId}/read`, { method: "POST" })
            .catch(() => undefined)
            .finally(() => {
              markingRead.current = false;
            });
        }
      } catch (err) {
        if (initial) {
          setLoadError(errorMessage(err));
          setStatus("error");
        }
      }
    },
    [conversationId],
  );

  useEffect(() => {
    setStatus("loading");
    setMessages([]);
    setConv(null);
    setActionError(null);
    stickToBottom.current = true;
    void refresh(true);
  }, [refresh]);

  useEffect(() => {
    api<Staff[]>("/api/v1/team/staff")
      .then((s) => setStaff(s.filter((x) => x.is_active && x.user_id)))
      .catch(() => setStaff([]));
  }, []);

  const refreshSoon = useDebounced(() => void refresh(), 250);
  useLiveEvents((e) => {
    if (e.type === "resync" || e.conversation_id === conversationId) refreshSoon();
  });

  // التمرير: للأسفل عند الفتح/وصول رسالة (إذا كان المستخدم في الأسفل)، وثبات الموضع عند تحميل الأقدم
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (keepOffset.current !== null) {
      el.scrollTop = el.scrollHeight - keepOffset.current;
      keepOffset.current = null;
    } else if (stickToBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  function onScroll() {
    const el = scroller.current;
    if (el) stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  async function loadOlder() {
    if (!olderCursor || !scroller.current) return;
    setLoadingOlder(true);
    try {
      const page = await api<Page<Message>>(
        `/api/v1/conversations/${conversationId}/messages${qs({ cursor: olderCursor, limit: PAGE })}`,
      );
      keepOffset.current = scroller.current.scrollHeight - scroller.current.scrollTop;
      setMessages((cur) => mergeMessages(cur, page.items));
      setOlderCursor(page.older_cursor ?? null);
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setLoadingOlder(false);
    }
  }

  async function act(name: "takeover" | "release" | "close") {
    setBusy(name);
    setActionError(null);
    try {
      await api(`/api/v1/conversations/${conversationId}/${name}`, { method: "POST" });
      await refresh();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function assign(userId: string) {
    setBusy("assign");
    setActionError(null);
    try {
      await api(`/api/v1/conversations/${conversationId}/assign`, { body: { user_id: userId || null } });
      await refresh();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function send(body: string, clientId: string) {
    const optimistic: LocalMessage = {
      id: `local-${clientId}`,
      local: true,
      direction: "outbound",
      sender_type: "staff",
      msg_type: "text",
      text_content: body,
      created_at: new Date().toISOString(),
      client_msg_id: clientId,
      delivery_status: "pending",
      delivery_error: null,
    };
    stickToBottom.current = true;
    setMessages((cur) => [...cur.filter((m) => m.client_msg_id !== clientId), optimistic]);
    setActionError(null);
    try {
      await api(`/api/v1/conversations/${conversationId}/messages`, { body: { text: body, client_msg_id: clientId } });
      await refresh();
    } catch (err) {
      // فشل الطلب نفسه: الرسالة تبقى ظاهرة كفاشلة، والنص يرجع لمربع الكتابة إن كان فارغاً
      setActionError(errorMessage(err));
      if (err instanceof ApiError && err.status < 500) {
        // رفض نهائي (نافذة 24 ساعة، قناة غير مفعّلة...): نحذفها ونرجع النص لمربع الكتابة
        setMessages((cur) => cur.filter((m) => !(m.local && m.client_msg_id === clientId)));
        setText((t) => t || body);
      } else {
        setMessages((cur) =>
          cur.map((m) => (m.client_msg_id === clientId && m.local ? { ...m, delivery_status: "failed" } : m)),
        );
      }
    }
  }

  function submit() {
    const body = text.trim();
    if (!body) return;
    setText("");
    void send(body, newUuid());
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  async function retry(m: LocalMessage) {
    if (m.local) {
      // الطلب لم يصل للخادم (انقطاع): نفس client_msg_id => لا تكرار حتى لو وصل الأول فعلاً
      if (m.text_content && m.client_msg_id) void send(m.text_content, m.client_msg_id);
      return;
    }
    try {
      await api(`/api/v1/messages/${m.id}/retry`, { method: "POST" });
      await refresh();
    } catch (err) {
      setActionError(errorMessage(err));
    }
  }

  if (status === "loading") return <section className="thread"><LoadingState /></section>;
  if (status === "error" || !conv) {
    return (
      <section className="thread">
        <ErrorState message={loadError ?? ""} onRetry={() => void refresh(true)} />
      </section>
    );
  }

  const name = conv.contact_name || localPhone(conv.contact_phone) || "زبون";
  const paused = conv.mode === "bot" && conv.bot_paused_until && new Date(conv.bot_paused_until) > new Date();
  const canAssignOthers = atLeast("admin");
  const assignOptions = staff.filter((s) => canAssignOthers || s.user_id === me.user.id);
  const composerDisabled = conv.mode === "closed" || !conv.reply_window_open;

  return (
    <section className="thread" aria-label={`محادثة ${name}`}>
      <header className="thread-head">
        <button className="btn btn-sm btn-ghost mobile-only" onClick={onBack} aria-label="رجوع">
          <IconBack />
        </button>
        <Avatar text={initials(name)} />
        <div className="grow">
          <div style={{ fontWeight: 600 }} className="truncate">{name}</div>
          <div className="faint row" style={{ gap: 6 }}>
            <span>{CHANNEL_LABEL[conv.channel] ?? conv.channel}</span>
            {conv.contact_phone && <span className="ltr">{localPhone(conv.contact_phone)}</span>}
          </div>
        </div>
        <select className="select" style={{ width: "auto", height: 32 }} aria-label="إسناد المحادثة"
          value={conv.assigned_user_id ?? ""} disabled={busy !== null || conv.mode === "closed"}
          onChange={(e) => void assign(e.target.value)}>
          <option value="">غير مسندة</option>
          {conv.assigned_user_id && !assignOptions.some((s) => s.user_id === conv.assigned_user_id) && (
            <option value={conv.assigned_user_id}>{conv.assigned_name ?? "موظف"}</option>
          )}
          {assignOptions.map((s) => (
            <option key={s.id} value={s.user_id ?? ""}>
              {s.user_id === me.user.id ? `أنا (${s.full_name})` : s.full_name}
            </option>
          ))}
        </select>
        {conv.mode === "bot" && (
          <button className="btn btn-sm btn-primary" disabled={busy !== null} onClick={() => void act("takeover")}>
            {busy === "takeover" ? <Spinner /> : "استلام المحادثة"}
          </button>
        )}
        {conv.mode === "human" && (
          <button className="btn btn-sm" disabled={busy !== null} onClick={() => void act("release")}>
            {busy === "release" ? <Spinner /> : "إرجاع للبوت"}
          </button>
        )}
        {conv.mode !== "closed" && (
          <button className="btn btn-sm btn-ghost" disabled={busy !== null} onClick={() => void act("close")}>
            {busy === "close" ? <Spinner /> : "إغلاق"}
          </button>
        )}
      </header>

      {conv.mode === "human" && (
        <div className="thread-banner tone-warn">
          البوت متوقف في هذه المحادثة{conv.assigned_name ? `، ويتابعها ${conv.assigned_name}` : ""}. اضغط «إرجاع للبوت» عند الانتهاء.
        </div>
      )}
      {paused && (
        <div className="thread-banner tone-warn">
          البوت متوقف مؤقتاً حتى {fullTime(conv.bot_paused_until)} (تحويل لموظف أو رد من تطبيق الهاتف).
        </div>
      )}
      {conv.mode === "closed" && <div className="thread-banner tone-muted">المحادثة مغلقة. أي رسالة جديدة من الزبون تفتح محادثة جديدة.</div>}

      <div className="messages" ref={scroller} onScroll={onScroll}>
        {olderCursor && (
          <button className="btn btn-sm" style={{ alignSelf: "center" }} onClick={() => void loadOlder()} disabled={loadingOlder}>
            {loadingOlder ? <Spinner /> : "رسائل أقدم"}
          </button>
        )}
        {messages.length === 0 && <p className="faint" style={{ textAlign: "center" }}>لا توجد رسائل</p>}
        {messages.map((m, i) => {
          const showDay = i === 0 || !sameDay(messages[i - 1].created_at, m.created_at);
          const cls = m.direction === "inbound" ? "in" : `out${m.sender_type === "bot" ? " bot" : ""}`;
          return (
            <div key={m.id} style={{ display: "contents" }}>
              {showDay && <div className="day-sep">{dayLabel(m.created_at)}</div>}
              <div className={`bubble ${cls}${m.delivery_status === "failed" ? " failed" : ""}`}>
                {m.text_content ?? <span className="faint">[{m.msg_type}]</span>}
                <div className="bubble-meta">
                  {senderLabel(m) && <span>{senderLabel(m)}</span>}
                  <span>{clockTime(m.created_at)}</span>
                  <DeliveryMark m={m} onRetry={(x) => void retry(x)} />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <footer className="composer">
        {actionError && <Alert>{actionError}</Alert>}
        {!conv.reply_window_open && conv.mode !== "closed" && (
          <Alert tone="warn">مرت أكثر من 24 ساعة على آخر رسالة من الزبون. واتساب وفيسبوك لا يسمحان برسالة حرة حتى يراسلك الزبون من جديد.</Alert>
        )}
        {conv.mode === "bot" && !composerDisabled && (
          <div className="faint" style={{ marginBottom: 6 }}>
            إرسالك أي رسالة يوقف البوت ويستلم المحادثة باسمك.
          </div>
        )}
        <div className="composer-row">
          <textarea rows={1} placeholder={composerDisabled ? "لا يمكن الإرسال الآن" : "اكتب ردك... (Enter للإرسال، Shift+Enter لسطر جديد)"}
            value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKeyDown}
            disabled={composerDisabled} maxLength={4000} aria-label="نص الرد" />
          <button className="btn btn-primary" onClick={submit} disabled={composerDisabled || !text.trim()} aria-label="إرسال">
            <IconSend />
          </button>
        </div>
        {conv.has_open_lead && (
          <div style={{ marginTop: 6 }}>
            <Badge tone="info">لهذا الزبون طلب حجز مفتوح — تجده في «طلبات الحجز»</Badge>
          </div>
        )}
      </footer>
    </section>
  );
}
