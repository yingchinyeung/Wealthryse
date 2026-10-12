-- U16088606 was activated before activation began enabling every governed
-- operating capability. Reconcile it to its existing owner activation approval;
-- never infer authorization for an unapproved subject.
WITH approved_subject AS (
    SELECT s.tenant_id, s.subject_id, s.history_start_date,
           a.approval_id, a.approved_by_identity_sha256
    FROM platform_subjects s
    JOIN account_onboarding_approvals a
      ON a.tenant_id = s.tenant_id AND a.subject_id = s.subject_id
     AND a.approval_type = 'owner_activation'
     AND a.approval_status = 'approved'
    WHERE s.tenant_id = app_tenant_id()
      AND s.subject_id = 'U16088606'
      AND s.lifecycle_status = 'active'
), capabilities(capability, configuration_json, configuration_sha256) AS (
    VALUES
      ('daily_ingestion', '{"start_date":"2025-01-20","source":"existing_v11"}'::jsonb,
       '1043acbd3ff44ed1b1c4041734912c2bbc559ec4bf3bf6db6bd951fcf4458041'),
      ('benchmark_refresh', '{"start_date":"2025-01-20"}'::jsonb,
       'b6bae9233bd61e5815203b181af7743e90ab653884920bfeeba2a87100524dcd'),
      ('sector_refresh', '{"external_approval_required":true}'::jsonb,
       'cb2355c983d282adcb239bc5381ab041d1fe7e04a8886e4a973f423e6798af40'),
      ('month_end_reporting', '{"approval_required":true}'::jsonb,
       '9651ac187bac82842493bea8651a30d38b07b0a418197c7ddb954b005003a533'),
      ('analyst_assurance', '{}'::jsonb,
       '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'),
      ('viewer_directory', '{}'::jsonb,
       '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a')
)
INSERT INTO subject_operating_registrations (
    registration_id, tenant_id, subject_id, capability, operating_state,
    effective_from, configuration_json, configuration_sha256,
    activation_approval_id, changed_by_identity_sha256
)
SELECT md5(s.subject_id || ':' || c.capability || ':' || s.history_start_date::text)::uuid,
       s.tenant_id, s.subject_id, c.capability, 'active',
       s.history_start_date, c.configuration_json, c.configuration_sha256,
       s.approval_id, s.approved_by_identity_sha256
FROM approved_subject s CROSS JOIN capabilities c
ON CONFLICT (tenant_id, subject_id, capability, effective_from)
DO UPDATE SET operating_state = 'active', effective_to = NULL,
              configuration_json = EXCLUDED.configuration_json,
              configuration_sha256 = EXCLUDED.configuration_sha256,
              activation_approval_id = EXCLUDED.activation_approval_id,
              changed_by_identity_sha256 = EXCLUDED.changed_by_identity_sha256,
              updated_at = now();
