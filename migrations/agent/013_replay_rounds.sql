-- Replay rounds: the month-end team can replay the released months again.
--
-- Applied after 012_all_accounts_grants.sql by scripts/install_agent_schema.py,
-- on purpose and with approval. Idempotent.
--
-- Until now each account and released month had one replay run, so once the
-- team had replayed a month it never looked at it again. A replay is how the
-- team is trained: it shows where the team is wrong, its instructions and
-- checks are tuned, and the next replay shows whether that helped. So each
-- replay now belongs to a round (1, 2, ...), every round checks every released
-- month afresh, and the Month-end page compares the rounds. The month's own
-- run keeps round 0. The replays already done become round 1. Nothing is
-- deleted, and a replay still never writes an inbox card or changes a file.

ALTER TABLE agent.month_runs ADD COLUMN IF NOT EXISTS replay_round smallint NOT NULL DEFAULT 0;
UPDATE agent.month_runs SET replay_round = 1 WHERE mode = 'replay' AND replay_round = 0;

DO $$
DECLARE
    old_key text;
BEGIN
    -- The table's original one-run-per-mode key, whatever PostgreSQL named it.
    SELECT c.conname INTO old_key
      FROM pg_catalog.pg_constraint c
     WHERE c.conrelid = 'agent.month_runs'::regclass AND c.contype = 'u'
       AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
              FROM pg_catalog.pg_attribute a
             WHERE a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey))
           = ARRAY['mode', 'period_end', 'subject_id', 'tenant_id'];
    IF old_key IS NOT NULL THEN
        EXECUTE format('ALTER TABLE agent.month_runs DROP CONSTRAINT %I', old_key);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                    WHERE conrelid = 'agent.month_runs'::regclass AND conname = 'month_runs_replay_round_check') THEN
        ALTER TABLE agent.month_runs ADD CONSTRAINT month_runs_replay_round_check
            CHECK ((mode = 'month' AND replay_round = 0) OR (mode = 'replay' AND replay_round >= 1));
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS month_runs_round_key
    ON agent.month_runs (tenant_id, subject_id, period_end, mode, replay_round);
