"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Alert, Avatar, Badge, EmptyState, ErrorState, SkeletonRows, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { ROLE_LABEL, fullTime, initials, longDate } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Invitation, Member, Role } from "@/lib/types";

type Load<T> = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: T };

interface CreatedInvite {
  link: string;
  role: Role;
  note: string;
  expires_at: string;
}

function invitationState(i: Invitation): { label: string; tone: "success" | "muted" | "danger" | "info" } {
  if (i.accepted_at) return { label: "تم القبول", tone: "success" };
  if (i.revoked_at) return { label: "ملغاة", tone: "muted" };
  if (new Date(i.expires_at) <= new Date()) return { label: "منتهية", tone: "danger" };
  return { label: "بانتظار القبول", tone: "info" };
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // الحافظة غير متاحة (http أو صلاحية): المستخدم ينسخ من الحقل يدوياً
      setCopied(false);
    }
  }
  return (
    <button type="button" className="btn btn-sm" onClick={() => void copy()}>
      {copied ? "تم النسخ ✓" : "نسخ الرابط"}
    </button>
  );
}

function InviteResult({ invite, onDone }: { invite: CreatedInvite; onDone: () => void }) {
  const message =
    `السلام عليكم${invite.note ? ` ${invite.note}` : ""}، هذا رابط الانضمام لفريقنا في لوحة الوكيل الذكي ` +
    `(${ROLE_LABEL[invite.role]}):\n${invite.link}`;
  return (
    <div className="card invite-result">
      <Alert tone="success">
        تم إنشاء الدعوة. انسخ الرابط الآن وأرسله للموظف — لن يظهر مرة أخرى. صالح حتى {longDate(invite.expires_at)}.
      </Alert>
      <input className="input ltr" readOnly value={invite.link} aria-label="رابط الدعوة"
        onFocus={(e) => e.currentTarget.select()} />
      <div className="row" style={{ marginTop: 10, flexWrap: "wrap" }}>
        <CopyButton text={invite.link} />
        <a className="btn btn-sm" href={`https://wa.me/?text=${encodeURIComponent(message)}`} target="_blank"
          rel="noopener noreferrer">
          مشاركة عبر واتساب
        </a>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onDone}>تم</button>
      </div>
    </div>
  );
}

export default function TeamPage() {
  const { me, tenant, atLeast } = useSession();
  const isAdmin = atLeast("admin");
  const [members, setMembers] = useState<Load<Member[]>>({ kind: "loading" });
  const [invites, setInvites] = useState<Load<Invitation[]>>({ kind: "loading" });
  const [role, setRole] = useState<Role>("agent");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedInvite | null>(null);

  const loadMembers = useCallback(async () => {
    setMembers({ kind: "loading" });
    try {
      setMembers({ kind: "ready", data: await api<Member[]>("/api/v1/team/members") });
    } catch (err) {
      setMembers({ kind: "error", message: errorMessage(err) });
    }
  }, []);

  const loadInvites = useCallback(async (quiet = false) => {
    if (!quiet) setInvites({ kind: "loading" });
    try {
      setInvites({ kind: "ready", data: await api<Invitation[]>("/api/v1/team/invitations") });
    } catch (err) {
      setInvites((cur) => (quiet && cur.kind === "ready" ? cur : { kind: "error", message: errorMessage(err) }));
    }
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    void loadMembers();
    void loadInvites();
  }, [isAdmin, loadMembers, loadInvites]);

  async function createInvite(e: FormEvent) {
    e.preventDefault();
    setBusy("create");
    setActionError(null);
    try {
      const res = await api<{ id: string; token: string; expires_at: string }>("/api/v1/team/invitations", {
        body: { role, note: note.trim() || null },
      });
      setCreated({
        link: `${window.location.origin}/register?invite=${encodeURIComponent(res.token)}`,
        role,
        note: note.trim(),
        expires_at: res.expires_at,
      });
      setNote("");
      void loadInvites(true);
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function revoke(inv: Invitation) {
    if (!window.confirm("إلغاء هذه الدعوة؟ الرابط لن يعمل بعدها.")) return;
    setBusy(inv.id);
    setActionError(null);
    try {
      await api(`/api/v1/team/invitations/${inv.id}`, { method: "DELETE" });
      await loadInvites(true);
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  if (!isAdmin) {
    return (
      <div className="page">
        <div className="page-head"><h1>الفريق</h1></div>
        <div className="card">
          <EmptyState title="هذه الصفحة للمدير فقط"
            text="إدارة الفريق ودعوة الموظفين من صلاحية صاحب النشاط أو المدير. تواصل معهم لإضافة زميل." />
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head"><h1>الفريق</h1></div>
      {actionError && <Alert>{actionError}</Alert>}

      <div className="grid-2 team-grid">
        <section>
          <div className="section-title" style={{ marginTop: 0 }}>الأعضاء</div>
          <div className="card">
            {members.kind === "loading" && <SkeletonRows rows={3} />}
            {members.kind === "error" && <ErrorState message={members.message} onRetry={() => void loadMembers()} />}
            {members.kind === "ready" && members.data.length === 0 && <EmptyState title="لا يوجد أعضاء" />}
            {members.kind === "ready" &&
              members.data.map((m) => (
                <div key={m.user_id} className="list-row">
                  <Avatar text={initials(m.full_name)} />
                  <div className="grow">
                    <div style={{ fontWeight: 600 }} className="truncate">
                      {m.full_name}
                      {m.user_id === me.user.id && <span className="faint"> (أنت)</span>}
                    </div>
                    <div className="faint ltr truncate">{m.email}</div>
                  </div>
                  <div style={{ textAlign: "end" }}>
                    <Badge tone={m.role === "agent" ? "neutral" : "info"}>{ROLE_LABEL[m.role] ?? m.role}</Badge>
                    {m.status !== "active" && (
                      <div><Badge tone="muted">غير نشط</Badge></div>
                    )}
                  </div>
                </div>
              ))}
          </div>
        </section>

        <section>
          <div className="section-title" style={{ marginTop: 0 }}>دعوة موظف</div>
          {created ? (
            <InviteResult invite={created} onDone={() => setCreated(null)} />
          ) : (
            <form className="card" style={{ padding: 16 }} onSubmit={createInvite}>
              <div className="field">
                <label className="label" htmlFor="inv-role">الصلاحية</label>
                <select id="inv-role" className="select" value={role} onChange={(e) => setRole(e.target.value as Role)}>
                  <option value="agent">موظف — المحادثات وطلبات الحجز</option>
                  {tenant.role === "owner" && <option value="admin">مدير — ويشمل الفريق والقنوات والتصدير</option>}
                </select>
              </div>
              <div className="field">
                <label className="label" htmlFor="inv-note">لمن الدعوة؟ (اختياري)</label>
                <input id="inv-note" className="input" maxLength={200} placeholder="مثال: سارة - المبيعات"
                  value={note} onChange={(e) => setNote(e.target.value)} />
                <div className="hint">للتذكير فقط؛ يظهر في قائمة الدعوات</div>
              </div>
              <button className="btn btn-primary" disabled={busy !== null}>
                {busy === "create" ? <Spinner /> : "إنشاء رابط دعوة"}
              </button>
            </form>
          )}

          <div className="section-title">الدعوات</div>
          <div className="card">
            {invites.kind === "loading" && <SkeletonRows rows={2} />}
            {invites.kind === "error" && <ErrorState message={invites.message} onRetry={() => void loadInvites()} />}
            {invites.kind === "ready" && invites.data.length === 0 && (
              <EmptyState title="لا توجد دعوات" text="أنشئ رابط دعوة وأرسله للموظف عبر واتساب." />
            )}
            {invites.kind === "ready" &&
              invites.data.map((i) => {
                const st = invitationState(i);
                return (
                  <div key={i.id} className="list-row">
                    <div className="grow">
                      <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                        <span style={{ fontWeight: 600 }}>{i.note || i.email || "دعوة"}</span>
                        <Badge tone="neutral">{ROLE_LABEL[i.role] ?? i.role}</Badge>
                        <Badge tone={st.tone}>{st.label}</Badge>
                      </div>
                      <div className="faint">
                        أُنشئت {fullTime(i.created_at)}
                        {st.tone === "info" && ` · تنتهي ${longDate(i.expires_at)}`}
                      </div>
                    </div>
                    {st.tone === "info" && (
                      <button className="btn btn-sm btn-danger" disabled={busy !== null} onClick={() => void revoke(i)}>
                        {busy === i.id ? <Spinner /> : "إلغاء"}
                      </button>
                    )}
                  </div>
                );
              })}
          </div>
        </section>
      </div>
    </div>
  );
}
