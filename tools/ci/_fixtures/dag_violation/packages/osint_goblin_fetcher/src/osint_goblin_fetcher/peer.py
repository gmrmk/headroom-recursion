"""Fixture: L1 fetcher illegally importing L1 peer (osint_goblin_opsec).

L1 peers may not import each other (the §4 peer rule). The orchestrator at
L3 composes both; L1 stays independent.
"""
from osint_goblin_opsec import tor  # L1 peer rule violation
