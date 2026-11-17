"""Owned follow-up context sent to the tool planner, with mocked model responses.

These checks cover routing input and authorization boundaries, not model accuracy.
"""
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from api import config, question_router as router, tool_planner as planner
from test_question_workflows import DB

PARENT = '11111111-1111-4111-8111-111111111111'
SETTINGS = config.LLMSettings('http://model.test/v1', 'mock-followup', '', 'json_schema')


def workflow_answer(**changes):
    answer = {
        'id': PARENT, 'question': '해답호의 2026년 11월 연료 사용량을 알려줘',
        'task': 'workflow', 'status': 'completed',
        'workflow': {
            'version': 'HAEDAP-QUESTION-WORKFLOW-1.0', 'missing': [],
            'scope': {'action': 'operations_summary',
                      'ships': [{'id': 'a', 'name': '해답호', 'version': 1, 'imo': '7422881'}],
                      'years': [], 'periods': [{'start': '2026-11-01', 'end': '2026-11-30'}]},
        },
    }
    answer.update(changes)
    return answer


def test_workflow_context_keeps_full_question_and_valid_scope_without_results():
    question = '운항 실적을 확인합니다. ' * 25 + '해답호 2026년 11월 연료 사용량'
    prior = workflow_answer(question=question, evidence=[{'text': 'private document text'}])
    prior['workflow']['results'] = [{'result': {'fuel_t': 12345}}]
    summary = planner.previous_context(prior)
    assert len(question) > 300 and summary['question'] == question
    assert summary['ships'] == ['해답호'] and summary['action'] == 'operations_summary'
    assert summary['periods'] == [{'start': '2026-11-01', 'end': '2026-11-30'}]
    assert 'results' not in summary and 'evidence' not in summary
    assert '7422881' not in json.dumps(summary) and '12345' not in json.dumps(summary)


@pytest.mark.parametrize('sample,projected', [(False, False), (True, False), (True, True)])
def test_recorded_position_context_retains_identity_time_and_synthetic_basis(sample, projected):
    prior = {
        'question': '해답호의 마지막 위치가 어디야?', 'task': 'position', 'status': 'completed',
        'position': {'ship': {'id': 'a', 'name': '해답호', 'imo': '7422881', 'version': 1},
                     'recordId': 'private-record', 'version': 9, 'date': '2026-11-10',
                     'asOf': '2026-11-11T00:00:00Z', 'position': '27.1, 34.0',
                     'sample': sample, 'projected': projected, 'note': 'private raw record note'},
        'routing': {'toolCalls': [{'name': 'search_documents'}, {'name': 'get_last_recorded_position'}]},
    }
    summary = planner.previous_context(prior)
    assert summary['ships'] == ['해답호']
    assert summary['position'] == {'ship': '해답호', 'date': '2026-11-10', 'asOf': '2026-11-11T00:00:00Z',
                                   'sample': sample, 'projected': projected, 'realtime': False}
    assert summary['tools'] == ['search_documents', 'get_last_recorded_position']
    encoded = json.dumps(summary, ensure_ascii=False)
    assert all(secret not in encoded for secret in ('private-record', '27.1, 34.0', 'private raw record note', '7422881'))


@pytest.mark.parametrize('tool', ['summarize_operations', 'request_clarification'])
def test_ship_clarification_keeps_original_request_separate_from_latest_reply(tool):
    prior = {
        'question': '해답2호.', 'task': 'clarification', 'status': 'needs_input',
        'continuation': {'question': '2026년 11월 10일의 연료 사용량을 알려줘',
                         'tool': tool, 'missing': ['ships']},
    }
    summary = planner.previous_context(prior)
    assert summary['question'] == '해답2호.'
    assert summary['request'] == '2026년 11월 10일의 연료 사용량을 알려줘'
    assert summary['missing'] == ['ships']
    assert summary['clarification'] == {'missing': ['ships'], 'tool': tool}


def test_workflow_clarification_exposes_only_next_prompt_and_missing_slots():
    prior = workflow_answer(status='needs_input')
    prior['workflow'].update(missing=['ships'], clarification={'field': 'ships', 'question': '어느 선박인가요?'},
                             options={'ships': [{'id': 'secret-candidate', 'name': 'unselected ship'}]})
    summary = planner.previous_context(prior)
    assert summary['clarification'] == {'field': 'ships', 'question': '어느 선박인가요?', 'missing': ['ships']}
    assert 'secret-candidate' not in json.dumps(summary)
    assert 'unselected ship' not in json.dumps(summary)


def test_legacy_clarification_retains_missing_slots_from_validated_call():
    prior = {'question': '연료를 얼마나 사용했어?', 'task': 'clarification', 'status': 'needs_input',
             'routing': {'toolCalls': [{'name': 'request_clarification',
                                       'arguments': {'missing': ['ships', 'periods'], 'secret': 'not forwarded'}}]}}
    summary = planner.previous_context(prior)
    assert summary['missing'] == ['ships', 'periods']
    assert summary['clarification']['missing'] == ['ships', 'periods']
    assert 'secret' not in json.dumps(summary)


def test_completed_answer_does_not_reactivate_a_stale_clarification_or_consent():
    prior = workflow_answer(continuation={'question': 'old request', 'tool': 'calculate_mrv_reference',
                                         'missing': ['reference_ack'], 'reference_ack': True})
    summary = planner.previous_context(prior)
    assert 'request' not in summary and 'clarification' not in summary
    assert 'reference_ack' not in json.dumps(summary)


def test_context_is_bounded_and_does_not_copy_arbitrary_saved_fields():
    prior = workflow_answer(question='q' * 10000, status='needs_input',
                            continuation={'question': 'r' * 10000, 'tool': 'summarize_operations',
                                          'missing': ['ships'] * 1000},
                            transcript=[{'content': 'not sent'}] * 1000)
    prior['workflow']['scope'].update(ships=[{'name': 's' * 1000, 'id': 'not sent'}] * 1000,
                                      years=list(range(1900, 2101)),
                                      periods=[{'start': '2026-11-01', 'end': '2026-11-30'}] * 1000)
    summary = planner.previous_context(prior)
    assert len(summary['question']) == len(summary['request']) == 2000
    assert len(summary['ships']) == 2 and all(len(name) == 100 for name in summary['ships'])
    assert len(summary['years']) == 3 and len(summary['periods']) == 2
    assert summary['missing'] == ['ships'] and 'transcript' not in summary
    assert len(json.dumps(summary)) < 6000


@pytest.mark.parametrize('prior', [None, [], {'workflow': [], 'position': [], 'routing': []},
    {'task': [], 'dataStatus': {'datasets': [{'dataset': []}]},
     'routing': {'toolCalls': [{'name': []}]}, 'status': 'needs_input',
     'continuation': {'tool': [], 'missing': [[]]},
     'workflow': {'clarification': {'field': []}}}])
def test_malformed_optional_metadata_does_not_become_planner_input(prior):
    summary = planner.previous_context(prior)
    assert isinstance(summary, dict)
    json.dumps(summary)


class Client:
    def __init__(self):
        self.body = None

    def post(self, url, json=None, **kwargs):
        self.body = json
        payload = {'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
                    'tool_calls': [{'type': 'function', 'function': {'name': 'get_last_recorded_position',
                                   'arguments': '{"ship_mentions":["해답호"]}'}}]}}]}
        return httpx.Response(200, json=payload, request=httpx.Request('POST', url))


@pytest.mark.parametrize('question', ['방금 위치는 언제 기록된 거야?', '그 위치는 실제야 가상이야?'])
def test_planner_receives_position_context_with_latest_question_in_one_bounded_turn(question):
    summary = planner.previous_context({'question': '해답호 위치는?', 'task': 'position',
        'position': {'ship': {'name': '해답호'}, 'date': '2026-11-10', 'asOf': '2026-11-11T00:00:00Z',
                     'sample': True, 'projected': True}})
    client = Client()
    calls = planner.plan(question, today='2026-11-17', summary=summary, settings=SETTINGS, client=client)
    assert calls[0].tool.name == 'get_last_recorded_position'
    assert len(client.body['messages']) == 2
    user = json.loads(client.body['messages'][1]['content'])
    assert user['question'] == question and user['previous'] == summary
    assert user['previous']['ships'] == ['해답호'] and user['previous']['position']['realtime'] is False


def context_for(db, prior, *, owner='crew-1', conversation=None):
    db.answers[PARENT] = deepcopy(prior)
    db.owners[PARENT] = owner
    body = {'question': '그럼 11월 10일은?', 'context': {'previousQueryId': PARENT}}
    if conversation is not None:
        body['conversationId'] = conversation
    return SimpleNamespace(db=db, user={'id': 'crew-1'}, body=body)


def test_router_supplies_the_owned_position_summary():
    db = DB()
    prior = {'id': PARENT, 'question': '해답호 위치는?', 'task': 'position', 'status': 'completed',
             'position': {'ship': deepcopy(db.fleet[0]), 'date': '2026-11-10',
                          'asOf': '2026-11-11T00:00:00Z', 'sample': True, 'projected': True}}
    summary = router.previous_summary(context_for(db, prior))
    assert summary['ships'] == ['해답호'] and summary['position']['date'] == '2026-11-10'


@pytest.mark.parametrize('change', ['owner', 'conversation'])
def test_router_never_supplies_someone_elses_or_another_conversations_context(change):
    db = DB()
    ctx = context_for(db, workflow_answer(), owner='someone-else' if change == 'owner' else 'crew-1',
                      conversation='different-conversation' if change == 'conversation' else None)
    assert router.previous_summary(ctx) is None


def test_navigation_and_dataset_context_keep_existing_planner_fields():
    summary = planner.previous_context({'question': '해답호의 항만과 자료를 비교해줘',
        'navigation': {'kind': 'port-comparison', 'ship': {'id': 'a', 'name': '해답호'},
                       'ports': [{'name': '인천항', 'id': 'private-port'}]},
        'dataStatus': {'datasets': [{'dataset': 'operations', 'row_count': 123}, {'dataset': 'mrv'}]}})
    assert summary['navigation'] == 'port-comparison' and summary['ship'] == '해답호'
    assert summary['ports'] == ['인천항'] and summary['datasets'] == ['operations', 'mrv']
    assert 'private-port' not in json.dumps(summary) and 'row_count' not in json.dumps(summary)


@pytest.mark.parametrize('change', ['ship_version', 'selected_ship', 'document_access'])
def test_router_drops_previously_owned_workflow_scope_when_it_no_longer_applies(change):
    db = DB()
    prior = workflow_answer()
    prior['workflow']['scope']['document_ids'] = ['doc-a']
    ctx = context_for(db, prior)
    if change == 'ship_version':
        db.fleet[0]['version'] += 1
    elif change == 'selected_ship':
        ctx.body['context']['ship'] = 'b'
    else:
        db.allowed.remove('doc-a')
    assert router.previous_summary(ctx) is None
