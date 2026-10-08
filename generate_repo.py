import os

files = {
    "requirements.txt": """fastapi>=0.109.0
uvicorn[standard]>=0.27.0
psycopg2-binary>=2.9.9
pydantic>=2.6.0
pydantic-settings>=2.1.0
requests>=2.31.0
diskcache>=5.6.3
""",

    ".env": """POSTGRES_USER=postgres
POSTGRES_PASSWORD=password
POSTGRES_DB=payments_db
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

OLLAMA_HOST=http://localhost:11434
CACHE_DIR=./.cache
""",

    "schema.sql": """CREATE TYPE transaction_status AS ENUM (
    'PENDING', 'PROCESSING', 'SUCCESS', 'FAILED', 'RETRYING', 'FLAGGED_FRAUD'
);

CREATE TYPE transaction_type AS ENUM (
    'PAYMENT', 'REFUND', 'PAYOUT'
);

CREATE TABLE transactions (
    transaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key VARCHAR(255) UNIQUE NOT NULL,
    account_id VARCHAR(100) NOT NULL,
    merchant_id VARCHAR(100) NOT NULL,
    amount NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    tx_type transaction_type NOT NULL DEFAULT 'PAYMENT',
    status transaction_status NOT NULL DEFAULT 'PENDING',
    failure_reason VARCHAR(255),
    retry_count INT DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE ledger_entries (
    entry_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID REFERENCES transactions(transaction_id),
    source_account VARCHAR(100) NOT NULL,
    destination_account VARCHAR(100) NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE reconciliation_reports (
    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_date DATE NOT NULL,
    total_processed INT NOT NULL,
    total_volume NUMERIC(14, 2) NOT NULL,
    successful_txs INT NOT NULL,
    failed_txs INT NOT NULL,
    discrepancy_count INT DEFAULT 0,
    summary_notes TEXT,
    generated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_idempotency ON transactions(idempotency_key);
CREATE INDEX idx_status ON transactions(status);
""",

    "app/__init__.py": "# Package marker\n",

    "app/config.py": """import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "password")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "payments_db")
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", 5432))

    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    CACHE_DIR: str = os.getenv("CACHE_DIR", "./.cache")

    class Config:
        env_file = ".env"

settings = Settings()
""",

    "app/db.py": """import psycopg2
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
""",

    "app/models.py": """from pydantic import BaseModel, Field

class PaymentRequest(BaseModel):
    account_id: str
    merchant_id: str
    amount: float = Field(..., gt=0)
    currency: str = "USD"

class PaymentResponse(BaseModel):
    transaction_id: str
    status: str
    message: str
""",

    "app/main.py": """import json
import uuid
from fastapi import FastAPI, Header, HTTPException, Depends, BackgroundTasks, status
from psycopg2.extras import RealDictCursor
from diskcache import Cache
from app.config import settings
from app.db import get_db
from app.models import PaymentRequest, PaymentResponse
from app.tasks import execute_payment_workflow

app = FastAPI(title="Real-Time Payment Engine (Pure Python)", version="1.0.0")

# Local SQLite-backed cache engine replacing Redis
local_cache = Cache(settings.CACHE_DIR)

@app.post("/v1/payments", status_code=status.HTTP_202_ACCEPTED, response_model=PaymentResponse)
def process_payment(
    payload: PaymentRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str = Header(..., alias="X-Idempotency-Key"),
    db = Depends(get_db)
):
    lock_key = f"lock:{idempotency_key}"
    cache_key = f"idempotency:{idempotency_key}"

    # 1. Idempotency Cache Check (DiskCache replacing Redis GET)
    cached_response = local_cache.get(cache_key)
    if cached_response:
        return json.loads(cached_response)

    # 2. Local Distributed Lock (DiskCache add() with expire time)
    acquired = local_cache.add(lock_key, "LOCKED", expire=10)
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transaction with this key is currently being processed."
        )

    try:
        cursor = db.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM transactions WHERE idempotency_key = %s", (idempotency_key,))
        existing_tx = cursor.fetchone()

        if existing_tx:
            response_data = {
                "transaction_id": str(existing_tx["transaction_id"]),
                "status": existing_tx["status"],
                "message": "Returned existing transaction status."
            }
            local_cache.set(cache_key, json.dumps(response_data), expire=86400)
            return response_data

        # 3. Insert Pending Record
        tx_id = str(uuid.uuid4())
        cursor.execute(
            \"\"\"
            INSERT INTO transactions (transaction_id, idempotency_key, account_id, merchant_id, amount, currency, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'PENDING')
            RETURNING transaction_id, status;
            \"\"\",
            (tx_id, idempotency_key, payload.account_id, payload.merchant_id, payload.amount, payload.currency)
        )
        db.commit()

        # 4. Asynchronous Queue (FastAPI BackgroundTasks replacing Celery)
        background_tasks.add_task(
            execute_payment_workflow,
            tx_id,
            payload.account_id,
            payload.merchant_id,
            payload.amount
        )

        response_payload = {
            "transaction_id": tx_id,
            "status": "PENDING",
            "message": "Payment accepted and queued for background processing."
        }

        local_cache.set(cache_key, json.dumps(response_payload), expire=86400)
        return response_payload

    finally:
        local_cache.delete(lock_key)

@app.get("/v1/payments/{transaction_id}")
def get_transaction_status(transaction_id: str, db = Depends(get_db)):
    cursor = db.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM transactions WHERE transaction_id = %s", (transaction_id,))
    tx = cursor.fetchone()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    tx["created_at"] = str(tx["created_at"])
    tx["updated_at"] = str(tx["updated_at"])
    return tx
""",

    "app/tasks.py": """import time
import random
import requests
from app.config import settings
from app.db import get_db_connection

def evaluate_fraud_risk_local_llm(account_id: str, amount: float) -> bool:
    Uses local Ollama endpoint or falls back if unavailable.
    prompt = f"Analyze risk: Account '{account_id}' transferring ${amount}. Reply ONLY 'SAFE' or 'SUSPICIOUS'."
    try:
        res = requests.post(
            f"{settings.OLLAMA_HOST}/api/generate",
            json={"model": "llama3.2:3b", "prompt": prompt, "stream": False},
            timeout=3
        )
        result = res.json().get("response", "").strip().upper()
        return "SUSPICIOUS" in result
    except Exception:
        return False

def execute_payment_workflow(tx_id: str, account_id: str, merchant_id: str, amount: float, max_retries: int = 3):
    conn = get_db_connection()
    cursor = conn.cursor()

    for attempt in range(1, max_retries + 1):
        try:
            # Set status to PROCESSING
            cursor.execute("UPDATE transactions SET status = 'PROCESSING', updated_at = NOW() WHERE transaction_id = %s", (tx_id,))
            conn.commit()

            # Fraud Check
            is_suspicious = evaluate_fraud_risk_local_llm(account_id, amount)
            if is_suspicious and amount > 5000:
                cursor.execute("UPDATE transactions SET status = 'FLAGGED_FRAUD', failure_reason = 'AI Anomaly Detected' WHERE transaction_id = %s", (tx_id,))
                conn.commit()
                conn.close()
                return

            # Simulate network latency/gateway call
            time.sleep(1)
            if random.random() < 0.2:  # 20% simulated network failure
                raise Exception("Simulated Payment Gateway Timeout")

            # Write Audit Ledger Entry
            cursor.execute(
                "INSERT INTO ledger_entries (transaction_id, source_account, destination_account, amount) VALUES (%s, %s, %s, %s)",
                (tx_id, account_id, merchant_id, amount)
            )

            # Mark Success
            cursor.execute("UPDATE transactions SET status = 'SUCCESS', updated_at = NOW() WHERE transaction_id = %s", (tx_id,))
            conn.commit()
            conn.close()
            return

        except Exception as exc:
            conn.rollback()
            if attempt < max_retries:
                cursor.execute(
                    "UPDATE transactions SET status = 'RETRYING', retry_count = %s, updated_at = NOW() WHERE transaction_id = %s",
                    (attempt, tx_id)
                )
                conn.commit()
                time.sleep(2 ** attempt)  # Exponential Backoff
            else:
                cursor.execute(
                    "UPDATE transactions SET status = 'FAILED', failure_reason = %s, updated_at = NOW() WHERE transaction_id = %s",
                    (str(exc), tx_id)
                )
                conn.commit()
                conn.close()
                return
""",

    "app/reconciliation.py": """import requests
from datetime import date
from app.db import get_db_connection
from app.config import settings

def generate_reconciliation_report():
    conn = get_db_connection()
    cursor = conn.cursor()
    today = date.today()

    cursor.execute(\"\"\"
        SELECT 
            COUNT(*) as total_txs,
            COALESCE(SUM(amount), 0) as total_vol,
            COUNT(*) FILTER (WHERE status = 'SUCCESS') as success_cnt,
            COUNT(*) FILTER (WHERE status = 'FAILED') as failed_cnt
        FROM transactions
        WHERE DATE(created_at) = %s
    \"\"\", (today,))

    stats = cursor.fetchone()
    total_txs, total_vol, success_cnt, failed_cnt = stats

    cursor.execute(\"\"\"
        SELECT COUNT(t.transaction_id) 
        FROM transactions t 
        LEFT JOIN ledger_entries l ON t.transaction_id = l.transaction_id 
        WHERE t.status = 'SUCCESS' AND l.entry_id IS NULL AND DATE(t.created_at) = %s
    \"\"\", (today,))
    discrepancy_count = cursor.fetchone()[0]

    summary_prompt = f"Summarize reconciliation: Total={total_txs}, Volume=${total_vol}, Success={success_cnt}, Failed={failed_cnt}, Discrepancies={discrepancy_count}."
    try:
        res = requests.post(
            f"{settings.OLLAMA_HOST}/api/generate",
            json={"model": "llama3.2:3b", "prompt": summary_prompt, "stream": False},
            timeout=5
        )
        notes = res.json().get("response", "Reconciliation complete.")
    except Exception:
        notes = f"Processed {total_txs} transactions. Total volume: ${total_vol}."

    cursor.execute(\"\"\"
        INSERT INTO reconciliation_reports (report_date, total_processed, total_volume, successful_txs, failed_txs, discrepancy_count, summary_notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    \"\"\", (today, total_txs, total_vol, success_cnt, failed_cnt, discrepancy_count, notes))

    conn.commit()
    conn.close()
    print("Reconciliation report created successfully.")

if __name__ == "__main__":
    generate_reconciliation_report()
""",

    "README.md": """# Pure Python Payment System
A self-contained real-time transaction processing engine built with zero external servers (uses DiskCache + FastAPI Background Tasks instead of Redis + Celery).
"""
}

def create_repository():
    print("Generating pure-Python repository...")
    for path, content in files.items():
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  Created: {path}")
    print("\nRepository created successfully with zero Docker/Redis dependencies!")

if __name__ == "__main__":
    create_repository()