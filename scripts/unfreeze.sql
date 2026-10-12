-- Remove the freeze that freeze.sql adds, so the old tables take every write
-- again. Safe to run twice.
DROP TRIGGER IF EXISTS purchase_frozen ON users;
DROP TRIGGER IF EXISTS purchase_frozen_truncate ON users;
DROP TRIGGER IF EXISTS purchase_frozen ON spaces;
DROP TRIGGER IF EXISTS purchase_frozen_truncate ON spaces;
DROP TRIGGER IF EXISTS purchase_frozen ON bookings;
DROP TRIGGER IF EXISTS purchase_frozen_truncate ON bookings;
DROP FUNCTION IF EXISTS purchase_refuse_write();
