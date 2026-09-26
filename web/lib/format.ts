import type { LeadStatus } from "./types";

// أرقام لاتينية (المعتاد في ليبيا) مع صيغ عربية للتواريخ
const TZ = "Africa/Tripoli";
const LOCALE = "ar-LY-u-nu-latn";

const timeFmt = new Intl.DateTimeFormat(LOCALE, { hour: "numeric", minute: "2-digit", timeZone: TZ });
const dayFmt = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "short", timeZone: TZ });
const fullFmt = new Intl.DateTimeFormat(LOCALE, {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZone: TZ,
});
const dateFmt = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "long", year: "numeric", timeZone: TZ });

function dayKey(d: Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(d);
}

export function shortTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  if (dayKey(d) === dayKey(now)) return timeFmt.format(d);
  const yesterday = new Date(now.getTime() - 86_400_000);
  if (dayKey(d) === dayKey(yesterday)) return "أمس";
  return dayFmt.format(d);
}

export function fullTime(iso: string | null | undefined): string {
  return iso ? fullFmt.format(new Date(iso)) : "";
}

export function clockTime(iso: string): string {
  return timeFmt.format(new Date(iso));
}

export function longDate(iso: string | null | undefined): string {
  return iso ? dateFmt.format(new Date(iso)) : "";
}

export function sameDay(a: string, b: string): boolean {
  return dayKey(new Date(a)) === dayKey(new Date(b));
}

export function dayLabel(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  if (dayKey(d) === dayKey(now)) return "اليوم";
  if (dayKey(d) === dayKey(new Date(now.getTime() - 86_400_000))) return "أمس";
  return dateFmt.format(d);
}

// +218912345678 => 091 234 5678 (الصيغة المحلية المألوفة)
export function localPhone(e164: string | null | undefined): string {
  if (!e164) return "";
  const m = /^\+218(\d{2})(\d{3})(\d{4})$/.exec(e164);
  return m ? `0${m[1]} ${m[2]} ${m[3]}` : e164;
}

export const CHANNEL_LABEL: Record<string, string> = {
  whatsapp: "واتساب",
  messenger: "ماسنجر",
  instagram: "إنستغرام",
  tiktok: "تيك توك",
  manual: "يدوي",
};

export const MODE_LABEL: Record<string, string> = {
  bot: "البوت",
  human: "موظف",
  closed: "مغلقة",
};

export const LEAD_STATUS: { value: LeadStatus; label: string; tone: Tone }[] = [
  { value: "new", label: "جديد", tone: "info" },
  { value: "contacted", label: "تم التواصل", tone: "neutral" },
  { value: "qualified", label: "مهتم جداً", tone: "warn" },
  { value: "booked", label: "تم الحجز", tone: "success" },
  { value: "lost", label: "لم يتم", tone: "danger" },
  { value: "spam", label: "غير جاد", tone: "muted" },
];

export const LEAD_STATUS_LABEL: Record<string, string> = Object.fromEntries(
  LEAD_STATUS.map((s) => [s.value, s.label]),
);

export const ROOM_LABEL: Record<string, string> = {
  quad: "رباعية",
  triple: "ثلاثية",
  double: "ثنائية",
  single: "مفردة",
  shared: "مشتركة",
  na: "—",
};

export const ROLE_LABEL: Record<string, string> = { owner: "المالك", admin: "مدير", agent: "موظف" };

export const CHANNEL_STATUS: Record<string, { label: string; tone: Tone }> = {
  active: { label: "مفعّلة", tone: "success" },
  paused: { label: "موقوفة مؤقتاً", tone: "warn" },
  needs_reauth: { label: "تحتاج إعادة ربط", tone: "danger" },
  disconnected: { label: "مفصولة", tone: "muted" },
};

export type Tone = "info" | "neutral" | "warn" | "success" | "danger" | "muted";

export function travelers(l: { adults: number | null; children: number | null; infants: number | null }): string {
  const parts: string[] = [];
  if (l.adults) parts.push(`${l.adults} بالغ`);
  if (l.children) parts.push(`${l.children} طفل`);
  if (l.infants) parts.push(`${l.infants} رضيع`);
  return parts.join("، ") || "—";
}

export function initials(name: string | null | undefined): string {
  const n = (name ?? "").trim();
  if (!n) return "؟";
  const parts = n.split(/\s+/);
  return parts.length > 1 ? parts[0][0] + parts[1][0] : n.slice(0, 2);
}
