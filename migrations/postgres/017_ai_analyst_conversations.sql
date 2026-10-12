CREATE TABLE ai_analyst_conversations (
    conversation_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    owner_identity_sha256 TEXT NOT NULL CHECK (owner_identity_sha256 ~ '^[0-9a-f]{64}$'),
    title TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, conversation_id)
);

CREATE INDEX ai_analyst_conversations_owner_updated_idx
    ON ai_analyst_conversations(
        tenant_id, owner_identity_sha256, subject_id, updated_at DESC
    );

CREATE TABLE ai_analyst_messages (
    message_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    conversation_id UUID NOT NULL,
    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content_text TEXT NOT NULL CHECK (char_length(content_text) BETWEEN 1 AND 20000),
    response_json JSONB,
    as_of_date DATE,
    benchmark_id TEXT,
    correlation_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, conversation_id)
        REFERENCES ai_analyst_conversations(tenant_id, conversation_id)
        ON DELETE CASCADE,
    UNIQUE (tenant_id, conversation_id, sequence_no),
    CHECK (
        (role = 'user' AND response_json IS NULL)
        OR (role = 'assistant' AND response_json IS NOT NULL)
    )
);

CREATE INDEX ai_analyst_messages_conversation_sequence_idx
    ON ai_analyst_messages(tenant_id, conversation_id, sequence_no);

ALTER TABLE ai_analyst_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_analyst_conversations FORCE ROW LEVEL SECURITY;
CREATE POLICY ai_analyst_conversations_tenant_policy
    ON ai_analyst_conversations
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE ai_analyst_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_analyst_messages FORCE ROW LEVEL SECURITY;
CREATE POLICY ai_analyst_messages_tenant_policy
    ON ai_analyst_messages
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

