# Migrations

Use ordinary Django migrations for model changes. `TypedField` has dedicated
tests for reconstruction, schema changes, reversal, and ORM round trips on SQLite
and PostgreSQL. The [demo](quickstart.md) includes a small real migration.

Names in `nova.db.zero_downtime` do not guarantee a lock-free deployment.
Migration behavior depends on PostgreSQL, the operation, transaction boundaries,
and concurrent traffic.

`CreateIndexConcurrently` emits PostgreSQL `CREATE INDEX CONCURRENTLY`. PostgreSQL
does not allow that command inside a transaction block, so its migration must be
non-atomic. Its current reverse SQL is an ordinary `DROP INDEX`; reverse behavior
must be reviewed independently. Consult the
[PostgreSQL CREATE INDEX reference](https://www.postgresql.org/docs/16/sql-createindex.html).

`AddFieldConcurrently` is experimental. Its PostgreSQL path issues an `ALTER TABLE
ADD COLUMN` statement directly. That statement takes a table lock, and the helper
does not reproduce the complete field-definition behavior of Django's schema
editor. Prefer standard Django migrations when you need those guarantees. See the
[PostgreSQL ALTER TABLE reference](https://www.postgresql.org/docs/16/sql-altertable.html).

Test forward and reverse operations on a disposable PostgreSQL database before
using a migration on application data. A successful SQLite test does not verify
PostgreSQL locking behavior.

See the [database API](api/db.md).
