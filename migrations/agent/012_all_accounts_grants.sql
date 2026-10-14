-- A scheduled job can cover every account, including accounts onboarded later.
--
-- Applied after 011_connection_recipe.sql by scripts/install_agent_schema.py,
-- on purpose and with approval. Idempotent.
--
-- Until now each job covered only the accounts the installer named one by
-- one, so a newly onboarded account got no weekly brief or live alert until
-- someone reran the installer for it. A grant whose subject_id is '*'
-- (installer: --grant <job>=all) now covers every active account and
-- portfolio of the tenant (not benchmarks), as each one goes active. Grants
-- are still added by the installer only; the agent cannot add one.

CREATE OR REPLACE FUNCTION agent.service_subjects(p_job_name text) RETURNS SETOF text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT s.subject_id FROM public.platform_subjects s
     WHERE s.tenant_id = agent.tenant() AND s.lifecycle_status = 'active'
       AND (EXISTS (SELECT 1 FROM agent.service_grants g
                     WHERE g.tenant_id = s.tenant_id AND g.job_name = p_job_name AND g.subject_id = s.subject_id)
            OR (s.subject_type <> 'benchmark'
                AND EXISTS (SELECT 1 FROM agent.service_grants g
                             WHERE g.tenant_id = s.tenant_id AND g.job_name = p_job_name AND g.subject_id = '*')))
     ORDER BY s.subject_id
$$;
