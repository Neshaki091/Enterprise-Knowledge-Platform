import {
  BarChart3,
  Building2,
  FileStack,
  LayoutDashboard,
  MessageSquareText,
  Search,
  ShieldCheck,
  Users,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  to: string;
  icon: LucideIcon;
  end?: boolean;
  adminOnly?: boolean;
  superuserOnly?: boolean;
}

export interface NavSection {
  title: string;
  items: NavItem[];
}

export const NAV_SECTIONS: NavSection[] = [
  {
    title: "Không gian làm việc",
    items: [
      { label: "Tổng quan", to: "/workspace", icon: LayoutDashboard },
      { label: "Trợ lý tri thức", to: "/chat", icon: MessageSquareText },
      { label: "Tìm kiếm lai", to: "/search", icon: Search },
      { label: "Kho tài liệu", to: "/documents", icon: FileStack },
    ],
  },
  {
    title: "Quản trị doanh nghiệp",
    items: [
      { label: "Phòng ban & Nhân sự", to: "/team", icon: Users, adminOnly: true },
      { label: "Quản trị doanh nghiệp", to: "/admin/tenant", icon: BarChart3, adminOnly: true },
    ],
  },
  {
    title: "Nền tảng",
    items: [
      { label: "Tổng quan nền tảng", to: "/admin/platform", icon: ShieldCheck, superuserOnly: true },
      { label: "Quản lý Tenants", to: "/admin/tenants", icon: Building2, superuserOnly: true },
    ],
  },
];

const ALL_ITEMS: NavItem[] = NAV_SECTIONS.flatMap((section) => section.items);

/** Xác định tiêu đề trang từ đường dẫn hiện tại (khớp tiền tố dài nhất). */
export function pageTitleFor(pathname: string): string {
  const matches = ALL_ITEMS.filter(
    (item) => pathname === item.to || pathname.startsWith(`${item.to}/`),
  ).sort((a, b) => b.to.length - a.to.length);
  return matches[0]?.label ?? "KnowledgeHub";
}
