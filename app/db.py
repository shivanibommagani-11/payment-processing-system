import psycopg2
from app.config import settings

def get_db_connection():
    return psycopg2.connect(
        dbname=settings.POSTGRES_DB,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT
    )

def get_db():
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()
