import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Copy,
  Database,
  Eraser,
  Eye,
  EyeOff,
  KeyRound,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
  Terminal,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { mcpApi } from "@/lib/api";
import { describeApiError } from "@/lib/api-client";
import { formatDateTime } from "@/lib/utils";
import type {
  ExternalDBConnectorResponse,
  ExternalDBQueryResult,
  ExternalDBTokenResponse,
  ExternalDbType,
  McpToolDefinition,
} from "@/lib/types";
import { PageHeader } from "@/components/layout/page-header";
import { StatCard } from "@/components/layout/stat-card";
import { Alert } from "@/components/feedback/alert";
import { EmptyState } from "@/components/feedback/empty-state";
import { Spinner } from "@/components/feedback/spinner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

function resolveMcpHttpUrl(): string {
  const apiBase = (import.meta.env.VITE_API_BASE_URL ?? "").trim().replace(/\/+$/, "");
  if (apiBase) return `${apiBase}/api/v1/mcp`;
  if (typeof window !== "undefined") return `${window.location.origin}/api/v1/mcp`;
  return "/api/v1/mcp";
}

function parseTableList(raw: string): string[] {
  return raw
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

/** Trang Quản lý Kết nối MCP & DB riêng Zero-Knowledge (chỉ Tenant Admin/Owner). */
export function McpConnectorsPage() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState("connectors");

  // Modal tạo connector mới
  const [createOpen, setCreateOpen] = useState(false);
  const [connName, setConnName] = useState("");
  const [dbType, setDbType] = useState<ExternalDbType>("postgresql");
  const [connectionUri, setConnectionUri] = useState("");
  const [createSecret, setCreateSecret] = useState("");
  const [allowedTablesRaw, setAllowedTablesRaw] = useState("orders, customers");
  const [showUri, setShowUri] = useState(false);

  // Modal cấp DB JWT
  const [tokenTarget, setTokenTarget] = useState<ExternalDBConnectorResponse | null>(null);
  const [tokenSecret, setTokenSecret] = useState("");
  const [expiresMinutes, setExpiresMinutes] = useState("60");
  const [issuedToken, setIssuedToken] = useState<ExternalDBTokenResponse | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  // Modal xác nhận xóa
  const [deleteTarget, setDeleteTarget] = useState<ExternalDBConnectorResponse | null>(null);

  // Playground chạy thử query_enterprise_db (chỉ giữ trong RAM state của tab)
  const [testConnectorId, setTestConnectorId] = useState("");
  const [testDbJwt, setTestDbJwt] = useState("");
  const [testSecret, setTestSecret] = useState("");
  const [testSql, setTestSql] = useState("SELECT * FROM orders LIMIT 10");
  const [queryResult, setQueryResult] = useState<ExternalDBQueryResult | null>(null);
  const [queryError, setQueryError] = useState<string | null>(null);

  const connectorsQuery = useQuery({
    queryKey: ["mcp", "external-dbs"],
    queryFn: mcpApi.listExternalDbs,
  });

  const toolsQuery = useQuery({
    queryKey: ["mcp", "tools-list"],
    queryFn: async () => {
      const res = await mcpApi.rpc<{ tools?: McpToolDefinition[] }>("tools/list");
      if (res.error) throw new Error(res.error.message);
      return res.result?.tools ?? [];
    },
  });

  const createMutation = useMutation({
    mutationFn: mcpApi.createExternalDb,
    onSuccess: async (created) => {
      toast.success(`Đã mã hóa và lưu kết nối "${created.name}"`);
      setCreateOpen(false);
      setConnName("");
      setConnectionUri("");
      setCreateSecret("");
      setAllowedTablesRaw("orders, customers");
      setTestConnectorId(created.id);
      await queryClient.invalidateQueries({ queryKey: ["mcp", "external-dbs"] });
    },
    onError: (err) => toast.error(describeApiError(err, "Không thể tạo kết nối DB")),
  });

  const issueTokenMutation = useMutation({
    mutationFn: mcpApi.issueDbToken,
    onSuccess: (res) => {
      setIssuedToken(res);
      toast.success("Đã cấp phát DB JWT Read-Only thành công");
    },
    onError: (err) => toast.error(describeApiError(err, "Không thể cấp DB JWT")),
  });

  const deleteMutation = useMutation({
    mutationFn: mcpApi.deleteExternalDb,
    onSuccess: async () => {
      toast.success("Đã xóa kết nối DB khỏi Tenant");
      if (deleteTarget && deleteTarget.id === testConnectorId) {
        setTestConnectorId("");
      }
      setDeleteTarget(null);
      await queryClient.invalidateQueries({ queryKey: ["mcp", "external-dbs"] });
    },
    onError: (err) => toast.error(describeApiError(err, "Không thể xóa kết nối DB")),
  });

  const runSqlMutation = useMutation({
    mutationFn: async () => {
      const res = await mcpApi.rpc<{ content?: Array<{ type: string; text: string }>; isError?: boolean }>(
        "tools/call",
        {
          name: "query_enterprise_db",
          arguments: {
            connector_id: testConnectorId,
            db_jwt: testDbJwt.trim(),
            tenant_secret: testSecret,
            sql_query: testSql.trim(),
          },
        },
      );
      if (res.error) throw new Error(res.error.message);
      const textPayload = res.result?.content?.[0]?.text ?? "";
      if (res.result?.isError) {
        throw new Error(textPayload || "Truy vấn bị từ chối bởi lớp bảo vệ SQL Read-Only");
      }
      return JSON.parse(textPayload) as ExternalDBQueryResult;
    },
    onSuccess: (data) => {
      setQueryError(null);
      setQueryResult(data);
      toast.success(`Truy vấn thành công (${data.row_count} dòng)`);
    },
    onError: (err) => {
      setQueryResult(null);
      const msg = describeApiError(err, "Không thể thực thi truy vấn MCP");
      setQueryError(msg);
    },
  });

  const connectors = connectorsQuery.data ?? [];
  const tools = toolsQuery.data ?? [];
  const activeCount = useMemo(() => connectors.filter((c) => c.is_active).length, [connectors]);
  const mcpUrl = resolveMcpHttpUrl();

  const mcpClientSnippet = useMemo(
    () =>
      JSON.stringify(
        {
          mcpServers: {
            "enterprise-knowledge": {
              transport: "http",
              url: mcpUrl,
              headers: {
                Authorization: "Bearer <ACCESS_TOKEN_CUA_DOANH_NGHIEP>",
              },
            },
          },
        },
        null,
        2,
      ),
    [mcpUrl],
  );

  const copyText = async (label: string, textValue: string): Promise<void> => {
    try {
      await navigator.clipboard.writeText(textValue);
      setCopiedKey(label);
      toast.success("Đã sao chép vào clipboard");
      window.setTimeout(() => setCopiedKey((prev) => (prev === label ? null : prev)), 2000);
    } catch {
      toast.error("Trình duyệt không hỗ trợ sao chép tự động");
    }
  };

  const handleCreateSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const tables = parseTableList(allowedTablesRaw);
    if (tables.length === 0) {
      toast.error("Vui lòng nhập ít nhất 1 tên bảng hợp lệ trong allowed_tables");
      return;
    }
    createMutation.mutate({
      name: connName.trim(),
      db_type: dbType,
      connection_uri: connectionUri.trim(),
      tenant_secret: createSecret,
      allowed_tables: tables,
    });
  };

  const handleIssueToken = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!tokenTarget) return;
    issueTokenMutation.mutate({
      connector_id: tokenTarget.id,
      tenant_secret: tokenSecret,
      expires_minutes: Number(expiresMinutes) || 60,
    });
  };

  const openTesterWithConnector = (connector: ExternalDBConnectorResponse): void => {
    setTestConnectorId(connector.id);
    if (connector.allowed_tables.length > 0) {
      setTestSql(`SELECT * FROM ${connector.allowed_tables[0]} LIMIT 10`);
    }
    setActiveTab("mcp");
  };

  const resultColumns = useMemo(() => {
    if (!queryResult || queryResult.rows.length === 0) return [];
    return Object.keys(queryResult.rows[0]);
  }, [queryResult]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Kết nối MCP & DB riêng"
        description="Cấu hình kết nối CSDL riêng của doanh nghiệp theo chuẩn Zero-Knowledge (AES-256-GCM + DB JWT Read-Only) và kiểm thử trực tiếp cổng Model Context Protocol."
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void connectorsQuery.refetch();
                void toolsQuery.refetch();
              }}
            >
              <RefreshCw className={connectorsQuery.isFetching ? "animate-spin" : undefined} /> Làm mới
            </Button>
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <Plus /> Thêm kết nối DB riêng
            </Button>
          </>
        }
      />

      <Alert variant="info" title="Bảo mật Zero-Knowledge: Chủ nền tảng cũng không thể đọc DB của bạn">
        Chuỗi kết nối (DSN) được mã hóa bằng <strong>AES-256-GCM</strong> với khóa dẫn xuất từ{" "}
        <code>tenant_secret</code> do riêng doanh nghiệp giữ — máy chủ không bao giờ lưu{" "}
        <code>tenant_secret</code>. Mọi truy vấn qua MCP bắt buộc kèm <code>db_jwt</code> Read-Only và chỉ
        chạy <code>SELECT</code> trên danh sách bảng <code>allowed_tables</code> (tối đa 50 dòng).
      </Alert>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          label="Kết nối DB riêng"
          value={connectors.length}
          hint={`${activeCount} kết nối đang hoạt động`}
          icon={Database}
        />
        <StatCard
          label="MCP Tools khả dụng"
          value={tools.length}
          hint="JSON-RPC 2.0 (2024-11-05)"
          icon={Terminal}
          tone="indigo"
        />
        <StatCard
          label="Chế độ truy vấn DB ngoài"
          value="Read-Only"
          hint="SELECT whitelist · LIMIT ≤ 50"
          icon={ShieldCheck}
          tone="emerald"
        />
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="connectors">Kết nối DB riêng ({connectors.length})</TabsTrigger>
          <TabsTrigger value="mcp">Cổng MCP & Truy vấn thử</TabsTrigger>
        </TabsList>

        <TabsContent value="connectors">
          <Card>
            <CardHeader>
              <CardTitle>Danh sách kết nối CSDL đã mã hóa</CardTitle>
              <CardDescription>
                Chuỗi kết nối gốc bị che hoàn toàn. Chỉ Admin/Owner của doanh nghiệp mới có quyền thêm, xóa hoặc
                xin cấp DB JWT.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {connectorsQuery.isError ? (
                <Alert variant="destructive" title="Không tải được danh sách kết nối DB">
                  {describeApiError(connectorsQuery.error, "Vui lòng thử lại sau.")}
                </Alert>
              ) : connectorsQuery.isLoading ? (
                <div className="space-y-2">
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                </div>
              ) : connectors.length === 0 ? (
                <EmptyState
                  icon={Database}
                  title="Chưa có kết nối DB riêng nào"
                  description="Thêm PostgreSQL hoặc SQLite Read-Only của doanh nghiệp để AI Agent tra cứu số liệu trực tiếp qua MCP."
                  action={
                    <Button size="sm" onClick={() => setCreateOpen(true)}>
                      <Plus /> Thêm kết nối DB riêng
                    </Button>
                  }
                />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Tên kết nối</TableHead>
                      <TableHead>Loại DB</TableHead>
                      <TableHead>Host (đã che)</TableHead>
                      <TableHead>Bảng cho phép</TableHead>
                      <TableHead>Ngày tạo</TableHead>
                      <TableHead className="text-right">Thao tác</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {connectors.map((conn) => (
                      <TableRow key={conn.id}>
                        <TableCell className="font-medium">
                          <div className="flex items-center gap-2">
                            <span>{conn.name}</span>
                            <Badge variant={conn.is_active ? "success" : "secondary"}>
                              {conn.is_active ? "Hoạt động" : "Tạm tắt"}
                            </Badge>
                          </div>
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline" className="uppercase">
                            {conn.db_type}
                          </Badge>
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">
                          {conn.host_masked}
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap gap-1">
                            {conn.allowed_tables.map((tbl) => (
                              <Badge key={tbl} variant="info" className="font-mono">
                                {tbl}
                              </Badge>
                            ))}
                          </div>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {formatDateTime(conn.created_at)}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-1.5">
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => {
                                setTokenTarget(conn);
                                setIssuedToken(null);
                                setTokenSecret("");
                              }}
                            >
                              <KeyRound /> Cấp DB JWT
                            </Button>
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => openTesterWithConnector(conn)}
                            >
                              <Play /> Truy vấn thử
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="text-destructive hover:text-destructive"
                              onClick={() => setDeleteTarget(conn)}
                              aria-label={`Xóa kết nối ${conn.name}`}
                            >
                              <Trash2 />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="mcp" className="space-y-6">
          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Thông tin kết nối MCP Client</CardTitle>
                <CardDescription>
                  Dùng cho Claude Desktop, Cursor hoặc AI Agent nội bộ kết nối qua HTTP JSON-RPC 2.0 / SSE.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-1.5">
                  <Label>HTTP JSON-RPC Endpoint</Label>
                  <div className="flex gap-2">
                    <Input readOnly value={mcpUrl} className="font-mono text-xs" />
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void copyText("mcp-url", mcpUrl)}
                    >
                      {copiedKey === "mcp-url" ? <Check /> : <Copy />} Copy
                    </Button>
                  </div>
                </div>

                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label>Mẫu cấu hình MCP JSON</Label>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void copyText("mcp-json", mcpClientSnippet)}
                    >
                      {copiedKey === "mcp-json" ? <Check /> : <Copy />} Sao chép JSON
                    </Button>
                  </div>
                  <pre className="max-h-44 overflow-auto rounded-lg border bg-muted/40 p-3 font-mono text-xs">
                    {mcpClientSnippet}
                  </pre>
                </div>

                <div className="space-y-2">
                  <Label>Danh sách Tools trả về từ `tools/list`</Label>
                  {toolsQuery.isLoading ? (
                    <Skeleton className="h-24 w-full" />
                  ) : toolsQuery.isError ? (
                    <Alert variant="destructive" title="Lỗi gọi MCP tools/list">
                      {describeApiError(toolsQuery.error, "Không gọi được /api/v1/mcp")}
                    </Alert>
                  ) : (
                    <div className="space-y-2">
                      {tools.map((tool) => (
                        <div key={tool.name} className="rounded-lg border p-2.5 text-xs">
                          <div className="flex items-center gap-2 font-mono font-semibold text-foreground">
                            <Terminal className="h-3.5 w-3.5 text-indigo-600" />
                            <span>{tool.name}</span>
                          </div>
                          <p className="mt-1 text-muted-foreground">{tool.description}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <CardTitle>Chạy thử `query_enterprise_db` (Read-Only)</CardTitle>
                    <CardDescription>
                      Kiểm tra trực tiếp truy vấn SELECT qua MCP bằng DB JWT và tenant_secret.
                    </CardDescription>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setTestDbJwt("");
                      setTestSecret("");
                      setQueryResult(null);
                      setQueryError(null);
                    }}
                    title="Xóa DB JWT và secret khỏi bộ nhớ trình duyệt"
                  >
                    <Eraser /> Xóa khóa RAM
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="tester-connector">Chọn kết nối DB riêng</Label>
                  <Select
                    id="tester-connector"
                    value={testConnectorId}
                    onChange={(e) => setTestConnectorId(e.target.value)}
                  >
                    <option value="">-- Chọn kết nối DB --</option>
                    {connectors.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name} ({c.db_type} · {c.allowed_tables.join(", ")})
                      </option>
                    ))}
                  </Select>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="tester-db-jwt">DB JWT (Read-Only)</Label>
                  <Input
                    id="tester-db-jwt"
                    type="password"
                    placeholder="Dán db_jwt từ bước Cấp DB JWT"
                    value={testDbJwt}
                    onChange={(e) => setTestDbJwt(e.target.value)}
                    className="font-mono text-xs"
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="tester-secret">Tenant Secret</Label>
                  <Input
                    id="tester-secret"
                    type="password"
                    placeholder="Khóa bí mật riêng của doanh nghiệp"
                    value={testSecret}
                    onChange={(e) => setTestSecret(e.target.value)}
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="tester-sql">Câu lệnh SQL (chỉ SELECT)</Label>
                  <Textarea
                    id="tester-sql"
                    rows={3}
                    value={testSql}
                    onChange={(e) => setTestSql(e.target.value)}
                    className="font-mono text-xs"
                  />
                </div>

                <Button
                  className="w-full"
                  disabled={
                    runSqlMutation.isPending ||
                    !testConnectorId ||
                    !testDbJwt.trim() ||
                    !testSecret ||
                    !testSql.trim()
                  }
                  onClick={() => runSqlMutation.mutate()}
                >
                  {runSqlMutation.isPending ? <Spinner /> : <Play />} Thực thi qua MCP
                </Button>

                {queryError ? (
                  <Alert variant="destructive" title="Truy vấn bị từ chối">
                    {queryError}
                  </Alert>
                ) : null}

                {queryResult ? (
                  <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-medium">{queryResult.connector_name}</span>
                      <Badge variant="success">{queryResult.row_count} dòng</Badge>
                    </div>
                    {queryResult.rows.length === 0 ? (
                      <p className="text-xs text-muted-foreground">Không có dòng dữ liệu nào khớp truy vấn.</p>
                    ) : (
                      <div className="max-h-56 overflow-auto">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              {resultColumns.map((col) => (
                                <TableHead key={col} className="font-mono text-xs">
                                  {col}
                                </TableHead>
                              ))}
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {queryResult.rows.map((row, idx) => (
                              <TableRow key={idx}>
                                {resultColumns.map((col) => (
                                  <TableCell key={col} className="font-mono text-xs">
                                    {String(row[col] ?? "")}
                                  </TableCell>
                                ))}
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>
                    )}
                  </div>
                ) : null}
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      {/* Dialog 1: Thêm kết nối DB riêng */}
      <Dialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        title="Đăng ký kết nối DB riêng của doanh nghiệp"
        description="DSN được mã hóa bằng AES-256-GCM với tenant_secret (khuyến nghị dùng user DB chỉ có quyền SELECT)."
      >
        <form onSubmit={handleCreateSubmit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="ext-name">Tên kết nối</Label>
              <Input
                id="ext-name"
                required
                placeholder="VD: ERP Orders DB"
                value={connName}
                onChange={(e) => setConnName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ext-type">Loại CSDL</Label>
              <Select
                id="ext-type"
                value={dbType}
                onChange={(e) => setDbType(e.target.value as ExternalDbType)}
              >
                <option value="postgresql">PostgreSQL (postgresql+asyncpg)</option>
                <option value="sqlite">SQLite (sqlite+aiosqlite)</option>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ext-uri">Chuỗi kết nối (Connection URI / DSN)</Label>
            <div className="flex gap-2">
              <Input
                id="ext-uri"
                required
                type={showUri ? "text" : "password"}
                placeholder={
                  dbType === "postgresql"
                    ? "postgresql+asyncpg://readonly:pass@db.company.com:5432/erp"
                    : "sqlite+aiosqlite:///path/to/erp.db"
                }
                value={connectionUri}
                onChange={(e) => setConnectionUri(e.target.value)}
                className="font-mono text-xs"
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setShowUri((prev) => !prev)}
                aria-label="Hiện hoặc ẩn chuỗi DSN"
              >
                {showUri ? <EyeOff /> : <Eye />}
              </Button>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ext-secret">Tenant Secret (tối thiểu 8 ký tự — server không lưu khóa này)</Label>
            <Input
              id="ext-secret"
              required
              minLength={8}
              type="password"
              placeholder="Nhập khóa bí mật do doanh nghiệp tự giữ"
              value={createSecret}
              onChange={(e) => setCreateSecret(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ext-tables">Danh sách bảng được phép đọc (`allowed_tables`, phân cách dấu phẩy)</Label>
            <Input
              id="ext-tables"
              required
              placeholder="orders, customers, invoices"
              value={allowedTablesRaw}
              onChange={(e) => setAllowedTablesRaw(e.target.value)}
              className="font-mono text-xs"
            />
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>
              Hủy
            </Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending ? <Spinner /> : <ShieldCheck />} Mã hóa & Lưu kết nối
            </Button>
          </div>
        </form>
      </Dialog>

      {/* Dialog 2: Cấp DB JWT */}
      <Dialog
        open={Boolean(tokenTarget)}
        onOpenChange={(open) => {
          if (!open) {
            setTokenTarget(null);
            setIssuedToken(null);
            setTokenSecret("");
          }
        }}
        title={tokenTarget ? `Cấp DB JWT cho "${tokenTarget.name}"` : "Cấp DB JWT"}
        description="Xác thực bằng tenant_secret để ký token phạm vi Read-Only cho AI Agent."
      >
        <form onSubmit={handleIssueToken} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="issue-secret">Tenant Secret</Label>
            <Input
              id="issue-secret"
              required
              minLength={8}
              type="password"
              placeholder="Nhập đúng tenant_secret đã dùng khi đăng ký"
              value={tokenSecret}
              onChange={(e) => setTokenSecret(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="issue-exp">Thời hạn hiệu lực (phút, từ 5 đến 1440)</Label>
            <Input
              id="issue-exp"
              type="number"
              min={5}
              max={1440}
              value={expiresMinutes}
              onChange={(e) => setExpiresMinutes(e.target.value)}
            />
          </div>

          {issuedToken ? (
            <div className="space-y-2 rounded-lg border bg-emerald-50/60 p-3">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-emerald-800">DB JWT đã ký (hết hạn sau {issuedToken.expires_in_seconds}s)</span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void copyText("db-jwt", issuedToken.db_jwt)}
                >
                  {copiedKey === "db-jwt" ? <Check /> : <Copy />} Copy JWT
                </Button>
              </div>
              <Textarea readOnly rows={3} value={issuedToken.db_jwt} className="font-mono text-xs" />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="w-full"
                onClick={() => {
                  if (!tokenTarget) return;
                  setTestConnectorId(tokenTarget.id);
                  setTestDbJwt(issuedToken.db_jwt);
                  setTestSecret(tokenSecret);
                  if (tokenTarget.allowed_tables.length > 0) {
                    setTestSql(`SELECT * FROM ${tokenTarget.allowed_tables[0]} LIMIT 10`);
                  }
                  setTokenTarget(null);
                  setActiveTab("mcp");
                }}
              >
                <Play /> Nạp vào trình truy vấn thử
              </Button>
            </div>
          ) : null}

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" onClick={() => setTokenTarget(null)}>
              Đóng
            </Button>
            <Button type="submit" disabled={issueTokenMutation.isPending}>
              {issueTokenMutation.isPending ? <Spinner /> : <KeyRound />} Ký & Cấp DB JWT
            </Button>
          </div>
        </form>
      </Dialog>

      {/* Dialog 3: Xác nhận xóa */}
      <Dialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title="Xác nhận xóa kết nối DB"
        description={
          deleteTarget
            ? `Xóa kết nối "${deleteTarget.name}"? Toàn bộ DB JWT đã cấp trước đó cho kết nối này sẽ không còn truy vấn được.`
            : undefined
        }
        size="sm"
      >
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={() => setDeleteTarget(null)}>
            Hủy
          </Button>
          <Button
            variant="destructive"
            disabled={deleteMutation.isPending}
            onClick={() => {
              if (deleteTarget) deleteMutation.mutate(deleteTarget.id);
            }}
          >
            {deleteMutation.isPending ? <Spinner /> : <Trash2 />} Xác nhận xóa
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
