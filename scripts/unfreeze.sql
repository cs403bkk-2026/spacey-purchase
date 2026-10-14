-- Unfreeze: the old tables take every write again, and the hub runs the old
-- SQL. Refused while the switch is on: a rollback runs freeze.sql and
-- --repair twice first. Safe to run twice.
DO $$
BEGIN
    UPDATE purchase_switch SET state = 'off' WHERE state <> 'on';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'refused: the switch is on (run freeze.sql, then --repair twice)';
    END IF;
END $$;
