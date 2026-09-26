import type { ReactNode } from "react";

export function AuthCard({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <main className="auth-wrap">
      <div className="card auth-card">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            و
          </span>
          الوكيل الذكي
        </div>
        <h1>{title}</h1>
        {subtitle && (
          <p className="muted" style={{ marginTop: 0 }}>
            {subtitle}
          </p>
        )}
        {children}
      </div>
    </main>
  );
}
