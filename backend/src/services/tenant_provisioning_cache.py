"""Bounded process-local evidence of completed tenant provisioning.

No authorization or ORM state is stored here. In-flight owners are retained until
completion, including across invalidation; waiters share their result.
"""
from collections import OrderedDict
from dataclasses import dataclass
from threading import Condition
from time import monotonic
from typing import Callable, Hashable

from src.lib.openai_agents.config import (
    get_user_tenant_cache_max_entries,
    get_user_tenant_cache_ttl_seconds,
)


@dataclass
class _Flight:
    generation: int
    done: bool = False
    succeeded: bool = False

    def is_done(self) -> bool:
        return self.done


class TenantProvisioningCache:
    def __init__(self, clock: Callable[[], float] = monotonic):
        self._clock = clock
        self._condition = Condition()
        self._success: OrderedDict[Hashable, float] = OrderedDict()
        self._flights: dict[Hashable, _Flight] = {}
        self._generation = 0

    def clear(self) -> None:
        """Invalidate all successes, including completions already in flight."""
        with self._condition:
            self._generation += 1
            self._success.clear()
            self._condition.notify_all()

    def ensure(self, key: Hashable, provision: Callable[[], bool]) -> bool:
        with self._condition:
            while True:
                now = self._clock()
                capacity = get_user_tenant_cache_max_entries()
                for expired in [k for k, expiry in self._success.items() if expiry <= now]:
                    del self._success[expired]
                while len(self._success) > capacity:
                    self._success.popitem(last=False)
                if key in self._success:
                    self._success.move_to_end(key)
                    return True
                flight = self._flights.get(key)
                if flight is not None:
                    self._condition.wait_for(flight.is_done)
                    # A reset cannot confer success on a waiter in the new generation.
                    if flight.generation != self._generation:
                        continue
                    return flight.succeeded
                if len(self._flights) < capacity:
                    flight = _Flight(self._generation)
                    self._flights[key] = flight
                    break
                self._condition.wait()

        succeeded = False
        try:
            succeeded = provision()
            return succeeded
        finally:
            with self._condition:
                flight.succeeded = succeeded
                flight.done = True
                del self._flights[key]
                if succeeded and flight.generation == self._generation:
                    self._success[key] = self._clock() + get_user_tenant_cache_ttl_seconds()
                    while len(self._success) > get_user_tenant_cache_max_entries():
                        self._success.popitem(last=False)
                self._condition.notify_all()


tenant_provisioning_cache = TenantProvisioningCache()


def invalidate_tenant_provisioning_cache() -> None:
    """Call after tenant/schema resets in every worker, or restart the workers."""
    tenant_provisioning_cache.clear()
