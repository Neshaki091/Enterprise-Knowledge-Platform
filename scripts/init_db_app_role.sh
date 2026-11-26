#!/bin/sh
# ==============================================================================
# Tạo role ứng dụng không đặc quyền cho PostgreSQL 16 (RLS bắt buộc).
#
# Chỉ chạy tự động MỘT LẦN khi Docker volume PostgreSQL còn trống
# (docker-entrypoint-initdb.d). Với volume đã tồn tại, phải tạo role và
# chuyển quyền sở hữu bảng thủ công trước khi bật DB_RLS_ENABLED=true.
# ==============================================================================
set -eu

: "${APP_DB_USER:=saas_app}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD is required to create the application role}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${APP_DB_USER}') THEN
        CREATE ROLE ${APP_DB_USER} LOGIN PASSWORD '${APP_DB_PASSWORD}'
            NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    ELSE
        ALTER ROLE ${APP_DB_USER} WITH LOGIN PASSWORD '${APP_DB_PASSWORD}'
            NOSUPERUSER NOBYPASSRLS;
    END IF;
END
\$\$;

GRANT CONNECT ON DATABASE "${POSTGRES_DB}" TO ${APP_DB_USER};
GRANT USAGE, CREATE ON SCHEMA public TO ${APP_DB_USER};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ${APP_DB_USER};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ${APP_DB_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
    GRANT ALL ON TABLES TO ${APP_DB_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
    GRANT ALL ON SEQUENCES TO ${APP_DB_USER};
EOSQL

echo "Đã tạo/cập nhật role ứng dụng '${APP_DB_USER}' (NOSUPERUSER, NOBYPASSRLS)."
