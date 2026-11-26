import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AuthContext, type AuthContextValue, type AuthStatus } from "./auth-context";
import { authApi, type RegisterPayload } from "@/lib/api";
import {
  AUTH_EXPIRED_EVENT,
  ApiError,
  getSession,
  setSession,
} from "@/lib/api-client";
import type { TenantContextResponse, TokenResponse } from "@/lib/types";

/**
 * Giữ trạng thái phiên đăng nhập toàn cục.
 * - Khôi phục phiên từ refresh token đã lưu khi tải lại trang.
 * - Lắng nghe sự kiện hết hạn phiên từ tầng HTTP để đăng xuất tức thời.
 * - Xóa cache TanStack Query khi đổi tenant/đăng xuất để không rò dữ liệu chéo tenant.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [me, setMe] = useState<TenantContextResponse | null>(null);
  const queryClient = useQueryClient();

  const hydrate = useCallback(async (): Promise<void> => {
    if (!getSession()) {
      setStatus("unauthenticated");
      setMe(null);
      return;
    }
    try {
      const context = await authApi.me();
      setMe(context);
      setStatus("authenticated");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setSession(null);
      }
      setMe(null);
      setStatus("unauthenticated");
    }
  }, []);

  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  useEffect(() => {
    const handleExpired = (): void => {
      setStatus("unauthenticated");
      setMe(null);
      queryClient.clear();
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, handleExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleExpired);
  }, [queryClient]);

  const applyTokens = useCallback((tokens: TokenResponse): void => {
    setSession({
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
      tenantId: tokens.tenant_id,
      role: tokens.role,
      tokenType: tokens.token_type,
    });
  }, []);

  const login = useCallback(
    async (email: string, password: string): Promise<TenantContextResponse> => {
      const tokens = await authApi.login(email, password);
      applyTokens(tokens);
      const context = await authApi.me();
      setMe(context);
      setStatus("authenticated");
      return context;
    },
    [applyTokens],
  );

  const register = useCallback(
    async (payload: RegisterPayload): Promise<void> => {
      await authApi.register(payload);
      await login(payload.email, payload.password);
    },
    [login],
  );

  const logout = useCallback(async (): Promise<void> => {
    const session = getSession();
    try {
      await authApi.logout(session?.refreshToken ?? null);
    } catch {
      // Token có thể đã bị thu hồi hoặc mất mạng; phiên cục bộ vẫn phải kết thúc.
    }
    setSession(null);
    queryClient.clear();
    setMe(null);
    setStatus("unauthenticated");
  }, [queryClient]);

  const switchTenant = useCallback(
    async (tenantId: string): Promise<void> => {
      const tokens = await authApi.switchTenant(tenantId);
      applyTokens(tokens);
      const context = await authApi.me();
      setMe(context);
      setStatus("authenticated");
      queryClient.clear();
    },
    [applyTokens, queryClient],
  );

  const reloadMe = useCallback(async (): Promise<void> => {
    const context = await authApi.me();
    setMe(context);
  }, []);

  const value = useMemo<AuthContextValue>(() => {
    const role = me?.role.toUpperCase() ?? "";
    return {
      status,
      me,
      isAdmin: Boolean(me?.is_superuser) || role === "OWNER" || role === "ADMIN",
      isSuperuser: Boolean(me?.is_superuser),
      login,
      register,
      logout,
      switchTenant,
      reloadMe,
    };
  }, [status, me, login, register, logout, switchTenant, reloadMe]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
