"""Fixture: L0 schemas package illegally importing L1 (osint_goblin_db).

Schemas is L0 (leaf). Importing osint_goblin_db (L1) is a DAG inversion — the
trunk depends on schemas, schemas does not depend on the trunk.
"""
