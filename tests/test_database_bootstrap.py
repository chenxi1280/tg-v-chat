from tg_v_chat.storage.ensure_database import database_name, maintenance_database_url


def test_database_name_reads_url_path():
    assert database_name("postgresql+psycopg://app_user:secret@postgres:5432/tg_v_chat") == "tg_v_chat"


def test_maintenance_database_url_points_to_postgres_database():
    url = "postgresql+psycopg://app_user:secret@postgres:5432/tg_v_chat?connect_timeout=3"

    assert maintenance_database_url(url) == (
        "postgresql+psycopg://app_user:secret@postgres:5432/postgres?connect_timeout=3"
    )
