import { expect, test } from "@playwright/test";

/**
 * E2E nghiệm thu Phase 2 trên SPA production (Nginx + FastAPI + PostgreSQL 16).
 *
 * Hành trình chính:
 *   đăng ký Tenant mới → tải tài liệu (Zero Raw Storage) → Hybrid Search
 *   → RAG SSE (chunks/citations phải xuất hiện trước token) → Tenant Admin analytics
 *   → RBAC chặn /admin/platform với OWNER.
 *
 * Mỗi lần chạy tạo một Tenant mới (slug duy nhất) để không phụ thuộc dữ liệu cũ.
 */

const RUN_ID = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
const TENANT_NAME = `E2E Nghiệm Thu ${RUN_ID}`;
const TENANT_SLUG = `e2e-${RUN_ID}`;
const OWNER_EMAIL = `e2e.${RUN_ID}@example.com`;
const OWNER_PASSWORD = "E2ePass#2026";
const DOC_TITLE = `e2e-chinh-sach-nghi-phep-${RUN_ID}`;

const POLICY_MARKDOWN = `# Chính sách nghỉ phép nội bộ

## Quy trình nghỉ phép của nhân viên

Nhân viên gửi đơn nghỉ phép trước ít nhất 2 ngày làm việc qua cổng thông tin nội bộ.
Quản lý phòng ban phê duyệt trong vòng 24 giờ. Phòng Nhân sự theo dõi số ngày phép còn lại.

## Chế độ bảo mật dữ liệu khách hàng

Mọi dữ liệu khách hàng phải được mã hóa khi lưu trữ và chỉ nhân sự được phân quyền mới có thể truy cập.
`;

test.describe("Phase 2 acceptance", () => {
  test("hành trình OWNER: đăng ký → tải tài liệu → tìm kiếm → RAG SSE → analytics → RBAC", async ({
    page,
  }) => {
    test.setTimeout(150_000);

    await test.step("Đăng ký Tenant mới và tự đăng nhập", async () => {
      await page.goto("/register");
      await expect(page.getByRole("heading", { name: "Tạo doanh nghiệp trên nền tảng" })).toBeVisible();

      await page.locator("#fullName").fill("E2E Owner");
      await page.locator("#register-email").fill(OWNER_EMAIL);
      await page.locator("#register-password").fill(OWNER_PASSWORD);
      await page.locator("#confirm-password").fill(OWNER_PASSWORD);
      await page.locator("#tenantName").fill(TENANT_NAME);
      await page.locator("#tenantSlug").fill(TENANT_SLUG);

      await page.getByRole("button", { name: "Đăng ký & tạo doanh nghiệp" }).click();
      await page.waitForURL("**/workspace", { timeout: 30_000 });
      await expect(page.getByRole("heading", { name: /Xin chào, E2E Owner/, level: 1 })).toBeVisible();
    });

    await test.step("Tải tài liệu Markdown với tiêu đề tùy chọn", async () => {
      await page.goto("/documents");
      await expect(page.getByRole("heading", { name: "Kho tài liệu", level: 1 })).toBeVisible();

      await page.getByRole("button", { name: "Tải tài liệu", exact: true }).click();
      await expect(page.getByText("Tải tài liệu lên kho tri thức")).toBeVisible();

      await page.locator("#document-file").setInputFiles({
        name: "chinh-sach-nghi-phep.md",
        mimeType: "text/markdown",
        buffer: Buffer.from(POLICY_MARKDOWN, "utf-8"),
      });
      await expect(page.getByText("Sẵn sàng bóc tách")).toBeVisible();
      await page.locator("#document-title").fill(DOC_TITLE);

      await page.getByRole("button", { name: "Nạp tài liệu" }).click();
      await expect(page.getByText(DOC_TITLE).first()).toBeVisible({ timeout: 30_000 });
      await expect(page.getByText("Đơn vị vector FAISS 786 chiều")).toBeVisible();
    });

    await test.step("Hybrid Search trả về phân đoạn của tài liệu vừa nạp", async () => {
      await page.goto("/search");
      await expect(page.getByRole("heading", { name: "Tìm kiếm lai", level: 1 })).toBeVisible();

      await page.getByLabel("Truy vấn tìm kiếm").fill("Quy trình nghỉ phép của nhân viên");
      await page.getByRole("button", { name: "Tìm kiếm", exact: true }).click();

      await expect(page.getByText(/phân đoạn cho/)).toBeVisible({ timeout: 20_000 });
      await expect(page.getByText(DOC_TITLE).first()).toBeVisible();
      await expect(page.getByText(/RRF k=60/)).toBeVisible();
    });

    await test.step("RAG SSE: chunks + citations đến trước token đầu tiên", async () => {
      await page.goto("/chat");
      await expect(page.getByRole("heading", { name: "Trợ lý tri thức", level: 1 })).toBeVisible();
      await page.locator("#chat-mode").selectOption("hybrid");

      const streamResponse = page.waitForResponse(
        (response) =>
          response.url().includes("/api/v1/chat/stream") &&
          response.request().method() === "POST",
        { timeout: 60_000 },
      );

      await page
        .getByPlaceholder("Đặt câu hỏi về tài liệu doanh nghiệp (2–2000 ký tự)…")
        .fill("Quy trình nghỉ phép của nhân viên gồm những bước nào?");
      await page.getByRole("button", { name: "Gửi" }).click();

      const response = await streamResponse;
      expect(response.status()).toBe(200);
      const body = await response.text();

      // Backend phát SSE dạng `data: {"type": "chunks" | "citations" | "token" | "done"}`.
      const chunksIndex = body.indexOf('"type": "chunks"');
      const citationsIndex = body.indexOf('"type": "citations"');
      const tokenIndex = body.indexOf('"type": "token"');
      const doneIndex = body.indexOf('"type": "done"');
      expect(chunksIndex, "SSE phải có event chunks").toBeGreaterThanOrEqual(0);
      expect(citationsIndex, "SSE phải có event citations").toBeGreaterThanOrEqual(0);
      expect(tokenIndex, "SSE phải có event token").toBeGreaterThanOrEqual(0);
      expect(doneIndex, "SSE phải có event done").toBeGreaterThanOrEqual(0);
      expect(chunksIndex, "chunks phải đến trước token").toBeLessThan(tokenIndex);
      expect(citationsIndex, "citations phải đến trước token").toBeLessThan(tokenIndex);
      expect(tokenIndex, "token phải đến trước done").toBeLessThan(doneIndex);

      await expect(page.getByText(/Ngữ cảnh đã truy xuất/)).toBeVisible();
      await expect(page.getByText("Trích dẫn", { exact: true })).toBeVisible();
    });

    await test.step("Tenant Admin Console hiển thị storage/analytics thật", async () => {
      await page.goto("/admin/tenant");
      await expect(page.getByRole("heading", { name: "Quản trị doanh nghiệp", level: 1 })).toBeVisible({
        timeout: 20_000,
      });
      await expect(page.getByText("Lưu trữ thật: PostgreSQL vs FAISS")).toBeVisible();
      await expect(page.getByText("Hạn ngạch Token Bucket")).toBeVisible();
      await expect(page.getByText("Nhật ký vận hành của doanh nghiệp")).toBeVisible();

      // Deep-link SPA + refresh vẫn phải render (Nginx try_files).
      await page.reload();
      await expect(page.getByRole("heading", { name: "Quản trị doanh nghiệp", level: 1 })).toBeVisible({
        timeout: 20_000,
      });
    });

    await test.step("RBAC: OWNER bị chặn khỏi Platform Super-Admin (UI + API 403)", async () => {
      await page.goto("/admin/platform");
      await expect(page).toHaveURL(/\/forbidden$/);
      await expect(page.getByText("403 Forbidden")).toBeVisible();
      await expect(page.getByText("Bạn không có quyền truy cập khu vực này")).toBeVisible();

      const apiStatus = await page.evaluate(async () => {
        const raw = window.localStorage.getItem("kh.session.v1");
        if (!raw) return -1;
        const session = JSON.parse(raw) as { accessToken: string; tenantId: string };
        const response = await fetch("/api/v1/dashboards/platform", {
          headers: {
            Authorization: `Bearer ${session.accessToken}`,
            "X-Tenant-ID": session.tenantId,
          },
        });
        return response.status;
      });
      expect(apiStatus, "Backend phải trả 403 cho OWNER").toBe(403);
    });
  });

  test("Nginx: SPA fallback, asset cache, SSE proxy và security headers", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Đăng nhập workspace" })).toBeVisible();

    const html = await page.request.get("/admin/platform");
    expect(html.status()).toBe(200);
    expect(html.headers()["content-type"]).toContain("text/html");
    expect(await html.text()).toContain('<div id="root">');

    const headers = html.headers();
    expect(headers["x-content-type-options"]).toBe("nosniff");
    expect(headers["x-frame-options"]).toBe("DENY");
    expect(headers["content-security-policy"]).toContain("default-src 'self'");
    expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");

    const assetPath = await page.evaluate(() => {
      const script = document.querySelector<HTMLScriptElement>('script[type="module"]');
      return script?.getAttribute("src") ?? "";
    });
    expect(assetPath).toMatch(/\/assets\/.+\.js$/);
    const asset = await page.request.get(assetPath);
    expect(asset.status()).toBe(200);
    expect(asset.headers()["cache-control"]).toContain("immutable");
  });
});
