"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { AuthCard } from "@/components/AuthCard";
import { Alert, LoadingState, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";

function safeNext(next: string | null): string {
  // فقط مسارات داخلية (لا //evil.com ولا روابط كاملة)
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/inbox";
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const invite = params.get("invite");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/v1/auth/login", { body: { email, password }, tenant: false });
      if (invite) {
        await api("/api/v1/team/invitations/accept", { body: { token: invite }, tenant: false });
      }
      router.replace(safeNext(params.get("next")));
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <AuthCard
      title="تسجيل الدخول"
      subtitle={invite ? "سجّل دخولك لقبول الدعوة والانضمام للنشاط" : "ادخل لإدارة محادثات زبائنك وطلباتهم"}
    >
      {error && <Alert>{error}</Alert>}
      <form onSubmit={submit} noValidate>
        <div className="field">
          <label className="label" htmlFor="email">
            البريد الإلكتروني
          </label>
          <input id="email" className="input ltr" type="email" autoComplete="email" required
            value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="field">
          <label className="label" htmlFor="password">
            كلمة المرور
          </label>
          <input id="password" className="input ltr" type="password" autoComplete="current-password" required
            value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <button className="btn btn-primary" style={{ width: "100%" }} disabled={busy || !email || !password}>
          {busy ? <Spinner /> : "دخول"}
        </button>
      </form>
      <p className="muted" style={{ marginBottom: 0, fontSize: 13.5 }}>
        ما عندكش حساب؟{" "}
        <Link href={invite ? `/register?invite=${encodeURIComponent(invite)}` : "/register"}>سجّل نشاطك</Link>
        <br />
        نسيت كلمة المرور؟ تواصل مع الدعم ليرسل لك رابط تغييرها.
      </p>
    </AuthCard>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <LoginForm />
    </Suspense>
  );
}
