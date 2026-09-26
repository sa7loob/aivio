"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { IconChannels } from "@/components/icons";
import { Alert, Badge, EmptyState, ErrorState, LoadingState, SkeletonRows, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { CHANNEL_LABEL, CHANNEL_STATUS, fullTime, shortTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ChannelAccount } from "@/lib/types";

type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: ChannelAccount[] };

function ChannelsView() {
  const router = useRouter();
  const welcome = useSearchParams().get("welcome") === "1";
  const { tenant, atLeast } = useSession();
  const isAdmin = atLeast("admin");
  const [state, setState] = useState<Load>({ kind: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setState({ kind: "loading" });
    try {
      setState({ kind: "ready", data: await api<ChannelAccount[]>("/api/v1/channels") });
    } catch (err) {
      // تحديث صامت فشل (مثلاً عند الرجوع للتبويب): نُبقي القائمة المعروضة
      setState((cur) => (quiet && cur.kind === "ready" ? cur : { kind: "error", message: errorMessage(err) }));
    }
  }, []);

  useEffect(() => {
    void load();
    // الرجوع من صفحة الربط (/connect) في نفس التبويب أو تبويب آخر => الحالة الجديدة
    const onFocus = () => void load(true);
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [load]);

  async function disconnect(ch: ChannelAccount) {
    const name = `${CHANNEL_LABEL[ch.channel] ?? ch.channel} ${ch.display_name ?? ch.external_id}`;
    if (!window.confirm(`فصل ${name}؟ سيتوقف استقبال الرسائل والرد عليها في هذه القناة فوراً.`)) return;
    setBusy(ch.id);
    setActionError(null);
    try {
      await api(`/api/v1/channels/${ch.id}/disconnect`, { method: "POST" });
      await load(true);
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  // صفحة الربط من الباكيند (تدفقات Meta)، ونمرر النشاط الحالي حتى لا تُربط القناة بنشاط آخر
  const connectHref = `/connect?tenant=${encodeURIComponent(tenant.tenant_id)}`;
  const channels = state.kind === "ready" ? state.data : [];
  const hasActive = channels.some((c) => c.status === "active");

  return (
    <div className="page">
      <div className="page-head">
        <h1 className="grow">القنوات</h1>
        {isAdmin && (
          <a className="btn btn-sm btn-primary" href={connectHref}>ربط قناة</a>
        )}
      </div>

      {welcome && (
        <div className="card welcome">
          <strong>أهلاً بك في لوحة الوكيل الذكي 👋</strong>
          <p>
            تم إنشاء حسابك ونشاطك. الخطوة الأولى: اربط رقم واتساب أو صفحة فيسبوك/إنستغرام، ليبدأ الوكيل بالرد على
            زبائنك وتظهر محادثاتهم هنا.
          </p>
          <div className="row" style={{ flexWrap: "wrap" }}>
            {isAdmin && <a className="btn btn-primary" href={connectHref}>ربط أول قناة</a>}
            <button className="btn btn-ghost" onClick={() => router.replace("/channels")}>لاحقاً</button>
          </div>
        </div>
      )}

      {actionError && <Alert>{actionError}</Alert>}
      {!isAdmin && <Alert tone="info">ربط القنوات وفصلها من صلاحية المدير أو صاحب النشاط.</Alert>}
      {state.kind === "ready" && channels.length > 0 && !hasActive && (
        <Alert tone="warn">لا توجد قناة مفعّلة الآن، فالبوت لا يستقبل رسائل. أعد ربط قناة ليعود العمل.</Alert>
      )}

      <div className="card">
        {state.kind === "loading" && <SkeletonRows rows={2} />}
        {state.kind === "error" && <ErrorState message={state.message} onRetry={() => void load()} />}
        {state.kind === "ready" && channels.length === 0 && (
          <EmptyState title="لا توجد قنوات مربوطة"
            text="اربط واتساب أو ماسنجر أو إنستغرام حتى يصل الوكيل الذكي لزبائنك."
            action={isAdmin ? <a className="btn btn-primary btn-sm" href={connectHref}>ربط قناة</a> : undefined} />
        )}
        {channels.map((ch) => {
          const st = CHANNEL_STATUS[ch.status] ?? { label: ch.status, tone: "neutral" as const };
          // disconnected: last_error يحمل سبباً تقنياً داخلياً لا يفيد المستخدم
          const problem = ch.status !== "disconnected" && ch.last_error;
          return (
            <div key={ch.id} className="list-row channel-row">
              <span className={`channel-icon ch-${ch.channel}`} aria-hidden><IconChannels /></span>
              <div className="grow">
                <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                  <strong>{CHANNEL_LABEL[ch.channel] ?? ch.channel}</strong>
                  <span className="ltr truncate">{ch.display_name || ch.external_id}</span>
                  <Badge tone={st.tone}>{st.label}</Badge>
                  {ch.is_test && <Badge tone="muted">رقم تجريبي</Badge>}
                </div>
                <div className="faint">
                  رُبطت {shortTime(ch.created_at)}
                  {ch.last_checked_at && <span title={fullTime(ch.last_checked_at)}> · آخر فحص {shortTime(ch.last_checked_at)}</span>}
                </div>
                {problem && <div className="faint" style={{ color: "var(--danger)" }}>{ch.last_error}</div>}
              </div>
              {isAdmin && ch.status === "needs_reauth" && (
                <a className="btn btn-sm btn-primary" href={connectHref}>إعادة الربط</a>
              )}
              {isAdmin && ch.status !== "disconnected" && (
                <button className="btn btn-sm btn-danger" disabled={busy !== null} onClick={() => void disconnect(ch)}>
                  {busy === ch.id ? <Spinner /> : "فصل"}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function ChannelsPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <ChannelsView />
    </Suspense>
  );
}
