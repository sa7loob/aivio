"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { LeadDrawer } from "@/components/leads/LeadDrawer";
import { Alert, Badge, EmptyState, ErrorState, SkeletonRows, Spinner } from "@/components/ui";
import { api, errorMessage, qs } from "@/lib/api";
import { useDebounced, useLiveEvents } from "@/lib/events";
import { CHANNEL_LABEL, LEAD_STATUS, LEAD_STATUS_LABEL, fullTime, localPhone, shortTime, travelers } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Lead, Page, Staff } from "@/lib/types";

const PAGE_SIZE = 30;
const MAX_LIMIT = 100;

interface Filters {
  status: string;
  assigned: string; // any | unassigned | staff_users.id
  q: string;
  from_date: string;
  to_date: string;
}

const EMPTY: Filters = { status: "", assigned: "any", q: "", from_date: "", to_date: "" };

function tone(status: string) {
  return LEAD_STATUS.find((s) => s.value === status)?.tone ?? "neutral";
}

export default function LeadsPage() {
  const { atLeast, tenant } = useSession();
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Lead[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [staff, setStaff] = useState<Staff[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);

  const seq = useRef(0);
  const loaded = useRef(0);
  loaded.current = items.length;

  const params = useCallback(
    () => ({
      status: filters.status,
      assigned: filters.assigned === "any" ? null : filters.assigned,
      q,
      from_date: filters.from_date,
      to_date: filters.to_date,
    }),
    [filters.status, filters.assigned, filters.from_date, filters.to_date, q],
  );

  const load = useCallback(
    async (refresh = false) => {
      const id = ++seq.current;
      if (!refresh) setStatus("loading");
      const limit = refresh ? Math.min(MAX_LIMIT, Math.max(PAGE_SIZE, loaded.current)) : PAGE_SIZE;
      try {
        const page = await api<Page<Lead>>(`/api/v1/leads${qs({ ...params(), limit })}`);
        if (id !== seq.current) return;
        setItems(page.items);
        setCursor(page.next_cursor ?? null);
        setStatus("ready");
        setMoreError(null);
      } catch (err) {
        if (id !== seq.current || refresh) return;
        setError(errorMessage(err));
        setStatus("error");
      }
    },
    [params],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const t = setTimeout(() => setQ(filters.q.trim()), 350);
    return () => clearTimeout(t);
  }, [filters.q]);

  useEffect(() => {
    api<Staff[]>("/api/v1/team/staff")
      .then(setStaff)
      .catch(() => setStaff([]));
  }, []);

  const refreshSoon = useDebounced(() => void load(true), 500);
  useLiveEvents((e) => {
    if (e.type === "lead" || e.type === "resync") refreshSoon();
  });

  async function loadMore() {
    if (!cursor) return;
    const id = seq.current;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await api<Page<Lead>>(`/api/v1/leads${qs({ ...params(), cursor, limit: PAGE_SIZE })}`);
      if (id !== seq.current) return;
      setItems((cur) => {
        const seen = new Set(cur.map((l) => l.id));
        return [...cur, ...page.items.filter((l) => !seen.has(l.id))];
      });
      setCursor(page.next_cursor ?? null);
    } catch (err) {
      setMoreError(errorMessage(err));
    } finally {
      setLoadingMore(false);
    }
  }

  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }));
  const filtered = JSON.stringify(filters) !== JSON.stringify(EMPTY);
  // التصدير رابط GET عادي (تنزيل ملف)؛ الوكالة في ?tenant= لأن الرابط لا يحمل هيدرات
  const exportHref = `/api/v1/leads/export.csv${qs({ ...params(), tenant: tenant.tenant_id })}`;

  return (
    <div className="page">
      <div className="page-head">
        <h1 className="grow">طلبات الحجز</h1>
        {atLeast("admin") && (
          <a className="btn btn-sm" href={exportHref} download>
            تصدير Excel (CSV)
          </a>
        )}
      </div>

      <div className="filters">
        <input className="input" type="search" placeholder="ابحث بالاسم أو الرقم..." aria-label="بحث"
          value={filters.q} onChange={(e) => set({ q: e.target.value })} />
        <select className="select" aria-label="الحالة" value={filters.status} onChange={(e) => set({ status: e.target.value })}>
          <option value="">كل الحالات</option>
          {LEAD_STATUS.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
        <select className="select" aria-label="الموظف" value={filters.assigned}
          onChange={(e) => set({ assigned: e.target.value })}>
          <option value="any">كل الموظفين</option>
          <option value="unassigned">غير مسندة</option>
          {staff.map((s) => (
            <option key={s.id} value={s.id}>{s.full_name}{s.is_active ? "" : " (غير نشط)"}</option>
          ))}
        </select>
        <label className="date-field">
          <span className="faint">من</span>
          <input className="input ltr" type="date" value={filters.from_date} max={filters.to_date || undefined}
            onChange={(e) => set({ from_date: e.target.value })} aria-label="من تاريخ" />
        </label>
        <label className="date-field">
          <span className="faint">إلى</span>
          <input className="input ltr" type="date" value={filters.to_date} min={filters.from_date || undefined}
            onChange={(e) => set({ to_date: e.target.value })} aria-label="إلى تاريخ" />
        </label>
        {filtered && (
          <button className="btn btn-sm btn-ghost" onClick={() => setFilters(EMPTY)}>مسح الفلاتر</button>
        )}
      </div>

      <div className="card">
        {status === "loading" && <SkeletonRows />}
        {status === "error" && <ErrorState message={error ?? ""} onRetry={() => void load()} />}
        {status === "ready" && items.length === 0 && (
          filtered ? (
            <EmptyState title="لا توجد طلبات مطابقة" text="غيّر الفلاتر أو امسحها لعرض كل الطلبات"
              action={<button className="btn btn-sm" onClick={() => setFilters(EMPTY)}>مسح الفلاتر</button>} />
          ) : (
            <EmptyState title="لا توجد طلبات حجز بعد"
              text="عندما يجمع البوت بيانات زبون يريد الحجز، يظهر الطلب هنا فوراً لتتابعه." />
          )
        )}
        {status === "ready" && items.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>الزبون</th>
                  <th>الحالة</th>
                  <th className="hide-sm">البرنامج / الفترة</th>
                  <th className="hide-sm">المسافرون</th>
                  <th className="hide-sm">الموظف</th>
                  <th className="hide-sm">القناة</th>
                  <th>التاريخ</th>
                </tr>
              </thead>
              <tbody>
                {items.map((l) => (
                  <tr key={l.id} onClick={() => setOpenId(l.id)} tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setOpenId(l.id);
                      }
                    }}
                    aria-selected={l.id === openId}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{l.full_name || "—"}</div>
                      {l.phone_e164 && <div className="faint ltr">{localPhone(l.phone_e164)}</div>}
                    </td>
                    <td><Badge tone={tone(l.status)}>{LEAD_STATUS_LABEL[l.status] ?? l.status}</Badge></td>
                    <td className="hide-sm">
                      <div>{l.package_title || "—"}</div>
                      {l.preferred_period && <div className="faint">{l.preferred_period}</div>}
                    </td>
                    <td className="hide-sm">{travelers(l)}</td>
                    <td className="hide-sm">{l.assigned_name || <span className="faint">غير مسند</span>}</td>
                    <td className="hide-sm">{CHANNEL_LABEL[l.source_channel ?? ""] ?? l.source_channel ?? "—"}</td>
                    <td title={fullTime(l.created_at)} style={{ whiteSpace: "nowrap" }}>{shortTime(l.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {status === "ready" && cursor && (
          <div style={{ padding: 12, textAlign: "center" }}>
            {moreError && <Alert>{moreError}</Alert>}
            <button className="btn btn-sm" onClick={() => void loadMore()} disabled={loadingMore}>
              {loadingMore ? <Spinner /> : "تحميل المزيد"}
            </button>
          </div>
        )}
      </div>

      {openId && (
        <LeadDrawer leadId={openId} staff={staff} onClose={() => setOpenId(null)}
          onChanged={(lead) => setItems((cur) => cur.map((l) => (l.id === lead.id ? { ...l, ...lead } : l)))} />
      )}
    </div>
  );
}
