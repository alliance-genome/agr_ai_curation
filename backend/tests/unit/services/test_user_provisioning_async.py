"""Worker lifecycle and cooperative coroutine progress without timing assertions."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from src.services import user_service


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel,fail', [(False, False), (True, False), (False, True)])
async def test_worker_owns_session_through_completion(monkeypatch, cancel, fail):
    loop = asyncio.get_running_loop()
    entered, closed = asyncio.Event(), asyncio.Event()
    release = Event()
    ownership = []
    session = object()
    @contextmanager
    def factory():
        ownership.append(('open', get_ident()))
        try:
            yield session
        finally:
            ownership.append(('close', get_ident()))
            loop.call_soon_threadsafe(closed.set)
    def provision(db, principal):
        assert db is session
        ownership.append(('work', get_ident()))
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        if fail:
            raise ValueError('SQL provisioning failed')
        return SimpleNamespace(id=17, auth_sub=principal.subject, email=None, display_name=None,
                               created_at=datetime.now(timezone.utc), last_login=None, is_active=False)
    monkeypatch.setattr(user_service, 'SessionLocal', factory)
    monkeypatch.setattr(user_service, 'provision_user', provision)
    task = asyncio.create_task(user_service.provision_user_async(SimpleNamespace(subject='test')))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # This coroutine progresses while cold provisioning is still blocked.
        assert not task.done()
        if cancel:
            task.cancel()
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        elif fail:
            with pytest.raises(ValueError, match='SQL provisioning failed'):
                await task
        else:
            result = await task
            assert result.id == 17 and result.is_active is False
            assert result.to_dict()['last_login'] is None
            assert not hasattr(result, '_sa_instance_state')
        await asyncio.wait_for(closed.wait(), 5)
        assert [operation for operation, _ in ownership] == ['open', 'work', 'close']
        assert len({thread for _, thread in ownership}) == 1
        assert ownership[0][1] != get_ident()
    finally:
        release.set()
