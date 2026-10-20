-- Yahoo's English company name next to each classification (instrument_classifications.long_name).
--
-- Kiwoom names a holding in Korean, which the factsheet fonts cannot draw, so a KRX holding's
-- factsheet name is the name Yahoo gives with its sector and industry. The sector-classification
-- job fills it; rows classified before this migration are looked up again once.
--
-- Additive: one nullable column. The table is in the silver schema behind a compatibility view
-- in public, which fixes its column list when created (070), so the view is re-created over the
-- same table with the new column at the end; CREATE OR REPLACE keeps its owner and grants.
-- Safe to run again. Undo: re-create the view without the column, then drop the column.
SET LOCAL lock_timeout = '5s';
ALTER TABLE silver.instrument_classifications ADD COLUMN IF NOT EXISTS long_name text;
CREATE OR REPLACE VIEW public.instrument_classifications WITH (security_invoker = true) AS
    SELECT * FROM silver.instrument_classifications;
