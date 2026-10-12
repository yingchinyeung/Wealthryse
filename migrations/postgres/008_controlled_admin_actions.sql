ALTER TABLE approved_actions
    ADD COLUMN IF NOT EXISTS action_payload_json TEXT,
    ADD COLUMN IF NOT EXISTS preview_json TEXT,
    ADD COLUMN IF NOT EXISTS expires_at TEXT,
    ADD COLUMN IF NOT EXISTS failure_code TEXT;

ALTER TABLE approved_actions
    DROP CONSTRAINT IF EXISTS approved_actions_status_check;

ALTER TABLE approved_actions
    ADD CONSTRAINT approved_actions_status_check
    CHECK (status IN (
        'pending', 'approved', 'rejected', 'executing',
        'executed', 'expired', 'failed'
    ));

CREATE INDEX IF NOT EXISTS idx_approved_actions_status
    ON approved_actions(tenant_id, status, expires_at);
