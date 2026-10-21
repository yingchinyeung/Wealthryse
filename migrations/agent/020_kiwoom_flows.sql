-- Kiwoom accounts' deposits and withdrawals for the agents' external-flow reader.
--
-- Applied after 019_onboarding_review_designed.sql by scripts/install_agent_schema.py,
-- on purpose and with approval. Idempotent.
--
-- A Kiwoom account has no Flex file: provisional_benchmark._kiwoom_flows reads its
-- deposits and withdrawals from cash_transactions (PR 186). The tool server runs that
-- code with search_path = agent, where cash_transactions did not exist, so the
-- performance, NAV history and risk tools failed for a Kiwoom account. This view
-- gives it only what that reader uses: the DEPOSIT and WITHDRAWAL rows of a scoped
-- Kiwoom account, and the five columns the reader selects.

CREATE OR REPLACE FUNCTION agent.scoped_kiwoom_flows()
RETURNS TABLE (account_id text, effective_date text, transaction_type text, amount_text text, currency text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT c.account_id, c.effective_date, c.transaction_type, c.amount_text, c.currency
      FROM public.cash_transactions c
      JOIN public.accounts a ON a.account_id = c.account_id AND a.account_type = 'kiwoom'
     WHERE c.account_id = ANY(agent.scope())
       AND c.transaction_type IN ('DEPOSIT', 'WITHDRAWAL')
$$;

CREATE OR REPLACE VIEW agent.cash_transactions AS
    SELECT account_id, effective_date, transaction_type, amount_text, currency FROM agent.scoped_kiwoom_flows();

REVOKE ALL ON FUNCTION agent.scoped_kiwoom_flows() FROM PUBLIC;
REVOKE ALL ON agent.cash_transactions FROM PUBLIC;
GRANT EXECUTE ON FUNCTION agent.scoped_kiwoom_flows() TO agent_reader;
GRANT SELECT ON agent.cash_transactions TO agent_reader;
