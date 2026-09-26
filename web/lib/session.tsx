"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { api, setApiTenant, setUnauthorizedHandler } from "./api";
import type { Me, Role, TenantInfo } from "./types";

const TENANT_KEY = "dash.tenant";
const RANK: Record<Role, number> = { agent: 1, admin: 2, owner: 3 };

interface SessionValue {
  me: Me;
  tenant: TenantInfo;
  switchTenant: (tenantId: string) => void;
  atLeast: (role: Role) => boolean;
  logout: () => Promise<void>;
  reload: () => Promise<void>;
}

const Ctx = createContext<SessionValue | null>(null);

export function useSession(): SessionValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession outside SessionProvider");
  return v;
}

function readStoredTenant(): string | null {
  try {
    return window.localStorage.getItem(TENANT_KEY);
  } catch {
    return null;
  }
}

function storeTenant(id: string) {
  try {
    window.localStorage.setItem(TENANT_KEY, id);
  } catch {
    // وضع التصفح الخاص: نكتفي بالذاكرة
  }
}

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "no-tenant"; me: Me }
  | { kind: "ready"; me: Me; tenant: TenantInfo };

export function SessionProvider({
  children,
  loading,
  failed,
  noTenant,
}: {
  children: ReactNode;
  loading: ReactNode;
  failed: (message: string, retry: () => void) => ReactNode;
  noTenant: (me: Me) => ReactNode;
}) {
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(async () => {
    try {
      const me = await api<Me>("/api/v1/me", { tenant: false });
      if (me.tenants.length === 0) {
        setState({ kind: "no-tenant", me });
        return;
      }
      const stored = readStoredTenant();
      const tenant = me.tenants.find((t) => t.tenant_id === stored) ?? me.tenants[0];
      setApiTenant(tenant.tenant_id);
      setState({ kind: "ready", me, tenant });
    } catch (err) {
      const status = (err as { status?: number }).status;
      if (status === 401) {
        router.replace(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
        return;
      }
      setState({ kind: "error", message: err instanceof Error ? err.message : "تعذّر التحميل" });
    }
  }, [router]);

  useEffect(() => {
    setUnauthorizedHandler(() => router.replace("/login"));
    void load();
    return () => setUnauthorizedHandler(null);
  }, [load, router]);

  const value = useMemo<SessionValue | null>(() => {
    if (state.kind !== "ready") return null;
    return {
      me: state.me,
      tenant: state.tenant,
      switchTenant: (id: string) => {
        const t = state.me.tenants.find((x) => x.tenant_id === id);
        if (!t) return;
        storeTenant(id);
        setApiTenant(id);
        setState({ kind: "ready", me: state.me, tenant: t });
        router.push("/inbox");
      },
      atLeast: (role: Role) => RANK[state.tenant.role] >= RANK[role],
      logout: async () => {
        try {
          await api("/api/v1/auth/logout", { method: "POST", tenant: false });
        } finally {
          setApiTenant(null);
          router.replace("/login");
        }
      },
      reload: load,
    };
  }, [state, router, load]);

  if (state.kind === "loading") return <>{loading}</>;
  if (state.kind === "error") return <>{failed(state.message, () => void load())}</>;
  if (state.kind === "no-tenant") return <>{noTenant(state.me)}</>;
  // key: تغيير النشاط يعيد تركيب الصفحات => لا تختلط بيانات نشاطين
  return (
    <Ctx.Provider value={value} key={state.tenant.tenant_id}>
      {children}
    </Ctx.Provider>
  );
}
