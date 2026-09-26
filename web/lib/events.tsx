"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { TenantEvent } from "./types";

export type LiveState = "connecting" | "live" | "offline";
type Listener = (e: TenantEvent) => void;

interface LiveCtx {
  state: LiveState;
  subscribe: (fn: Listener) => () => void;
}

const Ctx = createContext<LiveCtx | null>(null);
const EVENT_TYPES = ["message", "conversation", "lead", "message_status", "resync"] as const;

/**
 * اتصال SSE واحد للوحة كلها، والصفحات تشترك فيه.
 * المتصفح يعيد الاتصال تلقائياً؛ عند كل اتصال بعد الأول نرسل 'resync' لأن أحداثاً ربما ضاعت
 * أثناء الانقطاع (والخادم يغلق البث كل 30 دقيقة ليعيد فحص الجلسة).
 */
export function LiveProvider({ tenantId, children }: { tenantId: string; children: ReactNode }) {
  const listeners = useRef(new Set<Listener>());
  const [state, setState] = useState<LiveState>("connecting");

  useEffect(() => {
    if (typeof EventSource === "undefined") {
      setState("offline");
      return;
    }
    const emit = (e: TenantEvent) => {
      for (const fn of Array.from(listeners.current)) fn(e);
    };
    let first = true;
    const es = new EventSource(`/api/v1/events?tenant=${encodeURIComponent(tenantId)}`);
    es.addEventListener("ready", () => {
      setState("live");
      if (!first) emit({ type: "resync" });
      first = false;
    });
    const onData = (raw: Event) => {
      try {
        emit(JSON.parse((raw as MessageEvent<string>).data) as TenantEvent);
      } catch {
        // حدث غير مفهوم: نتجاهله
      }
    };
    for (const t of EVENT_TYPES) es.addEventListener(t, onData);
    es.onerror = () => setState(es.readyState === EventSource.CLOSED ? "offline" : "connecting");
    return () => es.close();
  }, [tenantId]);

  const subscribe = useCallback((fn: Listener) => {
    listeners.current.add(fn);
    return () => {
      listeners.current.delete(fn);
    };
  }, []);
  const value = useMemo<LiveCtx>(() => ({ state, subscribe }), [state, subscribe]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLiveState(): LiveState {
  return useContext(Ctx)?.state ?? "offline";
}

/** يشترك في الأحداث طوال عمر المكوّن. الـ handler الأحدث يُستخدم دائماً. */
export function useLiveEvents(handler: Listener): void {
  const ctx = useContext(Ctx);
  const ref = useRef(handler);
  ref.current = handler;
  const subscribe = ctx?.subscribe;
  useEffect(() => {
    if (!subscribe) return;
    return subscribe((e) => ref.current(e));
  }, [subscribe]);
}

/** يجمع عدة أحداث متقاربة في استدعاء واحد (5 رسائل وصلت معاً => تحديث واحد للقائمة). */
export function useDebounced(fn: () => void, ms: number): () => void {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  const trigger = useRef(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => fnRef.current(), ms);
  });
  return trigger.current;
}
