-- Onboarding agents, part 2: the intake agent (Agentic AI integration map,
-- "Frontier onboarding").
--
-- Applied after 003_onboarding_review.sql by scripts/install_agent_schema.py,
-- on purpose and with approval. Idempotent. Needs migration 064 (the
-- onboarding case tables) to be in place first.
--
-- Adds:
--   1. one scoped read-only view of cases before approval 1, with the terms
--      the owner entered, for the tool server only;
--   2. the intake agent's own table of drafted terms, for the agent writer.
-- The agent reads the client's documents (mandate, fee terms) from the private
-- onboarding-intake container and drafts the account terms, quoting the clause
-- each term came from. Nothing here can change a case or its terms: the owner
-- reads the draft beside approval 1 and approves the terms on the case page.

-- Scoped case view (tool server) ----------------------------------------------

-- Cases in stage 1 or 2 (before approval 1), not cancelled, with only what
-- the drafter compares against: the terms the owner entered.
CREATE OR REPLACE FUNCTION agent.scoped_onboarding_intake_cases()
RETURNS TABLE (
    case_id uuid, source_kind text, subject_id text, display_name text, description text,
    reporting_currency text, history_start_date text, stage text
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT c.case_id, c.source_kind, c.target_subject_id, c.display_name, c.description,
           c.reporting_currency, c.history_start_date::text, c.stage
      FROM public.broker_onboarding_cases c
     WHERE c.tenant_id = agent.tenant() AND c.stage IN ('request', 'load_and_reconcile')
       AND c.status <> 'cancelled'
       AND NOT EXISTS (SELECT 1 FROM public.broker_onboarding_approvals a
                        WHERE a.tenant_id = c.tenant_id AND a.case_id = c.case_id
                          AND a.approval_kind = 'account_terms')
$$;

CREATE OR REPLACE VIEW agent.onboarding_intake_cases AS SELECT * FROM agent.scoped_onboarding_intake_cases();

-- Drafted terms (agent writer) --------------------------------------------------

-- One draft per case and set of documents. ``documents_sha256`` is the digest
-- of the documents read (name, size and stored hash of each), so a new or
-- replaced document needs a new draft. ``terms`` holds each term's value with
-- the document, page and exact quote it came from; every quote was checked
-- against the document's text before it was saved. ``findings`` are the
-- points the owner must look at (a conflict between documents, a term that
-- differs from what the case says, a document that could not be read).
CREATE TABLE IF NOT EXISTS agent.onboarding_term_drafts (
    draft_id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    case_id uuid NOT NULL,
    subject_id text NOT NULL,
    documents_sha256 text NOT NULL CHECK (documents_sha256 ~ '^[0-9a-f]{64}$'),
    outcome text NOT NULL CHECK (outcome IN ('pass', 'warn', 'fail')),
    summary text NOT NULL CHECK (char_length(summary) BETWEEN 1 AND 1000),
    terms jsonb NOT NULL DEFAULT '{}'::jsonb,
    findings jsonb NOT NULL DEFAULT '[]'::jsonb,
    documents jsonb NOT NULL DEFAULT '[]'::jsonb,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    correlation_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, case_id, documents_sha256)
);

-- Grants ----------------------------------------------------------------------

REVOKE ALL ON agent.onboarding_term_drafts, agent.onboarding_intake_cases FROM PUBLIC;
REVOKE ALL ON FUNCTION agent.scoped_onboarding_intake_cases() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION agent.scoped_onboarding_intake_cases() TO agent_reader;
GRANT SELECT ON agent.onboarding_intake_cases TO agent_reader;

GRANT SELECT, INSERT ON agent.onboarding_term_drafts TO agent_writer;
GRANT USAGE ON SEQUENCE agent.onboarding_term_drafts_draft_id_seq TO agent_writer;
