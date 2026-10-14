-- Freeze spacey's old Purchase tables: from now on they refuse every write
-- except the mirror's and --repair's. The hub keeps the trigger installed
-- (purchase/schema.py, PUR-025); this only sets its switch, so any login in
-- team_purchase may run it. Also the first step of a rollback. Safe to run
-- twice.
BEGIN;
-- Waits for the writes in flight, so none commits after the freeze. bookings
-- first, as a booking takes them (then its keys in spaces and users), or the
-- two deadlock.
LOCK TABLE bookings, spaces, users IN EXCLUSIVE MODE;
UPDATE purchase_switch SET state = 'frozen';
COMMIT;
