import uuid
from app.models.tenant import Tenant
from app.models.user import User
from app.models.department import Department
from app.models.membership import Membership


def test_tenant_model_instantiation():
    """Kiểm tra khởi tạo đối tượng Tenant với các thuộc tính mặc định."""
    tenant = Tenant(
        name="ABC Technology",
        slug="abc-tech",
        quota_limits={"max_users": 100, "max_documents": 5000}
    )
    assert tenant.name == "ABC Technology"
    assert tenant.slug == "abc-tech"
    assert tenant.quota_limits["max_users"] == 100
    assert tenant.__tablename__ == "tenants"


def test_user_and_membership_relationship():
    """Kiểm tra mối quan hệ giữa User, Tenant, Department và Membership."""
    user = User(
        email="admin@abctech.com",
        hashed_password="fake_hashed_password",
        full_name="Nguyễn Văn A"
    )
    tenant = Tenant(name="ABC Technology", slug="abc-tech")
    dept = Department(name="Phòng Công Nghệ Thông Tin", code="IT", tenant=tenant)
    membership = Membership(
        user=user,
        tenant=tenant,
        department=dept,
        role="COMPANY_ADMIN"
    )

    assert membership.user == user
    assert membership.tenant == tenant
    assert membership.department == dept
    assert membership.role == "COMPANY_ADMIN"
    assert dept in tenant.departments
    assert membership in user.memberships
    assert membership in tenant.memberships
