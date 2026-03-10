.PHONY: init-db create-admin ingest ingest-dry import-whmcs import-whmcs-dry help

help:
	@echo "Support AI Assistant - Commands"
	@echo "  make init-db       - Create database and run migrations"
	@echo "  make create-admin  - Create initial admin user (after init-db)"
	@echo "  make ingest       - Ingest docs from source/ into database"
	@echo "  make ingest-dry   - Dry run: load docs without ingesting"
	@echo "  make import-whmcs - Import WHMCS tickets+replies from source/*.sql (use with docker compose exec api)"
	@echo "  make import-whmcs-dry - Dry run: validate SQL parsing without DB insert"

init-db:
	@python scripts/init_db.py

create-admin:
	@python -m scripts.create_admin_user

ingest:
	@python scripts/ingest_from_source.py

ingest-dry:
	@python scripts/ingest_from_source.py --dry-run

import-whmcs:
	@python scripts/import_whmcs_sql_dump_to_tickets.py --replace --batch-size 2000

import-whmcs-dry:
	@python scripts/import_whmcs_sql_dump_to_tickets.py --dry-run
