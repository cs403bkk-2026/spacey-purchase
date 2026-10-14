-- Switch on: with PURCHASE_URL set, the hub calls the service from its next
-- request, and the old tables stay frozen. Only after freeze.sql, --apply and
-- --verify. Safe to run twice.
DO $$
BEGIN
    UPDATE purchase_switch SET state = 'on' WHERE state <> 'off';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'refused: the old tables are not frozen (run freeze.sql, --apply and --verify first)';
    END IF;
END $$;
