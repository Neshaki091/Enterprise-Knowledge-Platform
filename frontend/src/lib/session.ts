/** Phiên đăng nhập lưu trên client. Access token ngắn hạn; refresh token xoay vòng 7 ngày. */
export interface StoredSession {
  accessToken: string;
  refreshToken: string;
  tenantId: string;
  role: string;
  tokenType: string;
}

const SESSION_KEY = "kh.session.v1";

function isStoredSession(value: unknown): value is StoredSession {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.accessToken === "string" &&
    typeof candidate.refreshToken === "string" &&
    typeof candidate.tenantId === "string" &&
    typeof candidate.role === "string" &&
    typeof candidate.tokenType === "string"
  );
}

/** Đọc phiên đã lưu; dữ liệu hỏng hoặc không tương thích bị loại bỏ an toàn. */
export function readStoredSession(): StoredSession | null {
  try {
    const raw = window.localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isStoredSession(parsed)) {
      window.localStorage.removeItem(SESSION_KEY);
      return null;
    }
    return parsed;
  } catch {
    // localStorage bị chặn (chế độ riêng tư) hoặc JSON hỏng: coi như chưa có phiên.
    return null;
  }
}

/** Ghi hoặc xóa phiên; mất quyền ghi localStorage chỉ làm phiên ngắn hạn trong RAM. */
export function writeStoredSession(session: StoredSession | null): void {
  try {
    if (session) window.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    else window.localStorage.removeItem(SESSION_KEY);
  } catch {
    // Không thể báo lỗi lưu trữ cho người dùng; phiên hiện tại vẫn dùng được trong RAM.
  }
}
