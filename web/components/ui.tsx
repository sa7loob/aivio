"use client";

import { useEffect, type ReactNode } from "react";
import type { Tone } from "@/lib/format";

export function Spinner({ label }: { label?: string }) {
  return <span className="spinner" role="status" aria-label={label ?? "جاري التحميل"} />;
}

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge tone-${tone}`}>{children}</span>;
}

export function Alert({ tone = "danger", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <div className={`alert tone-${tone}`} role={tone === "danger" ? "alert" : "status"}>
      {children}
    </div>
  );
}

export function LoadingState({ text = "جاري التحميل..." }: { text?: string }) {
  return (
    <div className="center-state">
      <Spinner />
      <p>{text}</p>
    </div>
  );
}

export function EmptyState({ title, text, action }: { title: string; text?: string; action?: ReactNode }) {
  return (
    <div className="center-state">
      <h3>{title}</h3>
      {text && <p>{text}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="center-state">
      <h3>تعذّر التحميل</h3>
      <p>{message}</p>
      {onRetry && (
        <button className="btn btn-sm" onClick={onRetry}>
          إعادة المحاولة
        </button>
      )}
    </div>
  );
}

export function SkeletonRows({ rows = 6 }: { rows?: number }) {
  return (
    <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 14 }} aria-hidden>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="row">
          <div className="skeleton" style={{ width: 38, height: 38, borderRadius: "50%" }} />
          <div className="grow" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div className="skeleton" style={{ height: 12, width: "45%" }} />
            <div className="skeleton" style={{ height: 10, width: "80%" }} />
          </div>
        </div>
      ))}
    </div>
  );
}

export function Drawer({ title, onClose, children, actions }: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  actions?: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="drawer-head">
          <div className="grow" style={{ fontWeight: 600 }}>
            {title}
          </div>
          {actions}
          <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="إغلاق">
            ✕
          </button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>
  );
}

export function Avatar({ text }: { text: string }) {
  return <span className="avatar">{text}</span>;
}
