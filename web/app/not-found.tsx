import Link from "next/link";

export default function NotFound() {
  return (
    <div className="auth-wrap">
      <div className="center-state">
        <h3>الصفحة غير موجودة</h3>
        <Link href="/inbox">الرجوع للمحادثات</Link>
      </div>
    </div>
  );
}
