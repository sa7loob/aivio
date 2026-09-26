"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Drawer, ErrorState, LoadingState, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useDebounced, useLiveEvents } from "@/lib/events";
import {
  CHANNEL_LABEL,
  LEAD_STATUS,
  LEAD_STATUS_LABEL,
  ROOM_LABEL,
  fullTime,
  localPhone,
  longDate,
  travelers,
} from "@/lib/format";
import type { Lead, LeadDetail, LeadEvent, LeadStatus, Staff } from "@/lib/types";

const ACTOR: Record<string, string> = { bot: "البوت", system: "النظام" };

function statusTone(s: string) {
  return LEAD_STATUS.find((x) => x.value === s)?.tone ?? "neutral";
}

function describe(e: LeadEvent, staffName: (id: unknown) => string): string {
  const d = e.data as Record<string, unknown>;
  switch (e.event_type) {
    case "created":
      return "أنشأ الطلب من المحادثة";
    case "updated":
      return d.field === "notes" ? "عدّل الملاحظات" : "حدّث بيانات الطلب";
    case "status_changed":
      return `غيّر الحالة من «${LEAD_STATUS_LABEL[String(d.from)] ?? d.from}» إلى «${LEAD_STATUS_LABEL[String(d.to)] ?? d.to}»`;
    case "assigned":
      return d.to ? `أسند الطلب إلى ${staffName(d.to)}` : "ألغى إسناد الطلب";
    case "note":
      return String(d.text ?? "");
    case "notification_sent":
      return "أُرسل إشعار واتساب للموظف";
    case "notification_failed":
      return "تعذّر إرسال إشعار واتساب للموظف";
    default:
      return e.event_type;
  }
}

export function LeadDrawer({ leadId, staff, onClose, onChanged }: {
  leadId: string;
  staff: Staff[];
  onClose: () => void;
  onChanged: (lead: Lead) => void;
}) {
  const [lead, setLead] = useState<LeadDetail | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [pendingLost, setPendingLost] = useState(false);
  const [lostReason, setLostReason] = useState("");
  const [notes, setNotes] = useState<string | null>(null); // null = لم يُعدَّل (نعرض قيمة الخادم)
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    try {
      const d = await api<LeadDetail>(`/api/v1/leads/${leadId}`);
      setLead(d);
      setStatus("ready");
    } catch (err) {
      setLoadError(errorMessage(err));
      setStatus((s) => (s === "ready" ? s : "error"));
    }
  }, [leadId]);

  useEffect(() => {
    void load();
  }, [load]);

  const reloadSoon = useDebounced(() => void load(), 300);
  useLiveEvents((e) => {
    if ((e.type === "lead" && e.id === leadId) || e.type === "resync") reloadSoon();
  });

  const staffName = (id: unknown) => staff.find((s) => s.id === id)?.full_name ?? "موظف";

  async function patch(body: Record<string, unknown>, what: string): Promise<boolean> {
    setBusy(what);
    setActionError(null);
    try {
      const updated = await api<Lead>(`/api/v1/leads/${leadId}`, { method: "PATCH", body });
      onChanged(updated);
      await load();
      return true;
    } catch (err) {
      setActionError(errorMessage(err));
      return false;
    } finally {
      setBusy(null);
    }
  }

  function onStatus(value: LeadStatus) {
    if (value === "lost") {
      setPendingLost(true);
      setLostReason(lead?.lost_reason ?? "");
      return;
    }
    setPendingLost(false);
    void patch({ status: value }, "status");
  }

  async function confirmLost() {
    if (await patch({ status: "lost", lost_reason: lostReason.trim() }, "status")) setPendingLost(false);
  }

  async function saveNotes() {
    if (notes === null) return;
    if (await patch({ notes: notes.trim() }, "notes")) setNotes(null);
  }

  async function addNote() {
    const text = note.trim();
    if (!text) return;
    setBusy("note");
    setActionError(null);
    try {
      await api(`/api/v1/leads/${leadId}/notes`, { body: { text } });
      setNote("");
      await load();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const title = lead ? lead.full_name || localPhone(lead.phone_e164) || "طلب حجز" : "طلب حجز";

  return (
    <Drawer title={title} onClose={onClose}
      actions={lead && <Badge tone={statusTone(lead.status)}>{LEAD_STATUS_LABEL[lead.status]}</Badge>}>
      {status === "loading" && <LoadingState />}
      {status === "error" && <ErrorState message={loadError ?? ""} onRetry={() => void load()} />}
      {status === "ready" && lead && (
        <>
          {actionError && <Alert>{actionError}</Alert>}

          <dl className="kv">
            <dt>الهاتف</dt>
            <dd>
              {lead.phone_e164 ? (
                <a className="ltr" href={`tel:${lead.phone_e164}`}>{localPhone(lead.phone_e164)}</a>
              ) : "—"}
            </dd>
            <dt>المدينة</dt>
            <dd>{lead.city || "—"}</dd>
            <dt>البرنامج</dt>
            <dd>{lead.package_title || "—"}</dd>
            {lead.depart_date && (
              <>
                <dt>موعد الانطلاق</dt>
                <dd>{longDate(lead.depart_date)}</dd>
              </>
            )}
            <dt>المسافرون</dt>
            <dd>{travelers(lead)}</dd>
            <dt>الغرفة</dt>
            <dd>{lead.room_type_pref ? ROOM_LABEL[lead.room_type_pref] ?? lead.room_type_pref : "—"}</dd>
            <dt>الفترة المفضلة</dt>
            <dd>{lead.preferred_period || "—"}</dd>
            <dt>المصدر</dt>
            <dd>
              {CHANNEL_LABEL[lead.source_channel ?? ""] ?? lead.source_channel ?? "—"}
              {lead.collected_by === "bot" ? " (البوت)" : ""}
            </dd>
            <dt>تاريخ الطلب</dt>
            <dd>{fullTime(lead.created_at)}</dd>
            {lead.first_contact_at && (
              <>
                <dt>أول متابعة</dt>
                <dd>{fullTime(lead.first_contact_at)}</dd>
              </>
            )}
            {lead.status === "lost" && lead.lost_reason && (
              <>
                <dt>سبب عدم الإتمام</dt>
                <dd>{lead.lost_reason}</dd>
              </>
            )}
          </dl>

          {lead.conversation_id && (
            <Link className="btn btn-sm" href={`/inbox?c=${lead.conversation_id}`}>فتح المحادثة</Link>
          )}

          <div className="section-title">الحالة والإسناد</div>
          <div className="grid-2">
            <div className="field">
              <label className="label" htmlFor="lead-status">الحالة</label>
              <select id="lead-status" className="select" disabled={busy !== null}
                value={pendingLost ? "lost" : lead.status}
                onChange={(e) => onStatus(e.target.value as LeadStatus)}>
                {LEAD_STATUS.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="label" htmlFor="lead-assign">الموظف المسؤول</label>
              <select id="lead-assign" className="select" disabled={busy !== null} value={lead.assigned_to ?? ""}
                onChange={(e) =>
                  void patch(e.target.value ? { assigned_to: e.target.value } : { unassign: true }, "assign")
                }>
                <option value="">غير مسند</option>
                {lead.assigned_to && !staff.some((s) => s.id === lead.assigned_to && s.is_active) && (
                  <option value={lead.assigned_to}>{lead.assigned_name ?? "موظف"}</option>
                )}
                {staff.filter((s) => s.is_active).map((s) => (
                  <option key={s.id} value={s.id}>{s.full_name}</option>
                ))}
              </select>
            </div>
          </div>
          {pendingLost && (
            <div className="card" style={{ padding: 12, marginBottom: 14 }}>
              <label className="label" htmlFor="lost-reason">سبب عدم الإتمام (إلزامي)</label>
              <input id="lost-reason" className="input" maxLength={500} autoFocus value={lostReason}
                placeholder="مثال: السعر عالي، سافر مع مكتب آخر..."
                onChange={(e) => setLostReason(e.target.value)} />
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn btn-sm btn-primary" disabled={busy !== null || !lostReason.trim()}
                  onClick={() => void confirmLost()}>
                  {busy === "status" ? <Spinner /> : "حفظ"}
                </button>
                <button className="btn btn-sm btn-ghost" disabled={busy !== null} onClick={() => setPendingLost(false)}>
                  إلغاء
                </button>
              </div>
            </div>
          )}

          <div className="section-title">ملاحظات الطلب</div>
          <textarea className="textarea" maxLength={4000} aria-label="ملاحظات الطلب"
            value={notes ?? lead.notes ?? ""} onChange={(e) => setNotes(e.target.value)}
            placeholder="تفاصيل تهم فريق المبيعات" />
          {notes !== null && notes.trim() !== (lead.notes ?? "") && (
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn btn-sm btn-primary" disabled={busy !== null} onClick={() => void saveNotes()}>
                {busy === "notes" ? <Spinner /> : "حفظ الملاحظات"}
              </button>
              <button className="btn btn-sm btn-ghost" disabled={busy !== null} onClick={() => setNotes(null)}>
                تراجع
              </button>
            </div>
          )}

          <div className="section-title">سجل المتابعة</div>
          <div className="field">
            <textarea className="textarea" style={{ minHeight: 60 }} maxLength={2000} aria-label="ملاحظة متابعة"
              value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="مثال: اتصلت بالزبون، يبي يأكد بعد الراتب" />
            <button className="btn btn-sm" style={{ marginTop: 8 }} disabled={busy !== null || !note.trim()}
              onClick={() => void addNote()}>
              {busy === "note" ? <Spinner /> : "إضافة للسجل"}
            </button>
          </div>
          {lead.events.length === 0 ? (
            <p className="faint">لا توجد أحداث بعد</p>
          ) : (
            <ol className="timeline">
              {[...lead.events].reverse().map((e) => (
                <li key={e.id}>
                  <div className={e.event_type === "note" ? "" : "muted"} style={{ whiteSpace: "pre-wrap" }}>
                    {describe(e, staffName)}
                  </div>
                  <div className="faint">
                    {e.actor_type === "staff" ? e.actor_name ?? "موظف" : ACTOR[e.actor_type] ?? e.actor_type}
                    {" · "}
                    {fullTime(e.created_at)}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </Drawer>
  );
}
