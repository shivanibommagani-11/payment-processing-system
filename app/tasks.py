import time
import random
import requests
from app.config import settings
from app.db import get_db_connection

def evaluate_fraud_risk_local_llm(account_id: str, amount: float) -> bool:
    "Uses local Ollama endpoint or falls back if unavailable."
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
