import json
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
            """
            INSERT INTO transactions (transaction_id, idempotency_key, account_id, merchant_id, amount, currency, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'PENDING')
            RETURNING transaction_id, status;
            """,
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
