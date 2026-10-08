import requests
from datetime import date
from app.db import get_db_connection
from app.config import settings

def generate_reconciliation_report():
    conn = get_db_connection()
    cursor = conn.cursor()
    today = date.today()

    cursor.execute("""
        SELECT 
            COUNT(*) as total_txs,
            COALESCE(SUM(amount), 0) as total_vol,
            COUNT(*) FILTER (WHERE status = 'SUCCESS') as success_cnt,
            COUNT(*) FILTER (WHERE status = 'FAILED') as failed_cnt
        FROM transactions
        WHERE DATE(created_at) = %s
    """, (today,))

    stats = cursor.fetchone()
    total_txs, total_vol, success_cnt, failed_cnt = stats

    cursor.execute("""
        SELECT COUNT(t.transaction_id) 
        FROM transactions t 
        LEFT JOIN ledger_entries l ON t.transaction_id = l.transaction_id 
        WHERE t.status = 'SUCCESS' AND l.entry_id IS NULL AND DATE(t.created_at) = %s
    """, (today,))
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

    cursor.execute("""
        INSERT INTO reconciliation_reports (report_date, total_processed, total_volume, successful_txs, failed_txs, discrepancy_count, summary_notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (today, total_txs, total_vol, success_cnt, failed_cnt, discrepancy_count, notes))

    conn.commit()
    conn.close()
    print("Reconciliation report created successfully.")

if __name__ == "__main__":
    generate_reconciliation_report()
