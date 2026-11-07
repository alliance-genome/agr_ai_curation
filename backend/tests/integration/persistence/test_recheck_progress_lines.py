"""A live review shows the re-check's paper work between its start and finish lines."""
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.lib.validation_history import store
from src.lib.validation_review import extraction_scheduling
from src.lib.validation_review.decisions import ReviewContext
from tests.integration.persistence.test_chat_review_adapter import planned as planned, scope as scope, migrated as migrated
from tests.integration.persistence.test_extraction_followup_admission import request_args


@pytest.mark.asyncio
async def test_recheck_paper_lines_arrive_in_order_through_real_execution(planned, monkeypatch):
    """Only provider and paper storage are fake; execution, tools and progress are real."""
    from agents import Agent
    from agr_ai_curation_alliance.tools import documents, weaviate_search
    from src.lib.openai_agents import document_references as refs, streaming_tools
    from src.lib.openai_agents.run_history import HistoryScope, HistoryStore
    from src.lib.openai_agents.tools import record_evidence
    from src.lib.packages.package_runner_entrypoint import _build_tool_context
    from src.lib.validation_review import extraction_execution, extractor_phase
    from src.lib.validation_review.progress import ReviewLines, review_progress

    item = planned
    doc, owner = item.scope.document_id, item.scope.owner_id
    history = HistoryStore(store.SessionLocal)
    history_scope = HistoryScope(owner, uuid4().hex, uuid4().hex, 'extractor', 'followup/', item.scope.session_id)
    binding = refs.SourceBinding(doc, owner, 'fixture-generation', history_scope, history, [])
    binding.pin_generation()

    @asynccontextmanager
    async def scoped(document, user):
        token = refs._ACTIVE_SOURCE.set(binding)
        try:
            yield
        finally:
            refs._ACTIVE_SOURCE.reset(token)
    chunk = {'id': str(uuid4()), 'chunk_index': 42, 'text': 'Methods identify the organism.',
             'document_id': doc, 'page_number': 3, 'parent_section': 'Methods', 'section_title': 'Methods',
             'metadata': {'page_number': 3, 'section_title': 'Methods'}}
    monkeypatch.setattr(refs, 'document_reference_scope', scoped)
    monkeypatch.setattr(refs, 'get_chunk_by_index', AsyncMock(return_value=chunk))
    monkeypatch.setattr(weaviate_search, 'get_chunk_by_index', AsyncMock(return_value=chunk))
    monkeypatch.setattr(weaviate_search, 'get_chunk_neighbor_ids', AsyncMock(return_value={}))
    monkeypatch.setattr(record_evidence, 'get_chunk_by_id', AsyncMock(return_value=chunk))
    monkeypatch.setattr('src.lib.weaviate_client.chunks.get_chunk_by_id', AsyncMock(return_value=chunk))
    monkeypatch.setattr('src.lib.context.get_current_user_id', lambda: owner)
    monkeypatch.setattr('src.lib.prompts.assembly.get_all_active_prompts', lambda: {})
    monkeypatch.setattr(streaming_tools, 'write_stream_event', lambda *args, **kwargs: None)
    tools = [documents.create_read_chunk_tool({'document_id': doc, 'user_id': owner}),
             documents.create_record_evidence_tool({'document_id': doc, 'user_id': owner})]
    agent = Agent(name='Saved extractor', model='test', instructions='Extract evidence', tools=tools)
    args = request_args(item)
    args['receipt'] = {'contract': extractor_phase.contract(agent, max_turns=17)}
    wid = extraction_scheduling.request(ReviewContext(item.scope, item.ep, 'supervisor'), **args)

    async def provider(phase, input_value, **kwargs):
        available = {tool.name: tool for tool in phase.tools}
        read = available['read_chunk']
        await read.on_invoke_tool(_build_tool_context(read.name, {}), '{"chunk_index":42}')
        record = available['record_evidence']
        evidence = await record.on_invoke_tool(_build_tool_context(record.name, {}), json.dumps({
            'entity': 'organism', 'selections': [{'chunk_index': 42, 'span_index': 0}]}))
        return type('Answer', (), {'final_output': {'entries': [{'entry_id': args['entry_ids'][0],
            'outcome': 'additional_information', 'assessment': 'Methods clarify the organism',
            'remaining_uncertainty': '', 'evidence_record_ids': [evidence['evidence_record_id']],
            'proposed_values': []}]}})()
    monkeypatch.setattr('src.lib.openai_agents.runner.run_agent_with_owned_openai_resources', provider)

    events = []
    streaming_tools.set_live_event_list(events)
    try:
        with review_progress(item.ep):
            lines = ReviewLines(item.ep)
            lines.start('request_targeted_extraction', {'entry_ids': args['entry_ids']})
            result = await extraction_execution.execute(item.scope, wid, resolve_agent=lambda _: agent)
            lines.finish('request_targeted_extraction', result)
    finally:
        streaming_tools.set_live_event_list(None)
    assert result['state'] == 'completed'
    assert [event['details'].get('friendlyName') for event in events if event['type'].startswith('TOOL_')] == [
        'Supervisor review: asking the extraction agent to re-check 1 record',
        'Supervisor review: extraction agent reading a passage',
        'Supervisor review: extraction agent saving evidence',
        'Supervisor review: re-check finished for 1 record',
    ]
    assert {event['details']['episodeId'] for event in events} == {item.ep}
