"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { ConversationList, type ListFilters } from "@/components/inbox/ConversationList";
import { Thread } from "@/components/inbox/Thread";
import { EmptyState, LoadingState } from "@/components/ui";
import { api, errorMessage, qs } from "@/lib/api";
import { useDebounced, useLiveEvents } from "@/lib/events";
import type { ConversationItem, Page } from "@/lib/types";

const PAGE_SIZE = 30;
const MAX_LIMIT = 100; // حد الـ API لطلب واحد
const DEFAULT_FILTERS: ListFilters = { view: "open", assigned: "any", channel: "", q: "" };

type Status = "loading" | "ready" | "error";

function InboxView() {
  const router = useRouter();
  const params = useSearchParams();
  const selectedId = params.get("c");

  const [filters, setFilters] = useState<ListFilters>(DEFAULT_FILTERS);
  const [q, setQ] = useState(""); // نص البحث بعد التأخير (لا طلب مع كل حرف)
  const [items, setItems] = useState<ConversationItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const seq = useRef(0); // آخر طلب فقط يُعتمد (تغيير الفلتر أثناء طلب سابق)
  const loaded = useRef(0);
  loaded.current = items.length;
  const openedHere = useRef(false);

  const query = useCallback(
    (extra: { cursor?: string | null; limit: number }) =>
      `/api/v1/conversations${qs({
        view: filters.view,
        assigned: filters.assigned,
        channel: filters.channel,
        q,
        cursor: extra.cursor,
        limit: extra.limit,
      })}`,
    [filters.view, filters.assigned, filters.channel, q],
  );

  // أول صفحة عند تغيير الفلاتر. refresh=true => تحديث صامت يحافظ على عدد العناصر المحمّلة
  const load = useCallback(
    async (refresh = false) => {
      const id = ++seq.current;
      if (!refresh) setStatus("loading");
      const limit = refresh ? Math.min(MAX_LIMIT, Math.max(PAGE_SIZE, loaded.current)) : PAGE_SIZE;
      try {
        const page = await api<Page<ConversationItem>>(query({ limit }));
        if (id !== seq.current) return;
        setItems(page.items);
        setCursor(page.next_cursor ?? null);
        setStatus("ready");
        setError(null);
      } catch (err) {
        if (id !== seq.current) return;
        // تحديث صامت فشل: نُبقي القائمة الحالية (الانقطاع يظهر في مؤشر الاتصال)
        if (!refresh) {
          setError(errorMessage(err));
          setStatus("error");
        }
      }
    },
    [query],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const t = setTimeout(() => setQ(filters.q.trim()), 350);
    return () => clearTimeout(t);
  }, [filters.q]);

  const refreshSoon = useDebounced(() => void load(true), 400);
  useLiveEvents((e) => {
    if (e.type === "message" || e.type === "conversation" || e.type === "lead" || e.type === "resync") {
      refreshSoon();
    }
  });

  async function loadMore() {
    if (!cursor) return;
    const id = seq.current;
    setLoadingMore(true);
    try {
      const page = await api<Page<ConversationItem>>(query({ cursor, limit: PAGE_SIZE }));
      if (id !== seq.current) return;
      setItems((cur) => {
        const seen = new Set(cur.map((c) => c.id));
        return [...cur, ...page.items.filter((c) => !seen.has(c.id))];
      });
      setCursor(page.next_cursor ?? null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoadingMore(false);
    }
  }

  function select(id: string) {
    // الصفر فوراً في القائمة؛ الخادم يُحدَّث من صفحة المحادثة ويصل الحدث لاحقاً
    setItems((cur) => cur.map((c) => (c.id === id ? { ...c, unread_count: 0 } : c)));
    openedHere.current = true;
    router.push(`/inbox?c=${encodeURIComponent(id)}`, { scroll: false });
  }

  function back() {
    // على الجوال: الرجوع للقائمة. إن فُتحت المحادثة من رابط مباشر لا نخرج من اللوحة
    if (openedHere.current) {
      openedHere.current = false;
      router.back();
    } else {
      router.replace("/inbox", { scroll: false });
    }
  }

  return (
    <div className={`inbox${selectedId ? " has-selection" : ""}`}>
      <ConversationList
        filters={filters}
        onFilters={setFilters}
        items={items}
        status={status}
        error={error}
        hasMore={cursor !== null}
        loadingMore={loadingMore}
        onLoadMore={() => void loadMore()}
        onRetry={() => void load()}
        selectedId={selectedId}
        onSelect={select}
      />
      {selectedId ? (
        <Thread key={selectedId} conversationId={selectedId} onBack={back} />
      ) : (
        <section className="thread">
          <EmptyState title="اختر محادثة" text="اختر محادثة من القائمة لعرض الرسائل والرد على الزبون." />
        </section>
      )}
    </div>
  );
}

export default function InboxPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <InboxView />
    </Suspense>
  );
}
