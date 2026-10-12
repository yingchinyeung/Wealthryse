-- Preserve the physical source-account relationship for filtered portfolios.
-- Older website-created subjects stored this only inside the governed definition,
-- which prevented FX-backed monthly attribution from resolving the source rates.

WITH latest_definition AS (
    SELECT DISTINCT ON (tenant_id, subject_id)
           tenant_id, subject_id, configuration_json
    FROM subject_calculation_definitions
    WHERE subject_type = 'filtered_portfolio'
      AND approval_status IN ('validated', 'approved')
    ORDER BY tenant_id, subject_id, definition_version DESC, created_at DESC
)
UPDATE platform_subjects AS subject
SET source_account_id = definition.configuration_json ->> 'source_account_id',
    updated_at = now()
FROM latest_definition AS definition
WHERE subject.subject_type = 'filtered_portfolio'
  AND definition.tenant_id = subject.tenant_id
  AND definition.subject_id = subject.subject_id
  AND subject.source_account_id IS NULL
  AND NULLIF(definition.configuration_json ->> 'source_account_id', '') IS NOT NULL;
