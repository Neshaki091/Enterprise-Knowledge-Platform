import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { PublicOnly, RequireAdmin, RequireAuth, RequireSuperuser } from "@/features/auth/guards";
import { LoginPage } from "@/pages/LoginPage";
import { RegisterPage } from "@/pages/RegisterPage";
import { WorkspacePage } from "@/pages/WorkspacePage";
import { ChatPage } from "@/pages/ChatPage";
import { SearchPage } from "@/pages/SearchPage";
import { DocumentsPage } from "@/pages/DocumentsPage";
import { TeamPage } from "@/pages/TeamPage";
import { TenantAdminPage } from "@/pages/TenantAdminPage";
import { PlatformAdminPage } from "@/pages/PlatformAdminPage";
import { TenantsPage } from "@/pages/TenantsPage";
import { ForbiddenPage } from "@/pages/ForbiddenPage";
import { NotFoundPage } from "@/pages/NotFoundPage";

/** Bảng định tuyến SPA với 3 lớp bảo vệ: phiên đăng nhập → vai trò Tenant → Super-Admin. */
export default function App() {
  return (
    <Routes>
      <Route element={<PublicOnly />}>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
      </Route>

      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/workspace" replace />} />
          <Route path="/workspace" element={<WorkspacePage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/forbidden" element={<ForbiddenPage />} />

          <Route element={<RequireAdmin />}>
            <Route path="/team" element={<TeamPage />} />
            <Route path="/admin/tenant" element={<TenantAdminPage />} />
          </Route>

          <Route element={<RequireSuperuser />}>
            <Route path="/admin/platform" element={<PlatformAdminPage />} />
            <Route path="/admin/tenants" element={<TenantsPage />} />
          </Route>

          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
