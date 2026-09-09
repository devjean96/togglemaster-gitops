BEGIN;

DROP TRIGGER IF EXISTS flags_set_updated_at ON flags;
DROP FUNCTION IF EXISTS set_flags_updated_at();
DROP TABLE IF EXISTS flags;

COMMIT;
