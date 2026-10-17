"""Deterministic cache lifetime, capacity, and thread coordination contracts."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest

from src.services.tenant_provisioning_cache import TenantProvisioningCache
from src.services import user_service


@pytest.fixture
def cache(monkeypatch):
    monkeypatch.setenv('USER_TENANT_CACHE_MAX_ENTRIES', '2')
    monkeypatch.setenv('USER_TENANT_CACHE_TTL_SECONDS', '10')
    clock = [0.0]
    return TenantProvisioningCache(lambda: clock[0]), clock


def test_expiry_from_completed_success_and_lru_eviction(cache):
    cache, clock = cache
    calls = Mock(return_value=True)
    assert cache.ensure('a', calls)
    clock[0] = 9
    assert cache.ensure('a', calls)
    assert calls.call_count == 1
    clock[0] = 10
    assert cache.ensure('a', calls)
    assert calls.call_count == 2
    cache.ensure('b', calls)
    cache.ensure('a', calls)
    cache.ensure('c', calls)
    cache.ensure('b', calls)
    assert calls.call_count == 5
    assert len(cache._success) == 2


def test_failure_retries_and_exception_releases_owner(cache):
    cache, _ = cache
    calls = Mock(side_effect=[False, RuntimeError('unavailable'), True])
    assert not cache.ensure('a', calls)
    with pytest.raises(RuntimeError):
        cache.ensure('a', calls)
    assert not cache._flights and not cache._success
    assert cache.ensure('a', calls)
    assert cache.ensure('a', calls)
    assert calls.call_count == 3


@pytest.mark.parametrize("outcome", [True, False])
def test_same_key_coalesces_while_other_key_progresses(cache, monkeypatch, outcome):
    cache, _ = cache
    entered, release, waiting = Event(), Event(), Event()
    wait_for = cache._condition.wait_for
    def observed_wait(predicate):
        waiting.set()
        return wait_for(predicate)
    monkeypatch.setattr(cache._condition, 'wait_for', observed_wait)
    def slow():
        entered.set()
        assert release.wait(5)
        return outcome
    with ThreadPoolExecutor(3) as pool:
        owner = pool.submit(cache.ensure, 'a', slow)
        assert entered.wait(5)
        waiter = pool.submit(cache.ensure, 'a', lambda: pytest.fail('second owner'))
        assert waiting.wait(5)
        assert pool.submit(cache.ensure, 'b', lambda: True).result(5)
        release.set()
        assert owner.result(5) is outcome
        assert waiter.result(5) is outcome
    assert not cache._flights


def test_capacity_bounds_inflight_and_invalidation_discards_completion(cache, monkeypatch):
    cache, clock = cache
    monkeypatch.setenv('USER_TENANT_CACHE_MAX_ENTRIES', '1')
    entered, release, waiting = Event(), Event(), Event()
    wait = cache._condition.wait
    def observed_wait(*args, **kwargs):
        waiting.set()
        return wait(*args, **kwargs)
    monkeypatch.setattr(cache._condition, 'wait', observed_wait)
    def slow():
        entered.set()
        assert release.wait(5)
        clock[0] = 50
        return True
    with ThreadPoolExecutor(2) as pool:
        owner = pool.submit(cache.ensure, 'a', slow)
        assert entered.wait(5)
        other = pool.submit(cache.ensure, 'b', lambda: True)
        assert waiting.wait(5)
        assert len(cache._flights) == 1
        cache.clear()
        release.set()
        assert owner.result(5) and other.result(5)
    assert 'a' not in cache._success
    assert cache._success['b'] == 60
    assert not cache._flights


def test_partial_failure_retries_both_collections_and_scrubs_reporting(monkeypatch):
    from contextlib import contextmanager
    cache = TenantProvisioningCache()
    monkeypatch.setattr(user_service, 'tenant_provisioning_cache', cache)
    created = []
    fail = [True]
    class Collection:
        def __init__(self, name):
            self.name = name
            self.tenants = self
        def create(self, tenant):
            created.append((self.name, tenant.name))
            if self.name == 'PDFDocument' and fail[0]:
                raise RuntimeError('secret-token curator@example.org document-content')
    class Connection:
        @contextmanager
        def session(self):
            yield type('Client', (), {'collections': type('Collections', (), {'get': staticmethod(Collection)})})
    conn = Connection()
    monkeypatch.setattr(user_service, 'get_connection', lambda: conn)
    report = Mock()
    monkeypatch.setattr(user_service, 'report_runtime_exception', report)
    assert not user_service.provision_weaviate_tenants('private-subject')
    error = report.call_args.args[0]
    assert str(error) == 'Weaviate tenant provisioning failed'
    assert error.__cause__ is None and error.__context__ is None
    assert 'private-subject' not in str(report.call_args)
    fail[0] = False
    assert user_service.provision_weaviate_tenants('private-subject')
    assert user_service.provision_weaviate_tenants('private-subject')
    assert len(created) == 4
    assert user_service.provision_weaviate_tenants('different-subject')
    assert len(created) == 6
    conn = Connection()
    assert user_service.provision_weaviate_tenants('private-subject')
    assert len(created) == 8


def test_reset_waiter_retries_without_duplicate_active_owners(cache, monkeypatch):
    cache, _ = cache
    entered, release, waiting = Event(), Event(), Event()
    wait_for = cache._condition.wait_for
    def observed_wait(predicate):
        waiting.set()
        return wait_for(predicate)
    monkeypatch.setattr(cache._condition, 'wait_for', observed_wait)
    def slow():
        entered.set()
        assert release.wait(5)
        return True
    retried = Mock(return_value=True)
    with ThreadPoolExecutor(2) as pool:
        owner = pool.submit(cache.ensure, 'a', slow)
        assert entered.wait(5)
        waiter = pool.submit(cache.ensure, 'a', retried)
        assert waiting.wait(5)
        cache.clear()
        assert len(cache._flights) == 1
        release.set()
        assert owner.result(5) and waiter.result(5)
    retried.assert_called_once()
    assert len(cache._success) == 1 and not cache._flights


def test_zero_ttl_and_runtime_capacity_reduction(cache, monkeypatch):
    cache, _ = cache
    calls = Mock(return_value=True)
    for key in ('a', 'b'):
        cache.ensure(key, calls)
    monkeypatch.setenv('USER_TENANT_CACHE_MAX_ENTRIES', '1')
    cache.ensure('b', calls)
    assert len(cache._success) == 1
    cache.clear()
    monkeypatch.setenv('USER_TENANT_CACHE_TTL_SECONDS', '0')
    cache.ensure('a', calls)
    cache.ensure('a', calls)
    assert calls.call_count == 4


def test_connection_lifecycle_invalidates_success(monkeypatch):
    from src.lib.weaviate_client import connection
    from src.services import tenant_provisioning_cache as cache_module
    cache = TenantProvisioningCache()
    monkeypatch.setattr(cache_module, 'tenant_provisioning_cache', cache)
    first, second = Mock(), Mock()
    monkeypatch.setattr(connection, '_connection', first)
    cache.ensure('a', lambda: True)
    connection.set_connection(second)
    assert not cache._success
    cache.ensure('a', lambda: True)
    connection.close_connection()
    second.disconnect.assert_called_once()
    assert not cache._success


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_strand_owner(cache, monkeypatch):
    import asyncio
    from starlette.concurrency import run_in_threadpool
    cache, _ = cache
    loop = asyncio.get_running_loop()
    entered, waiting, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
    release = Event()
    wait_for = cache._condition.wait_for
    def observed_wait(predicate):
        loop.call_soon_threadsafe(waiting.set)
        result = wait_for(predicate)
        loop.call_soon_threadsafe(drained.set)
        return result
    monkeypatch.setattr(cache._condition, 'wait_for', observed_wait)
    def slow():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return True
    owner = asyncio.create_task(run_in_threadpool(cache.ensure, 'a', slow))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        waiter = asyncio.create_task(run_in_threadpool(cache.ensure, 'a', lambda: pytest.fail('second owner')))
        await asyncio.wait_for(waiting.wait(), 5)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        assert await owner
        await asyncio.wait_for(drained.wait(), 5)
        assert not cache._flights
        assert cache.ensure('a', lambda: pytest.fail('lost success'))
    finally:
        release.set()
