CREATE TYPE transaction_status AS ENUM (
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
