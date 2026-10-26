"""Expected review control flow must not become an SDK-owned Sentry failure."""
import asyncio
import subprocess
import sys
import textwrap

import pytest
from agents import MaxTurnsExceeded, UserError
from sentry_sdk.utils import event_from_exception

from src.lib.observability.sentry import before_send
from src.lib.openai_agents.model_context_view import ContextRecoveryError
from src.lib.validation_history.store import HistoryConflict, HistoryScope
from src.lib.validation_review.admission import ReviewStopped
from src.lib.validation_review.provider import ExecutionBudget, budget_scope


@pytest.fixture
def budget():
    return ExecutionBudget(
        HistoryScope('owner', '00000000-0000-0000-0000-000000000001', 'session'),
        'episode', 'reviewer_tokens', True, reviewer_agent_id='reviewer',
    )


def event_for(error, mechanism='openai_agents'):
    return event_from_exception(
        error,
        mechanism={'type': mechanism, 'handled': False},
    )


@pytest.mark.parametrize('reason', ['complete', 'superseded', 'budget_exhausted'])
def test_owned_sdk_review_stop_is_not_an_error(budget, reason):
    event, hint = event_for(ReviewStopped(reason))
    with budget_scope(budget):
        assert before_send(event, hint) is None


@pytest.mark.parametrize('link', ['__cause__', '__context__'])
def test_sdk_wrappers_and_handling_failures_are_left_to_the_owner(budget, link):
    error = UserError('tool wrapper')
    setattr(error, link, ReviewStopped('complete'))
    event, hint = event_for(error)
    with budget_scope(budget):
        assert before_send(event, hint) is None
    outer = RuntimeError('database failed')
    setattr(outer, link, error)
    event, hint = event_for(outer)
    with budget_scope(budget):
        assert before_send(event, hint) is None


@pytest.mark.parametrize('error', [
    RuntimeError('complete'), HistoryConflict('complete'),
    MaxTurnsExceeded('turns'), ContextRecoveryError('capacity'),
])
def test_owned_sdk_failures_are_deferred_without_marking_captured(budget, error):
    event, hint = event_for(error)
    with budget_scope(budget):
        assert before_send(event, hint) is None
        assert not getattr(error, '_ai_curation_sentry_captured', False)


@pytest.mark.asyncio
@pytest.mark.parametrize('own_deadline', [False, True])
@pytest.mark.parametrize('link', [None, '__cause__', '__context__'])
async def test_only_review_deadline_timeout_is_quiet(budget, own_deadline, link):
    from src.lib.validation_review.provider import guarded_await

    loop = asyncio.get_running_loop()
    budget.execution.deadline = loop.time() + (0 if own_deadline else 60)

    async def provider():
        if own_deadline:
            await asyncio.Future()
        raise TimeoutError('provider transport timeout')

    with budget_scope(budget):
        with pytest.raises(TimeoutError) as raised:
            await guarded_await(provider())
        error = raised.value
        assert bool(getattr(error, '_review_deadline_expired', False)) is own_deadline
        if link:
            wrapped = UserError('tool wrapper')
            setattr(wrapped, link, error)
            error = wrapped
        event, hint = event_for(error)
        assert (before_send(event, hint) is None) is own_deadline


@pytest.mark.parametrize('mechanism', ['starlette', 'logging', 'generic'])
def test_non_sdk_reporters_remain_reportable(budget, mechanism):
    event, hint = event_for(ReviewStopped('complete'), mechanism)
    with budget_scope(budget):
        assert before_send(event, hint) is not None


def test_no_trusted_review_owner_keeps_the_event():
    event, hint = event_for(ReviewStopped('complete'))
    assert before_send(event, hint) is not None


@pytest.mark.parametrize('hint', [None, {}, {'exc_info': ()}])
def test_serialized_exception_names_cannot_suppress_events(budget, hint):
    event, _ = event_for(ReviewStopped('complete'))
    with budget_scope(budget):
        assert before_send(event, hint) is not None


def test_real_sdk_capture_preserves_failure_and_redaction():
    script = textwrap.dedent('''
        import sentry_sdk
        from sentry_sdk.integrations.openai_agents.utils import _capture_exception
        from src.lib.observability.sentry import before_send
        from src.lib.validation_review.admission import ReviewStopped
        from src.lib.validation_review.audit import report_operational_failure
        from agents import UserError
        from src.lib.validation_review.provider import ExecutionBudget, budget_scope
        from src.lib.validation_history.store import HistoryScope
        events = []
        with sentry_sdk.init(dsn='http://public@example.invalid/1', transport=events.append,
                             default_integrations=False, include_local_variables=False,
                             before_send=before_send):
            budget = ExecutionBudget(HistoryScope('owner', '00000000-0000-0000-0000-000000000001', 'session'),
                                     'episode', 'reviewer_tokens', True, reviewer_agent_id='reviewer')
            with budget_scope(budget):
                _capture_exception(ReviewStopped('complete'))
                error = RuntimeError('Authorization: Bearer synthetic-private-token')
                _capture_exception(error)
                assert not getattr(error, '_ai_curation_sentry_captured', False)
                report_operational_failure(error)
                wrapper = UserError('tool wrapper')
                wrapper.__cause__ = error
                report_operational_failure(wrapper)
            _capture_exception(error)
            sentry_sdk.flush()
        assert len(events) == 1, [(e.get('exception')) for e in events]
        assert events[0]['exception']['values'][-1]['type'] == 'RuntimeError'
        assert events[0]['tags']['runtime_component'] == 'validation_review'
        assert error._ai_curation_sentry_captured is True
        assert 'synthetic-private-token' not in str(events)
    ''')
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_owner_liveness_follows_copied_tasks_and_threads(budget):
    gate = asyncio.Event()
    async def late_child():
        await gate.wait()
        return before_send(*event_for(RuntimeError('late fault')))
    with budget_scope(budget):
        assert await asyncio.to_thread(lambda: before_send(*event_for(RuntimeError('owned')))) is None
        task = asyncio.create_task(late_child())
    gate.set()
    assert await task is not None


def test_generic_capture_is_retained_inside_nested_scope(budget):
    with budget_scope(budget):
        with budget_scope(budget):
            assert before_send(*event_for(RuntimeError('owned'), 'generic')) is not None
        assert budget.execution.capture_owner_active
    assert not budget.execution.capture_owner_active
