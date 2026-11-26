import type { ReactNode } from "react";
import { BrainCircuit, FileSearch, Gauge, ShieldCheck } from "lucide-react";

const FEATURES = [
  {
    icon: ShieldCheck,
    title: "Cách ly dữ liệu tuyệt đối",
    description: "RLS PostgreSQL theo tenant, kiểm tra Membership và Department ACL ở mọi truy vấn.",
  },
  {
    icon: FileSearch,
    title: "RAG có trích dẫn nguồn",
    description: "Hybrid Search FAISS mmap + FTS, hợp nhất RRF và nén ngữ cảnh 3 tầng trước khi gọi LLM.",
  },
  {
    icon: Gauge,
    title: "Hạn mức minh bạch",
    description: "Token bucket 30 RPM / 16.000 TPM theo từng Tenant, chặn sớm bằng HTTP 429 rõ ràng.",
  },
];

/** Khung xác thực hai cột: brand panel giàu ngữ cảnh + vùng form. */
export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-background lg:grid lg:grid-cols-[1.05fr_1fr]">
      <div className="relative hidden flex-col justify-between overflow-hidden bg-slate-950 p-10 text-white lg:flex">
        <div className="absolute inset-0 grid-pattern opacity-40" />
        <div className="absolute -right-24 top-1/4 h-96 w-96 rounded-full bg-indigo-600/25 blur-3xl" />
        <div className="absolute -bottom-24 -left-16 h-80 w-80 rounded-full bg-cyan-500/15 blur-3xl" />

        <div className="relative flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-cyan-400 shadow-lg shadow-indigo-950/50">
            <BrainCircuit className="h-5 w-5 text-white" />
          </div>
          <div>
            <p className="text-sm font-semibold tracking-tight">KnowledgeHub</p>
            <p className="text-[10px] uppercase tracking-[0.18em] text-slate-400">Enterprise AI Platform</p>
          </div>
        </div>

        <div className="relative max-w-lg space-y-8">
          <div className="space-y-3">
            <h1 className="text-balance text-3xl font-semibold leading-tight tracking-tight">
              Tri thức doanh nghiệp, được bảo vệ và trả lời có trích dẫn.
            </h1>
            <p className="text-sm leading-relaxed text-slate-300">
              Nền tảng trợ lý AI đa khách thuê cho tài liệu nội bộ: tải lên, tìm kiếm lai và hỏi đáp RAG
              theo thời gian thực — trong ngân sách 1.0 GB RAM của GCP e2-micro.
            </p>
          </div>
          <div className="space-y-4">
            {FEATURES.map((feature) => (
              <div key={feature.title} className="flex gap-3">
                <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/5 ring-1 ring-white/10">
                  <feature.icon className="h-4 w-4 text-indigo-300" />
                </div>
                <div className="space-y-0.5">
                  <p className="text-xs font-semibold text-white">{feature.title}</p>
                  <p className="max-w-sm text-[11px] leading-relaxed text-slate-400">{feature.description}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative flex items-center gap-4 text-[10px] text-slate-500">
          <span>Single PostgreSQL 16</span>
          <span className="h-1 w-1 rounded-full bg-slate-600" />
          <span>FAISS mmap per Tenant</span>
          <span className="h-1 w-1 rounded-full bg-slate-600" />
          <span>Fail-Closed Security</span>
        </div>
      </div>

      <div className="flex min-h-screen items-center justify-center px-5 py-10 sm:px-8">
        <div className="w-full max-w-md animate-fade-in">
          <div className="mb-6 flex items-center gap-2.5 lg:hidden">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-cyan-400">
              <BrainCircuit className="h-5 w-5 text-white" />
            </div>
            <div>
              <p className="text-sm font-semibold tracking-tight">KnowledgeHub</p>
              <p className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">Enterprise AI</p>
            </div>
          </div>
          {children}
        </div>
      </div>
    </div>
  );
}
