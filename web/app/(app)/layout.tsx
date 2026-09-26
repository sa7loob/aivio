"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import { IconChannels, IconInbox, IconLeads, IconMenu, IconTeam } from "@/components/icons";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui";
import { api } from "@/lib/api";
import { LiveProvider, useLiveState } from "@/lib/events";
import { ROLE_LABEL } from "@/lib/format";
import { SessionProvider, useSession } from "@/lib/session";

const NAV = [
  { href: "/inbox", label: "المحادثات", icon: IconInbox },
  { href: "/leads", label: "طلبات الحجز", icon: IconLeads },
  { href: "/team", label: "الفريق", icon: IconTeam },
  { href: "/channels", label: "القنوات", icon: IconChannels },
];

function LiveIndicator() {
  const state = useLiveState();
  const map = {
    live: { color: "var(--success)", text: "متصل لحظياً" },
    connecting: { color: "var(--warn)", text: "جاري الاتصال..." },
    offline: { color: "var(--danger)", text: "غير متصل — حدّث الصفحة" },
  } as const;
  const s = map[state];
  return (
    <span className="faint row" title="التحديث اللحظي للمحادثات والطلبات">
      <span className="live-dot" style={{ background: s.color }} />
      {s.text}
    </span>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { me, tenant, switchTenant, logout } = useSession();
  const [open, setOpen] = useState(false);
  const current = NAV.find((n) => pathname.startsWith(n.href));

  return (
    <LiveProvider tenantId={tenant.tenant_id}>
      <div className="shell">
        <div className="mobile-bar">
          <button className="btn btn-sm btn-ghost" onClick={() => setOpen(true)} aria-label="القائمة">
            <IconMenu />
          </button>
          <strong className="grow truncate">{current?.label ?? tenant.name}</strong>
          <LiveIndicator />
        </div>
        {open && <div className="drawer-backdrop" style={{ zIndex: 55 }} onClick={() => setOpen(false)} />}
        <nav className={`sidebar${open ? " open" : ""}`} aria-label="القائمة الرئيسية">
          <div className="brand" style={{ padding: "0 8px" }}>
            <span className="brand-mark" aria-hidden>و</span>
            <span className="truncate">{tenant.name}</span>
          </div>
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link key={href} href={href} onClick={() => setOpen(false)}
              className={`nav-link${pathname.startsWith(href) ? " active" : ""}`}>
              <Icon />
              {label}
            </Link>
          ))}
          <div className="sidebar-foot">
            <LiveIndicator />
            {me.tenants.length > 1 && (
              <select className="select" aria-label="النشاط" value={tenant.tenant_id}
                onChange={(e) => switchTenant(e.target.value)}>
                {me.tenants.map((t) => (
                  <option key={t.tenant_id} value={t.tenant_id}>{t.name}</option>
                ))}
              </select>
            )}
            <div style={{ fontSize: 13 }}>
              <div className="truncate" style={{ fontWeight: 600 }}>{me.user.full_name}</div>
              <div className="faint">{ROLE_LABEL[tenant.role]}</div>
            </div>
            <button className="btn btn-sm" onClick={() => void logout()}>تسجيل الخروج</button>
          </div>
        </nav>
        <div className="main">{children}</div>
      </div>
    </LiveProvider>
  );
}

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <SessionProvider
      loading={<div className="auth-wrap"><LoadingState /></div>}
      failed={(message, retry) => (
        <div className="auth-wrap"><ErrorState message={message} onRetry={retry} /></div>
      )}
      noTenant={(me) => (
        <div className="auth-wrap">
          <EmptyState
            title={`أهلاً ${me.user.full_name}`}
            text="حسابك غير مرتبط بأي نشاط (ربما أُلغيت عضويتك). اطلب من صاحب النشاط رابط دعوة جديد وافتحه."
            action={
              <button className="btn" onClick={() => {
                void api("/api/v1/auth/logout", { method: "POST", tenant: false })
                  .finally(() => window.location.assign("/login"));
              }}>تسجيل الخروج</button>
            }
          />
        </div>
      )}
    >
      <Shell>{children}</Shell>
    </SessionProvider>
  );
}
