import { createContext, useContext } from "react";
import type { RegisterPayload } from "@/lib/api";
import type { TenantContextResponse } from "@/lib/types";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

export interface AuthContextValue {
  status: AuthStatus;
  me: TenantContextResponse | null;
  isAdmin: boolean;
  isSuperuser: boolean;
  login: (email: string, password: string) => Promise<TenantContextResponse>;
  register: (payload: RegisterPayload) => Promise<void>;
  logout: () => Promise<void>;
  switchTenant: (tenantId: string) => Promise<void>;
  reloadMe: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

/** Truy cập trạng thái xác thực trong cây React; lỗi rõ ràng nếu thiếu Provider. */
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth phải được dùng bên trong <AuthProvider>");
  return context;
}
