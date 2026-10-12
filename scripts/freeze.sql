-- Refuse every write to spacey's old Purchase tables, except in a transaction
-- that ran SET LOCAL purchase.mirror = 'on'. Safe to run twice.
CREATE OR REPLACE FUNCTION purchase_refuse_write() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('purchase.mirror', true) = 'on' THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'purchase: % on % refused: table frozen', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'read_only_sql_transaction';
END $$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['users', 'spaces', 'bookings'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS purchase_frozen ON %I', t);
        EXECUTE format('DROP TRIGGER IF EXISTS purchase_frozen_truncate ON %I', t);
        EXECUTE format('CREATE TRIGGER purchase_frozen BEFORE INSERT OR UPDATE OR DELETE ON %I'
                       ' FOR EACH ROW EXECUTE FUNCTION purchase_refuse_write()', t);
        EXECUTE format('CREATE TRIGGER purchase_frozen_truncate BEFORE TRUNCATE ON %I'
                       ' FOR EACH STATEMENT EXECUTE FUNCTION purchase_refuse_write()', t);
    END LOOP;
END $$;
