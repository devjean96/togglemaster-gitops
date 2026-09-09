BEGIN;

DROP TRIGGER IF EXISTS targeting_rules_set_updated_at ON targeting_rules;
DROP FUNCTION IF EXISTS set_targeting_rules_updated_at();
DROP TABLE IF EXISTS targeting_rules;

COMMIT;
