// عميل الـ API: نفس الـ origin (كوكي الجلسة httpOnly)، والوكالة في هيدر X-Tenant-ID.
// الصلاحيات تُفرض في الباكيند؛ إخفاء الأزرار في الواجهة للراحة فقط.

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

const MESSAGES: Record<string, string> = {
  not_authenticated: "سجّل دخولك من جديد",
  session_expired: "انتهت الجلسة، سجّل دخولك من جديد",
  invalid_credentials: "البريد أو كلمة المرور غير صحيحة",
  too_many_attempts: "محاولات كثيرة، حاول بعد ربع ساعة",
  email_or_slug_taken: "هذا البريد مسجّل من قبل",
  invalid_invitation: "رابط الدعوة غير صالح أو مستخدم أو منتهي",
  invalid_email: "البريد الإلكتروني غير صحيح",
  invalid_phone: "رقم الهاتف غير صحيح (مثال: 0912345678)",
  business_name_required: "اكتب اسم النشاط",
  signup_plan_not_configured: "التسجيل غير متاح حالياً، تواصل مع الدعم",
  invalid_reset_token: "الرابط غير صالح أو مستخدم أو منتهي",
  insufficient_role: "هذه العملية للمدير فقط",
  tenant_not_found: "لا تملك صلاحية على هذا النشاط",
  tenant_required: "اختر النشاط",
  origin_not_allowed: "الطلب مرفوض من هذا العنوان",
  conversation_not_found: "المحادثة غير موجودة",
  conversation_closed: "المحادثة مغلقة",
  channel_not_active: "القناة غير مفعّلة، أعد ربطها من صفحة القنوات",
  reply_window_closed: "مرت أكثر من 24 ساعة على آخر رسالة من الزبون؛ لا يمكن إرسال رسالة حرة",
  user_not_member: "الموظف ليس عضواً في النشاط",
  not_retryable: "لا يمكن إعادة إرسال هذه الرسالة",
  empty_message: "اكتب الرسالة",
  lead_not_found: "الطلب غير موجود",
  lost_reason_required: "اكتب سبب عدم الإتمام",
  invalid_status: "حالة غير صحيحة",
  staff_not_found: "الموظف غير موجود",
  invalid_assigned: "اختيار الموظف غير صحيح",
  invalid_cursor: "أعد تحميل الصفحة",
  only_owner_invites_admins: "المالك فقط يدعو مديرين",
  invitation_not_found: "الدعوة غير موجودة",
  channel_not_found: "القناة غير موجودة",
  realtime_unavailable: "التحديث اللحظي غير متاح",
};

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof TypeError) return "تعذّر الاتصال بالخادم، تأكد من الإنترنت";
  return "حدث خطأ غير متوقع";
}

let currentTenant: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setApiTenant(tenantId: string | null) {
  currentTenant = tenantId;
}

export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

export function tenantQuery(): string {
  return currentTenant ? `tenant=${encodeURIComponent(currentTenant)}` : "";
}

type Json = Record<string, unknown> | unknown[];

async function parseError(res: Response): Promise<ApiError> {
  let code = `http_${res.status}`;
  let message = "";
  try {
    const body = (await res.json()) as { detail?: unknown };
    const d = body.detail;
    if (d && typeof d === "object" && !Array.isArray(d)) {
      const obj = d as { error?: string; message?: string };
      code = obj.error ?? code;
      message = obj.message ?? "";
    } else if (Array.isArray(d)) {
      code = "validation_error";
      message = "تحقق من البيانات المدخلة";
    }
  } catch {
    // ليس JSON (مثلاً 502 من nginx)
  }
  if (!message) {
    message =
      MESSAGES[code] ??
      (res.status >= 500 ? "خطأ في الخادم، حاول بعد قليل" : "تعذّر تنفيذ الطلب");
  }
  return new ApiError(res.status, code, message);
}

export async function api<T = unknown>(
  path: string,
  init: { method?: string; body?: Json; tenant?: boolean; signal?: AbortSignal } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  if (init.tenant !== false && currentTenant) headers["X-Tenant-ID"] = currentTenant;
  const res = await fetch(path, {
    method: init.method ?? (init.body !== undefined ? "POST" : "GET"),
    headers,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    credentials: "same-origin",
    cache: "no-store",
    signal: init.signal,
  });
  if (!res.ok) {
    const err = await parseError(res);
    if (res.status === 401 && onUnauthorized && !path.startsWith("/api/v1/auth/")) onUnauthorized();
    throw err;
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function qs(params: Record<string, string | number | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}
