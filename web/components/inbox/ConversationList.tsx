"use client";

import { Avatar, Badge, EmptyState, ErrorState, SkeletonRows, Spinner } from "@/components/ui";
import { CHANNEL_LABEL, initials, localPhone, shortTime } from "@/lib/format";
import type { ConversationItem } from "@/lib/types";

export type View = "open" | "unread" | "human" | "bot" | "closed" | "all";
export type Assigned = "any" | "me" | "unassigned";

export interface ListFilters {
  view: View;
  assigned: Assigned;
  channel: string;
  q: string;
}

const VIEWS: { value: View; label: string }[] = [
  { value: "open", label: "المفتوحة" },
  { value: "unread", label: "غير مقروءة" },
  { value: "human", label: "مع موظف" },
  { value: "bot", label: "مع البوت" },
  { value: "closed", label: "المغلقة" },
];

export function ConversationList(props: {
  filters: ListFilters;
  onFilters: (f: ListFilters) => void;
  items: ConversationItem[];
  status: "loading" | "ready" | "error";
  error: string | null;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onRetry: () => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const { filters, onFilters, items, status } = props;
  const set = (patch: Partial<ListFilters>) => onFilters({ ...filters, ...patch });

  return (
    <section className="conv-list" aria-label="قائمة المحادثات">
      <div className="conv-filters">
        <input className="input" type="search" placeholder="ابحث بالاسم أو الرقم..." value={filters.q}
          onChange={(e) => set({ q: e.target.value })} aria-label="بحث" />
        <div className="tabs" role="tablist">
          {VIEWS.map((v) => (
            <button key={v.value} role="tab" aria-selected={filters.view === v.value}
              className={`tab${filters.view === v.value ? " active" : ""}`} onClick={() => set({ view: v.value })}>
              {v.label}
            </button>
          ))}
        </div>
        <div className="row">
          <select className="select" aria-label="الإسناد" value={filters.assigned}
            onChange={(e) => set({ assigned: e.target.value as Assigned })}>
            <option value="any">كل المحادثات</option>
            <option value="me">المسندة لي</option>
            <option value="unassigned">غير مسندة</option>
          </select>
          <select className="select" aria-label="القناة" value={filters.channel}
            onChange={(e) => set({ channel: e.target.value })}>
            <option value="">كل القنوات</option>
            <option value="whatsapp">واتساب</option>
            <option value="messenger">ماسنجر</option>
            <option value="instagram">إنستغرام</option>
          </select>
        </div>
      </div>

      <div className="conv-scroll">
        {status === "loading" && <SkeletonRows />}
        {status === "error" && <ErrorState message={props.error ?? ""} onRetry={props.onRetry} />}
        {status === "ready" && items.length === 0 && (
          <EmptyState
            title={filters.q ? "لا توجد نتائج" : "لا توجد محادثات هنا"}
            text={filters.q ? "جرّب اسماً أو رقماً آخر" : "ستظهر هنا رسائل الزبائن فور وصولها"}
          />
        )}
        {status === "ready" &&
          items.map((c) => {
            const name = c.contact_name || localPhone(c.contact_phone) || "زبون";
            return (
              <button key={c.id} className={`conv-item${c.id === props.selectedId ? " selected" : ""}${c.unread_count ? " unread" : ""}`}
                onClick={() => props.onSelect(c.id)} aria-current={c.id === props.selectedId}>
                <Avatar text={initials(name)} />
                <div className="grow">
                  <div className="row">
                    <span className="name grow truncate">{name}</span>
                    <span className="faint">{shortTime(c.last_message_at)}</span>
                  </div>
                  <div className="row">
                    <span className="preview grow truncate">
                      {c.last_message_direction === "outbound" ? "↩ " : ""}
                      {c.last_message_preview}
                    </span>
                    {c.unread_count > 0 && <span className="unread-count">{c.unread_count}</span>}
                  </div>
                  <div className="row" style={{ marginTop: 4, flexWrap: "wrap", gap: 4 }}>
                    <Badge tone="muted">{CHANNEL_LABEL[c.channel] ?? c.channel}</Badge>
                    {c.mode === "human" && <Badge tone="warn">مع موظف{c.assigned_name ? `: ${c.assigned_name}` : ""}</Badge>}
                    {c.mode === "closed" && <Badge tone="muted">مغلقة</Badge>}
                    {c.has_open_lead && <Badge tone="info">طلب حجز</Badge>}
                  </div>
                </div>
              </button>
            );
          })}
        {status === "ready" && props.hasMore && (
          <div style={{ padding: 12, textAlign: "center" }}>
            <button className="btn btn-sm" onClick={props.onLoadMore} disabled={props.loadingMore}>
              {props.loadingMore ? <Spinner /> : "تحميل المزيد"}
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
