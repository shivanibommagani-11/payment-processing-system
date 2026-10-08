# Pure Python Payment System
A self-contained real-time transaction processing engine built with zero external servers 
(uses DiskCache + FastAPI Background Tasks instead of Redis + Celery).


# Real-Time Transaction Processing Engine

A lightweight, fault-tolerant, real-time payment ingestion and processing system built in pure Python. The system provides sub-millisecond idempotency locking, asynchronous non-blocking task execution, automated retry workflows with exponential backoff, double-entry audit logging, and automated daily reconciliation reports.

Designed to run natively in constrained environments (such as restricted enterprise desktops) without requiring Docker containers or external server daemons like Redis or Celery.

---

## Architecture & System Topology

```text
                                  ┌────────────────────────┐
                                  │   HTTP Client / Curl   │
                                  └───────────┬────────────┘
                                              │
                                              │ POST /v1/payments (X-Idempotency-Key)
                                              ▼
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│                                   FASTAPI WEB ENGINE                                      │
│                                                                                           │
│  ┌─────────────────────────┐     Cached?     ┌─────────────────────────────────────────┐  │
│  │  1. Check Idempotency   ├────────────────►│  Return Saved Response Payload (200 OK) │  │
│  └───────────┬─────────────┘                 └─────────────────────────────────────────┘  │
│              │                                                                            │
│              │ Not Cached                                                                 │
│              ▼                                                                            │
│  ┌─────────────────────────┐    Acquired?    ┌─────────────────────────────────────────┐  │
│  │ 2. DiskCache Mutex Lock ├────────────────►│  409 Conflict: Processing in Progress    │  │
│  └───────────┬─────────────┘                 └─────────────────────────────────────────┘  │
│              │                                                                            │
│              │ Lock Acquired                                                              │
│              ▼                                                                            │
│  ┌─────────────────────────┐                 ┌─────────────────────────────────────────┐  │
│  │  3. Persist Transaction ├────────────────►│  PostgreSQL Database (Status: PENDING)  │  │
│  └───────────┬─────────────┘                 └─────────────────────────────────────────┘  │
│              │                                                                            │
│              │ Instant Dispatch                                                           │
│              ▼                                                                            │
│  ┌─────────────────────────┐                                                              │
│  │ 4. Fast Background Task ├────────────────┐                                             │
│  └───────────┬─────────────┘                │                                             │
│              │                              │                                             │
│              ▼                              │                                             │
│   Return 202 ACCEPTED                       │                                             │
│   (Immediate Client Relief)                 │                                             │
└─────────────────────────────────────────────┼─────────────────────────────────────────────┘
                                              │
                                              ▼
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│                               ASYNC BACKGROUND WORKER ENGINE                              │
│                                                                                           │
│  ┌─────────────────────────┐                 ┌─────────────────────────────────────────┐  │
│  │  5. Mark 'PROCESSING'   ├────────────────►│  PostgreSQL DB                          │  │
│  └───────────┬─────────────┘                 └─────────────────────────────────────────┘  │
│              │                                                                            │
│              ▼                                                                            │
│  ┌─────────────────────────┐   Suspicious?   ┌─────────────────────────────────────────┐  │
│  │ 6. AI Risk / Fraud Check├────────────────►│  Flag Fraud & Terminate Workflow        │  │
│  └───────────┬─────────────┘                 └─────────────────────────────────────────┘  │
│              │                                                                            │
│              │ Safe                                                                       │
│              ▼                                                                            │
│  ┌─────────────────────────┐    Transient    ┌─────────────────────────────────────────┐  │
│  │ 7. Payment Gateway Call ├────────────────►│  Exponential Backoff Retry Schedule     │  │
│  └───────────┬─────────────┘     Failure     │  (Status: RETRYING -> FAILED)           │  │
│              │                               └─────────────────────────────────────────┘  │
│              │ Gateway Success                                                            │
│              ▼                                                                            │
│  ┌─────────────────────────┐                 ┌─────────────────────────────────────────┐  │
│  │ 8. Audit Ledger Logging ├────────────────►│  Insert double-entry into ledger_entries│  │
│  └───────────┬─────────────┘                 └─────────────────────────────────────────┘  │
│              │                                                                            │
│              ▼                                                                            │
│  ┌─────────────────────────┐                 ┌─────────────────────────────────────────┐  │
│  │ 9. Final State Update   ├────────────────►│  PostgreSQL DB (Status: SUCCESS)        │  │
│  └─────────────────────────┘                 └─────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────────────────┘


Core Technical Concepts & TerminologyTermDefinition & Project ImplementationIdempotencyGuaranteeing that executing the same operation multiple times yields the exact same result without duplicate side-effects (e.g., preventing double-charging a credit card on duplicate HTTP clicks).
 Implemented via X-Idempotency-Key headers stored in SQLite-backed DiskCache.Distributed Mutex LockA temporary execution lock that prevents race conditions when two identical requests hit the server at the exact same millisecond. 
 Implemented using atomic key addition (local_cache.add(lock_key, expire=10)).
 Non-Blocking ExecutionDecoupling the HTTP client response from slow downstream dependencies (like payment gateways or risk models). The API responds instantly with HTTP 202 Accepted, running heavy execution in an async background task.ACID LedgerDouble-entry financial audit log guaranteeing Atomicity, Consistency, Isolation, and Durability. Ensures money is never marked as processed without an accompanying immutable entry in ledger_entries.Exponential BackoffA retry strategy for handling transient gateway/network failures. When an execution fails, subsequent retry delays grow exponentially ($2^{\text{attempt}}$ seconds) to prevent overwhelming target servers.ReconciliationAn end-of-day batch processing job (app.reconciliation) that scans transaction logs, detects discrepancies between payment states and ledger entries, and generates aggregated financial reports.Step-by-Step Processing FlowIngestion & Idempotency Check: Client submits POST /v1/payments with an X-Idempotency-Key header.Locking & Cache Lookup: The API checks DiskCache:If a completed response payload exists, it returns it instantly (200 OK).If an active lock exists, it rejects concurrent duplicates (409 Conflict).Otherwise, it acquires a 10-second lock.Database Pre-Persist: Inserts an initial transaction record with status PENDING into PostgreSQL.Immediate Acknowledgment: Enqueues execute_payment_workflow into BackgroundTasks and returns 202 Accepted to the caller within ~15ms.Asynchronous Execution:Updates state to PROCESSING.Evaluates AI risk/fraud thresholds.Connects to payment gateway with automatic backoff retries on failure.On success, writes an audit record to ledger_entries and updates state to SUCCESS.Reconciliation: Batch job compares total volume, successful transactions, failed counts, and detects un-ledgered orphan transactions.Setup & InstallationPrerequisitesPython 3.10+Local PostgreSQL installation (or pgAdmin)ConfigurationClone or extract the repository files:PowerShellpython generate_repo.py
Activate virtual environment and install dependencies:PowerShell.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Configure environment variables in .env:Ini, TOMLPOSTGRES_USER=postgres
POSTGRES_PASSWORD=Incedo@1234
POSTGRES_DB=payments_db
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
CACHE_DIR=./.cache
Initialize the PostgreSQL schema:Open pgAdmin or run via psql.Create database payments_db.Execute the queries inside schema.sql.Start the web application:PowerShelluvicorn app.main:app --reload --port 8000
Testing & Verification1. Submit a Payment (PowerShell)PowerShellInvoke-RestMethod -Uri "http://localhost:8000/v1/payments" `
  -Method Post `
  -Headers @{"X-Idempotency-Key"="tx_unique_999"} `
  -ContentType "application/json" `
  -Body '{"account_id": "acc_user_1", "merchant_id": "mer_store_A", "amount": 250.00, "currency": "USD"}'
2. Submit a Payment (cURL / Bash)Bashcurl -X POST "http://localhost:8000/v1/payments" \
     -H "Content-Type: application/json" \
     -H "X-Idempotency-Key: tx_unique_999" \
     -d '{
           "account_id": "acc_user_1",
           "merchant_id": "mer_store_A",
           "amount": 250.00,
           "currency": "USD"
         }'
3. Verify Idempotency Duplicate RejectionRun the same request again immediately using the same X-Idempotency-Key. Notice that the API returns the original cached response payload without re-executing or double-charging.4. Check Transaction & Ledger Status in DatabasePowerShellpython -c "import psycopg2; conn = psycopg2.connect(host='localhost', port=5432, user='postgres', password='Incedo@1234', dbname='payments_db'); cur = conn.cursor(); cur.execute('SELECT transaction_id, status, amount FROM transactions'); print('TRANSACTIONS:', cur.fetchall()); cur.execute('SELECT * FROM ledger_entries'); print('LEDGERS:', cur.fetchall())"
5. Run Daily Reconciliation ReportPowerShellpython -m app.reconciliation
Key Interview Talking Points1. Architectural Trade-offs & Constraint SatisfactionContext: Restricted enterprise desktop environment without admin privileges, Docker containers, or Redis/Celery background daemons.Solution: Swapped Redis with DiskCache (POSIX/Windows file-locking backed by SQLite) and substituted Celery workers with FastAPI in-memory BackgroundTasks.2. Eliminating Race Conditions & Double-SpendingUtilized DiskCache.add() which acts as an atomic operation (SETNX equivalent in Redis).If two identical requests arrive simultaneously, only one acquires the mutex lock; the secondary request receives a 409 Conflict.3. Financial Consistency & ACID ComplianceLeveraged PostgreSQL transaction blocks (BEGIN...COMMIT/ROLLBACK).Status transitions (PENDING $\rightarrow$ PROCESSING $\rightarrow$ SUCCESS) occur inside transaction blocks alongside ledger inserts, ensuring no ghost transactions exist without audit records.4. Scalability PathThe system is horizontally scalable:Replace DiskCache with a shared Redis cluster for multi-node locking.Replace BackgroundTasks with RabbitMQ / Kafka for durable distributed message queuing.Web nodes remain 100% stateless.


In our payment processing system, rate limiting and concurrency control were handled through Distributed Idempotency Locking using DiskCache and the X-Idempotency-Key header.Instead of just capping requests per minute (like traditional rate limiters), payment systems require strict concurrency suppression—ensuring a user cannot execute two identical charge operations simultaneously even if they click "Pay" 5 times within a single second.How We Implemented It Step-by-Step1. Unique Key ExtractionThe client sends a unique identifier in the HTTP request header:X-Idempotency-Key: tx_unique_1012. Atomic Mutex Locking (local_cache.add)In app/main.py, we construct a lock key (lock:tx_unique_101) and perform an atomic insertion check using SQLite-backed DiskCache:Python# app/main.py

idempotency_key = request.headers.get("X-Idempotency-Key")
cache_key = f"idempotency:{idempotency_key}"
lock_key = f"lock:{idempotency_key}"

# 1. Check if the transaction already completed earlier
cached_response = local_cache.get(cache_key)
if cached_response:
    return JSONResponse(status_code=200, content=json.loads(cached_response))

# 2. ATOMIC LOCK: Try to set the key in cache (acts as SETNX in Redis)
# expire=10 locks the transaction window for 10 seconds
acquired = local_cache.add(lock_key, "LOCKED", expire=10)

if not acquired:
    # A request with this EXACT key is currently processing in background
    raise HTTPException(
        status_code=409, 
        detail="A transaction with this idempotency key is already in progress."
    )
The Three Operational OutingsScenarioIncoming Request StateSystem Action & ResponseNew Payment RequestKey does not exist in DiskCache.Lock is acquired (acquired = True), record saved as PENDING in PostgreSQL, background job triggered, returns 202 Accepted.Concurrent Double-Click (0-10s)Key exists as "LOCKED".Second request fails lock acquisition (acquired = False) and is instantly rejected with 409 Conflict.Retry After Completion (>10s)Key exists in cache with full payload saved.Returns saved 200 OK response instantly from cache without touching PostgreSQL or the payment gateway again.Why This Architecture Was ChosenSub-millisecond Check: Checking DiskCache takes less than 1ms, preventing database connections from filling up during rapid client retries.No Third-Party Daemons: Using DiskCache enabled POSIX file-locking semantics natively in Python on Windows/Linux without needing an admin-installed Redis server.ACID Safety: Guarantees that the payment gateway is called at most once (AMON) per financial request.
