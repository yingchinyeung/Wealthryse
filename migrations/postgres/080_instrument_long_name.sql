-- Yahoo's English company name next to each classification (instrument_classifications.long_name).
--
-- Kiwoom names a holding in Korean, which the factsheet fonts cannot draw, so a KRX holding's
-- factsheet name is the name Yahoo gives with its sector and industry. The sector-classification
-- job fills it; rows classified before this migration are looked up again once.
--
-- Additive: one nullable column. The table is in the silver schema behind a compatibility view
-- in public, which fixes its column list when created (070), so the view is re-created over the
-- same table with the new column at the end; CREATE OR REPLACE keeps its owner and grants.
-- A database built without the layer moves (the unlayered build in tests/test_medallion_layers_db.py,
-- or after scripts/medallion_layers.py undo-sql silver) still has the table in public, so the column
-- goes there instead. Safe to run again. Undo: re-create the view without the column, then drop it.
SET LOCAL lock_timeout = '5s';
DO $$
BEGIN
    IF to_regclass('silver.instrument_classifications') IS NOT NULL THEN
        ALTER TABLE silver.instrument_classifications ADD COLUMN IF NOT EXISTS long_name text;
        CREATE OR REPLACE VIEW public.instrument_classifications WITH (security_invoker = true) AS
            SELECT * FROM silver.instrument_classifications;
    ELSE
        ALTER TABLE public.instrument_classifications ADD COLUMN IF NOT EXISTS long_name text;
    END IF;
END;
$$;
