import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Hợp nhất className của Tailwind, xử lý xung đột tiện ích trùng lặp. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Định dạng dung lượng byte sang chuỗi dễ đọc (KB/MB/GB). */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return "—";
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, exponent);
  return `${value.toFixed(value >= 100 || exponent === 0 ? 0 : 1)} ${units[exponent]}`;
}

/** Định dạng số nguyên với dấu phân cách hàng nghìn theo vi-VN. */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("vi-VN").format(value);
}

/** Định dạng tỷ lệ phần trăm một chữ số thập phân. */
export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(1)}%`;
}

/** Định dạng ngày giờ ISO sang chuỗi vi-VN ngắn gọn. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Định dạng mốc thời gian tương đối (vừa xong, x phút trước...). */
export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const diffSeconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (diffSeconds < 10) return "vừa xong";
  if (diffSeconds < 60) return `${diffSeconds} giây trước`;
  if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)} phút trước`;
  if (diffSeconds < 86400) return `${Math.floor(diffSeconds / 3600)} giờ trước`;
  return formatDateTime(iso);
}

/** Rút gọn UUID để hiển thị trong bảng. */
export function shortId(id: string | null | undefined, length = 8): string {
  if (!id) return "—";
  return id.slice(0, length);
}

/** Lấy chữ viết tắt từ họ tên/email để hiển thị avatar. */
export function initialsOf(nameOrEmail: string | null | undefined): string {
  if (!nameOrEmail) return "U";
  const base = nameOrEmail.split("@")[0] ?? "U";
  const parts = base.split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return "U";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
}

/** Sinh slug URL thân thiện từ tên tiếng Việt (bỏ dấu, chữ thường, gạch nối). */
export function slugify(input: string): string {
  return input
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);
}
