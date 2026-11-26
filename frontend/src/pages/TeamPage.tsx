import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building, Plus, Search, UserPlus, Users } from "lucide-react";
import { toast } from "sonner";
import { departmentApi, memberApi } from "@/lib/api";
import { describeApiError } from "@/lib/api-client";
import { roleLabel } from "@/lib/labels";
import { formatDateTime, initialsOf } from "@/lib/utils";
import { PageHeader } from "@/components/layout/page-header";
import { StatCard } from "@/components/layout/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Alert } from "@/components/feedback/alert";
import { EmptyState } from "@/components/feedback/empty-state";
import { Spinner } from "@/components/feedback/spinner";

const ROLE_VARIANTS: Record<string, "violet" | "default" | "info" | "secondary"> = {
  OWNER: "violet",
  ADMIN: "default",
  COMPLIANCE_OFFICER: "info",
  EMPLOYEE: "secondary",
};

/** Trang quản trị phòng ban và nhân sự của Tenant (yêu cầu ADMIN/OWNER). */
export function TeamPage() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("");
  const [deptOpen, setDeptOpen] = useState(false);
  const [memberOpen, setMemberOpen] = useState(false);

  const [deptName, setDeptName] = useState("");
  const [deptCode, setDeptCode] = useState("");

  const [memberName, setMemberName] = useState("");
  const [memberEmail, setMemberEmail] = useState("");
  const [memberPassword, setMemberPassword] = useState("");
  const [memberRole, setMemberRole] = useState("EMPLOYEE");
  const [memberDepartment, setMemberDepartment] = useState("");

  const departmentsQuery = useQuery({ queryKey: ["departments"], queryFn: departmentApi.list });
  const membersQuery = useQuery({ queryKey: ["members"], queryFn: memberApi.list });

  const createDepartment = useMutation({
    mutationFn: departmentApi.create,
    onSuccess: async (created) => {
      toast.success(`Đã tạo phòng ban ${created.name}`);
      setDeptOpen(false);
      setDeptName("");
      setDeptCode("");
      await queryClient.invalidateQueries({ queryKey: ["departments"] });
    },
    onError: (error) => toast.error(describeApiError(error, "Không thể tạo phòng ban")),
  });

  const addMember = useMutation({
    mutationFn: memberApi.add,
    onSuccess: async (created) => {
      toast.success(`Đã thêm ${created.full_name} vào doanh nghiệp`);
      setMemberOpen(false);
      setMemberName("");
      setMemberEmail("");
      setMemberPassword("");
      setMemberRole("EMPLOYEE");
      setMemberDepartment("");
      await queryClient.invalidateQueries({ queryKey: ["members"] });
    },
    onError: (error) => toast.error(describeApiError(error, "Không thể thêm nhân sự")),
  });

  const departments = departmentsQuery.data ?? [];
  const members = membersQuery.data ?? [];

  const departmentNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const department of departments) map.set(department.id, department.name);
    return map;
  }, [departments]);

  const filteredMembers = useMemo(() => {
    const keyword = filter.trim().toLowerCase();
    if (!keyword) return members;
    return members.filter(
      (member) =>
        member.full_name.toLowerCase().includes(keyword) ||
        member.email.toLowerCase().includes(keyword) ||
        roleLabel(member.role).toLowerCase().includes(keyword),
    );
  }, [members, filter]);

  const handleCreateDepartment = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    createDepartment.mutate({ name: deptName.trim(), code: deptCode.trim().toUpperCase() });
  };

  const handleAddMember = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    addMember.mutate({
      email: memberEmail.trim(),
      password: memberPassword,
      full_name: memberName.trim(),
      role: memberRole,
      department_id: memberDepartment || null,
    });
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Phòng ban & Nhân sự"
        description="Quản lý cơ cấu tổ chức trong doanh nghiệp. Nhân viên gắn phòng ban chỉ truy cập được tài liệu thuộc phận sự của mình."
        actions={
          <>
            <Button variant="outline" onClick={() => setDeptOpen(true)}>
              <Building /> Tạo phòng ban
            </Button>
            <Button onClick={() => setMemberOpen(true)}>
              <UserPlus /> Thêm nhân sự
            </Button>
          </>
        }
      />

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard label="Tổng thành viên" value={members.length} icon={Users} tone="indigo" hint="Bao gồm mọi vai trò đang hoạt động" />
        <StatCard label="Phòng ban" value={departments.length} icon={Building} tone="sky" hint="Đơn vị phân quyền tài liệu" />
        <StatCard
          label="Quản trị viên"
          value={members.filter((member) => ["OWNER", "ADMIN"].includes(member.role.toUpperCase())).length}
          icon={UserPlus}
          tone="violet"
          hint="OWNER và ADMIN có toàn quyền tenant"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[340px_1fr]">
        <Card className="h-fit">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Phòng ban</CardTitle>
            <Button variant="ghost" size="sm" onClick={() => setDeptOpen(true)}>
              <Plus /> Thêm
            </Button>
          </CardHeader>
          <CardContent className="space-y-2">
            {departmentsQuery.isLoading ? (
              Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-14 rounded-lg" />)
            ) : departments.length === 0 ? (
              <EmptyState
                icon={Building}
                title="Chưa có phòng ban"
                description="Tạo phòng ban đầu tiên để phân vùng tài liệu và giới hạn quyền đọc của nhân viên."
                action={<Button size="sm" onClick={() => setDeptOpen(true)}><Plus /> Tạo phòng ban</Button>}
              />
            ) : (
              departments.map((department) => {
                const count = members.filter((member) => member.department_id === department.id).length;
                return (
                  <div
                    key={department.id}
                    className="flex items-center justify-between rounded-lg border bg-muted/20 px-3 py-2.5"
                  >
                    <div className="flex items-center gap-2.5">
                      <div className="flex h-8 w-8 items-center justify-center rounded-md bg-sky-50 text-sky-600">
                        <Building className="h-4 w-4" />
                      </div>
                      <div>
                        <p className="text-xs font-medium">{department.name}</p>
                        <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{department.code}</p>
                      </div>
                    </div>
                    <Badge variant="outline">{count} người</Badge>
                  </div>
                );
              })
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="gap-3 sm:flex-row sm:items-center sm:justify-between">
            <CardTitle>Thành viên doanh nghiệp</CardTitle>
            <div className="relative w-full sm:w-64">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                className="h-8 pl-8 text-xs"
                placeholder="Tìm theo tên, email, vai trò…"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
              />
            </div>
          </CardHeader>
          <CardContent className="px-0 pb-0">
            {membersQuery.isLoading ? (
              <div className="space-y-2 px-5 pb-5">
                {Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-12 rounded-lg" />)}
              </div>
            ) : membersQuery.isError ? (
              <div className="px-5 pb-5">
                <Alert variant="destructive" title="Không tải được danh sách">
                  {describeApiError(membersQuery.error, "Vui lòng thử lại sau.")}
                </Alert>
              </div>
            ) : filteredMembers.length === 0 ? (
              <EmptyState
                icon={Users}
                title={members.length === 0 ? "Chưa có nhân sự" : "Không khớp bộ lọc"}
                description={members.length === 0 ? "Mời nhân viên đầu tiên tham gia doanh nghiệp." : "Thử từ khóa khác."}
              />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="pl-5">Nhân sự</TableHead>
                    <TableHead>Vai trò</TableHead>
                    <TableHead>Phòng ban</TableHead>
                    <TableHead>Tham gia</TableHead>
                    <TableHead className="pr-5 text-right">Trạng thái</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredMembers.map((member) => (
                    <TableRow key={member.membership_id}>
                      <TableCell className="pl-5">
                        <div className="flex items-center gap-2.5">
                          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[10px] font-semibold text-slate-600">
                            {initialsOf(member.full_name || member.email)}
                          </div>
                          <div className="min-w-0">
                            <p className="truncate text-xs font-medium">{member.full_name}</p>
                            <p className="truncate text-[10px] text-muted-foreground">{member.email}</p>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={ROLE_VARIANTS[member.role.toUpperCase()] ?? "secondary"}>
                          {roleLabel(member.role)}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {member.department_id ? departmentNames.get(member.department_id) ?? "—" : "Toàn Tenant"}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{formatDateTime(member.created_at)}</TableCell>
                      <TableCell className="pr-5 text-right">
                        <Badge variant={member.is_active ? "success" : "destructive"}>
                          {member.is_active ? "Hoạt động" : "Đã khóa"}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      <Dialog
        open={deptOpen}
        onOpenChange={setDeptOpen}
        title="Tạo phòng ban mới"
        description="Phòng ban dùng để phân vùng tài liệu và giới hạn phạm vi đọc của nhân viên."
        footer={
          <>
            <Button variant="outline" onClick={() => setDeptOpen(false)}>Hủy</Button>
            <Button type="submit" form="create-department-form" disabled={createDepartment.isPending}>
              {createDepartment.isPending ? <Spinner /> : <Plus />} Tạo phòng ban
            </Button>
          </>
        }
      >
        <form id="create-department-form" onSubmit={handleCreateDepartment} className="space-y-4">
          {createDepartment.isError ? (
            <Alert variant="destructive">{describeApiError(createDepartment.error, "Không thể tạo phòng ban")}</Alert>
          ) : null}
          <div className="space-y-1.5">
            <Label htmlFor="dept-name">Tên phòng ban</Label>
            <Input
              id="dept-name"
              required
              minLength={2}
              maxLength={255}
              placeholder="Phòng Tài chính - Kế toán"
              value={deptName}
              onChange={(event) => setDeptName(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="dept-code">Mã phòng ban</Label>
            <Input
              id="dept-code"
              required
              minLength={2}
              maxLength={50}
              pattern="^[A-Za-z0-9_\-]+$"
              placeholder="FINANCE"
              value={deptCode}
              onChange={(event) => setDeptCode(event.target.value.toUpperCase())}
            />
            <p className="text-[10px] text-muted-foreground">Chỉ chữ, số, gạch ngang hoặc gạch dưới.</p>
          </div>
        </form>
      </Dialog>

      <Dialog
        open={memberOpen}
        onOpenChange={setMemberOpen}
        title="Thêm nhân sự vào doanh nghiệp"
        description="Nếu email chưa tồn tại, hệ thống tạo tài khoản mới với mật khẩu khởi tạo bên dưới."
        footer={
          <>
            <Button variant="outline" onClick={() => setMemberOpen(false)}>Hủy</Button>
            <Button type="submit" form="add-member-form" disabled={addMember.isPending}>
              {addMember.isPending ? <Spinner /> : <UserPlus />} Thêm nhân sự
            </Button>
          </>
        }
      >
        <form id="add-member-form" onSubmit={handleAddMember} className="space-y-4">
          {addMember.isError ? (
            <Alert variant="destructive">{describeApiError(addMember.error, "Không thể thêm nhân sự")}</Alert>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="member-name">Họ và tên</Label>
              <Input
                id="member-name"
                required
                minLength={2}
                maxLength={255}
                placeholder="Trần Bảo"
                value={memberName}
                onChange={(event) => setMemberName(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="member-email">Email</Label>
              <Input
                id="member-email"
                type="email"
                required
                placeholder="bao.tran@congty.vn"
                value={memberEmail}
                onChange={(event) => setMemberEmail(event.target.value)}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="member-password">Mật khẩu khởi tạo</Label>
            <Input
              id="member-password"
              type="password"
              required
              minLength={8}
              autoComplete="new-password"
              placeholder="Tối thiểu 8 ký tự (chỉ dùng khi tạo tài khoản mới)"
              value={memberPassword}
              onChange={(event) => setMemberPassword(event.target.value)}
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="member-role">Vai trò</Label>
              <Select id="member-role" value={memberRole} onChange={(event) => setMemberRole(event.target.value)}>
                <option value="EMPLOYEE">Nhân viên</option>
                <option value="COMPLIANCE_OFFICER">Kiểm soát tuân thủ</option>
                <option value="ADMIN">Quản trị viên</option>
                <option value="OWNER">Chủ sở hữu</option>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="member-department">Phòng ban (tùy chọn)</Label>
              <Select
                id="member-department"
                value={memberDepartment}
                onChange={(event) => setMemberDepartment(event.target.value)}
              >
                <option value="">Toàn doanh nghiệp</option>
                {departments.map((department) => (
                  <option key={department.id} value={department.id}>
                    {department.name}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          <Alert variant="warning">
            Nhân viên gắn phòng ban chỉ đọc được tài liệu của phòng ban đó và tài liệu toàn doanh nghiệp.
            Hạn ngạch <span className="font-medium">max_users</span> của gói dịch vụ được kiểm tra tự động.
          </Alert>
        </form>
      </Dialog>
    </div>
  );
}
