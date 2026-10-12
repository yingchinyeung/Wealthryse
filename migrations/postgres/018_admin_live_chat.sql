CREATE TABLE admin_chat_conversations (
    conversation_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    title TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120),
    initiated_by TEXT NOT NULL CHECK (initiated_by IN ('owner', 'administrator')),
    source_key TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    owner_last_read_sequence INTEGER NOT NULL DEFAULT 0 CHECK (owner_last_read_sequence >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, conversation_id)
);

CREATE UNIQUE INDEX admin_chat_conversations_source_key_idx
    ON admin_chat_conversations(tenant_id, source_key)
    WHERE source_key IS NOT NULL;
CREATE INDEX admin_chat_conversations_updated_idx
    ON admin_chat_conversations(tenant_id, status, updated_at DESC);

CREATE TABLE admin_chat_messages (
    message_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    conversation_id UUID NOT NULL,
    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
    role TEXT NOT NULL CHECK (role IN ('owner', 'administrator')),
    message_kind TEXT NOT NULL CHECK (message_kind IN (
        'chat', 'proactive_update', 'incident', 'action_preview', 'action_result'
    )),
    content_text TEXT NOT NULL CHECK (char_length(content_text) BETWEEN 1 AND 20000),
    response_json JSONB,
    approval_id BIGINT REFERENCES approved_actions(approval_id),
    incident_id BIGINT REFERENCES system_incidents(incident_id),
    correlation_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, conversation_id)
        REFERENCES admin_chat_conversations(tenant_id, conversation_id)
        ON DELETE CASCADE,
    UNIQUE (tenant_id, conversation_id, sequence_no),
    CHECK (role = 'administrator' OR response_json IS NULL)
);

CREATE INDEX admin_chat_messages_conversation_sequence_idx
    ON admin_chat_messages(tenant_id, conversation_id, sequence_no);

ALTER TABLE admin_chat_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_chat_conversations FORCE ROW LEVEL SECURITY;
CREATE POLICY admin_chat_conversations_tenant_policy
    ON admin_chat_conversations
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE admin_chat_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_chat_messages FORCE ROW LEVEL SECURITY;
CREATE POLICY admin_chat_messages_tenant_policy
    ON admin_chat_messages
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());
