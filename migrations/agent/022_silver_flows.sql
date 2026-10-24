-- Deposits and withdrawals from silver for the agents' flow reader (Medallion S6, FLOWS_SOURCE).
--
-- Applied after 021_certified_latest.sql by scripts/install_agent_schema.py, on purpose and with
-- approval. Idempotent and additive: no existing grant changes and nothing is written.
--
-- The tool server reads external flows with provisional_benchmark._external_flows and
-- search_path = agent, so with FLOWS_SOURCE set (ibkr_etl.flows_source) its silver reader looks
-- for cash_transactions and transfers there. Until now the agent schema had only 020's Kiwoom
-- rows, so fallback logged silver_unreadable on every call and kept bronze (agent.flex_rows).
-- This gives that reader what it reads, for the accounts in scope only:
--
--   agent.cash_transactions  widened from 020: the four columns the silver reader also selects
--                            (settle_date, event_datetime, description, source_key), and the
--                            rows that can be a deposit or withdrawal: a Kiwoom DEPOSIT or
--                            WITHDRAWAL, an IBKR type naming deposits or withdrawals, or a row
--                            without a type whose description names one. A superset of what
--                            provisional_benchmark._is_external_cash_transaction keeps, which
--                            still decides each row; dividends, fees and trades stay out.
--   agent.transfers          IBKR's Transfers records (silver.transfers, migration 084): the
--                            columns the reader uses. Skipped where 084 is not applied yet; a
--                            later run installs it, and until then on reads bronze and says so.
--
-- With FLOWS_SOURCE unset (off) nothing reads either view's new part: the bronze reader reads
-- agent.flex_rows, and a Kiwoom account's DEPOSIT and WITHDRAWAL rows exactly as before.

CREATE OR REPLACE FUNCTION agent.scoped_cash_flows()
RETURNS TABLE (account_id text, effective_date text, transaction_type text, amount_text text, currency text,
               settle_date text, event_datetime text, description text, source_key text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT c.account_id, c.effective_date, c.transaction_type, c.amount_text, c.currency,
           c.settle_date, c.event_datetime, c.description, c.source_key
      FROM public.cash_transactions c
      JOIN public.accounts a ON a.account_id = c.account_id AND a.tenant_id = agent.tenant()
     WHERE c.account_id = ANY(agent.scope())
       AND ((a.account_type = 'kiwoom' AND c.transaction_type IN ('DEPOSIT', 'WITHDRAWAL'))
            OR upper(c.transaction_type) LIKE '%DEPOSIT%'
            OR upper(c.transaction_type) LIKE '%WITHDRAW%'
            OR (COALESCE(c.transaction_type, '') = ''
                AND upper(c.description) ~ '(ELECTRONIC FUND TRANSFER|CASH RECEIPT|DISBURSEMENT|WIRE (IN|OUT|TRANSFER)|DEPOSIT|WITHDRAWAL)'))
$$;

-- Columns can be added to a view, never removed or reordered: 020's five come first.
CREATE OR REPLACE VIEW agent.cash_transactions AS
    SELECT account_id, effective_date, transaction_type, amount_text, currency,
           settle_date, event_datetime, description, source_key
      FROM agent.scoped_cash_flows();

REVOKE ALL ON FUNCTION agent.scoped_cash_flows() FROM PUBLIC;
REVOKE ALL ON agent.cash_transactions FROM PUBLIC;
GRANT EXECUTE ON FUNCTION agent.scoped_cash_flows() TO agent_reader;
GRANT SELECT ON agent.cash_transactions TO agent_reader;

DO $transfers$
BEGIN
    IF to_regclass('public.transfers') IS NULL THEN
        RAISE NOTICE 'public.transfers (084) is not installed yet; agent.transfers is skipped';
        RETURN;
    END IF;

    EXECUTE $sql$
    CREATE OR REPLACE FUNCTION agent.scoped_transfers()
    RETURNS TABLE (account_id text, source_key text, transfer_date text, report_date text, asset_category text,
                   currency text, cash_transfer_text text, position_amount_text text,
                   position_amount_in_base_text text)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $body$
        SELECT t.account_id, t.source_key, t.transfer_date, t.report_date, t.asset_category, t.currency,
               t.cash_transfer_text, t.position_amount_text, t.position_amount_in_base_text
          FROM public.transfers t
          JOIN public.accounts a ON a.account_id = t.account_id AND a.tenant_id = agent.tenant()
         WHERE t.account_id = ANY(agent.scope())
    $body$
    $sql$;

    EXECUTE 'CREATE OR REPLACE VIEW agent.transfers AS SELECT * FROM agent.scoped_transfers()';

    EXECUTE 'REVOKE ALL ON FUNCTION agent.scoped_transfers() FROM PUBLIC';
    EXECUTE 'REVOKE ALL ON agent.transfers FROM PUBLIC';
    EXECUTE 'GRANT EXECUTE ON FUNCTION agent.scoped_transfers() TO agent_reader';
    EXECUTE 'GRANT SELECT ON agent.transfers TO agent_reader';
END
$transfers$;
