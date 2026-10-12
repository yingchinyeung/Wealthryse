-- Align the U16088571 website label with the platform's account-name convention.
-- This changes display metadata only; subject identity, data and report artifacts are untouched.

UPDATE platform_subjects
SET display_name = 'U16088571-U.S. Technology and Biotechnology',
    updated_at = now()
WHERE subject_id = 'U16088571'
  AND display_name IS DISTINCT FROM 'U16088571-U.S. Technology and Biotechnology';
