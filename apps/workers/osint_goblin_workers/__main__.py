"""Day-1 placeholder Dramatiq actor module. Replace with the real
tool_runner actor (MANUFACTURING-PLAN sec1, apps/workers per Sora sec3.1)
once Sprint-1 lands evidence_pipeline."""
import dramatiq
from dramatiq.brokers.redis import RedisBroker

# This module is intentionally importable as `python -m osint_goblin_workers`
# so the dramatiq CLI can do `dramatiq osint_goblin_workers` against it.
broker = RedisBroker(url="redis://127.0.0.1:6379/0")
dramatiq.set_broker(broker)


@dramatiq.actor
def heartbeat() -> str:
    """Day-1 sentinel. Replace with the real tool_runner."""
    return "ok"