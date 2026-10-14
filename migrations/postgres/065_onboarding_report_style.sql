-- A composite opened in the portfolio builder can report in the Alpha World MS
-- or Alpha World T design, under its own name, description and composition
-- tree. The case records the owner's choice; approval 1 writes the report
-- profile into the portfolio's platform subject.
--
-- Additive only: one nullable column on the onboarding cases table from 064.

ALTER TABLE broker_onboarding_cases
    ADD COLUMN report_style TEXT CHECK (report_style IN ('alpha-world-ms', 'alpha-world-t'));

ALTER TABLE broker_onboarding_cases
    ADD CONSTRAINT broker_onboarding_report_style_composite CHECK (
        report_style IS NULL
        OR (source_kind = 'composite_portfolio' AND reporting_currency = 'HKD')
    );

COMMENT ON COLUMN broker_onboarding_cases.report_style IS
    'Report design for an HKD fixed-unit composite: alpha-world-ms or alpha-world-t. NULL uses the standard design.';
