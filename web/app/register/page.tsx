"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { AuthCard } from "@/components/AuthCard";
import { Alert, LoadingState, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";

const MIN_PASSWORD = 10;

function RegisterForm() {
  const router = useRouter();
  const invite = useSearchParams().get("invite");
  const [form, setForm] = useState({ full_name: "", email: "", phone: "", password: "", business_name: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = (k: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  const valid =
    form.full_name.trim().length >= 2 &&
    form.email.includes("@") &&
    form.password.length >= MIN_PASSWORD &&
    (invite ? true : form.business_name.trim().length >= 2);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/v1/auth/register", {
        tenant: false,
        body: {
          full_name: form.full_name.trim(),
          email: form.email.trim(),
          phone: form.phone.trim() || null,
          password: form.password,
          ...(invite ? { invite_token: invite } : { business_name: form.business_name.trim() }),
        },
      });
      // نشاط جديد => أول خطوة ربط القنوات. موظف مدعو => المحادثات مباشرة.
      router.replace(invite ? "/inbox" : "/channels?welcome=1");
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <AuthCard
      title={invite ? "الانضمام للفريق" : "تسجيل نشاط جديد"}
      subtitle={invite ? "أنشئ حسابك لقبول الدعوة" : "فترة تجريبية مجانية، وبعدها تربط واتساب وفيسبوك وإنستغرام"}
    >
      {error && <Alert>{error}</Alert>}
      <form onSubmit={submit} noValidate>
        <div className="field">
          <label className="label" htmlFor="full_name">الاسم</label>
          <input id="full_name" className="input" autoComplete="name" value={form.full_name} onChange={set("full_name")} />
        </div>
        {!invite && (
          <div className="field">
            <label className="label" htmlFor="business_name">اسم النشاط</label>
            <input id="business_name" className="input" placeholder="مثال: وكالة النور للحج والعمرة"
              value={form.business_name} onChange={set("business_name")} />
          </div>
        )}
        <div className="field">
          <label className="label" htmlFor="email">البريد الإلكتروني</label>
          <input id="email" className="input ltr" type="email" autoComplete="email" value={form.email} onChange={set("email")} />
        </div>
        <div className="field">
          <label className="label" htmlFor="phone">رقم واتساب (اختياري)</label>
          <input id="phone" className="input ltr" type="tel" inputMode="tel" placeholder="0912345678"
            value={form.phone} onChange={set("phone")} />
          <div className="hint">تصلك عليه إشعارات طلبات الحجز الجديدة</div>
        </div>
        <div className="field">
          <label className="label" htmlFor="password">كلمة المرور</label>
          <input id="password" className="input ltr" type="password" autoComplete="new-password"
            value={form.password} onChange={set("password")} />
          <div className="hint">{MIN_PASSWORD} أحرف على الأقل</div>
        </div>
        <button className="btn btn-primary" style={{ width: "100%" }} disabled={busy || !valid}>
          {busy ? <Spinner /> : invite ? "إنشاء الحساب والانضمام" : "إنشاء الحساب"}
        </button>
      </form>
      <p className="muted" style={{ marginBottom: 0, fontSize: 13.5 }}>
        عندك حساب؟{" "}
        <Link href={invite ? `/login?invite=${encodeURIComponent(invite)}` : "/login"}>سجّل دخولك</Link>
      </p>
    </AuthCard>
  );
}

export default function RegisterPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <RegisterForm />
    </Suspense>
  );
}
