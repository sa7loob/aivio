"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { AuthCard } from "@/components/AuthCard";
import { Alert, LoadingState, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";

function ResetForm() {
  const token = useSearchParams().get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setError("كلمتا المرور غير متطابقتين");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/api/v1/auth/password-reset", { body: { token, new_password: password }, tenant: false });
      setDone(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <AuthCard title="تغيير كلمة المرور">
        <Alert>الرابط ناقص. افتح الرابط كما وصلك من الدعم.</Alert>
      </AuthCard>
    );
  }
  if (done) {
    return (
      <AuthCard title="تم تغيير كلمة المرور">
        <Alert tone="success">سجّل دخولك الآن بكلمة المرور الجديدة.</Alert>
        <Link className="btn btn-primary" href="/login" style={{ width: "100%" }}>تسجيل الدخول</Link>
      </AuthCard>
    );
  }
  return (
    <AuthCard title="تغيير كلمة المرور" subtitle="الرابط يُستخدم مرة واحدة، وكل الأجهزة ستخرج من الحساب">
      {error && <Alert>{error}</Alert>}
      <form onSubmit={submit} noValidate>
        <div className="field">
          <label className="label" htmlFor="p1">كلمة المرور الجديدة</label>
          <input id="p1" className="input ltr" type="password" autoComplete="new-password"
            value={password} onChange={(e) => setPassword(e.target.value)} />
          <div className="hint">10 أحرف على الأقل</div>
        </div>
        <div className="field">
          <label className="label" htmlFor="p2">تأكيد كلمة المرور</label>
          <input id="p2" className="input ltr" type="password" autoComplete="new-password"
            value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </div>
        <button className="btn btn-primary" style={{ width: "100%" }} disabled={busy || password.length < 10}>
          {busy ? <Spinner /> : "حفظ"}
        </button>
      </form>
    </AuthCard>
  );
}

export default function ResetPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <ResetForm />
    </Suspense>
  );
}
