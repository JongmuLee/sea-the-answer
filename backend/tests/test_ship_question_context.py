"""Recorded dates and owner-bound follow-ups, with fixture model replies."""
from datetime import date

import pytest

from api import question_intent as intent, question_router as router
from api.ship_context import calendar_defaults
from test_tool_selection import PositionStore, Store, ask, ask_position, sim_record


def again(store, question, calls, monkeypatch, parent, **body):
    monkeypatch.setattr('test_tool_selection.Store', lambda: store)
    return ask(question, calls, monkeypatch,
               body={'conversationId':parent, 'context':{'previousQueryId':parent}, **body})[0]


def test_historical_position_uses_the_requested_day_not_the_latest(monkeypatch):
    records=[sim_record('2026-11-09','1, 2'),sim_record('2026-11-10','3, 4',projected=True)]
    answer,store=ask_position('해답호는 2026년 11월 9일에 어디에 있었어?',
        [('get_last_recorded_position',{'ship_mentions':['해답호']})],monkeypatch,records)
    assert answer['position']['date']=='2026-11-09'
    assert answer['position']['position']=='1, 2' and answer['position']['selection']=='date'
    assert store.position_calls==[('a','2026-11-09')]
    assert '2026-11-09 기록 위치' in answer['notice'] and '마지막 기록 위치' not in answer['notice']


def test_missing_exact_date_never_uses_an_older_position(monkeypatch):
    answer,_=ask_position('해답호 2026년 11월 8일 위치',
        [('get_last_recorded_position',{'ship_mentions':['해답호']})],monkeypatch,
        [sim_record('2026-11-07','1, 2'),sim_record('2026-11-09','3, 4')])
    assert answer['status']=='insufficient_evidence' and 'position' not in answer
    assert '2026-11-08' in answer['notice']


@pytest.mark.parametrize('question',[
    '해답호 2026년 11월 9일부터 10일까지 위치',
    '해답호 2026년 11월 위치',
    '해답호 2026년 11월 31일 위치',
    '해답호 2026년 11월 10일부터 9일까지 위치',
])
def test_position_requires_one_valid_date(question,monkeypatch):
    answer,_=ask_position(question,[('get_last_recorded_position',{'ship_mentions':['해답호']})],
                         monkeypatch,[sim_record('2026-11-09','1, 2')])
    assert answer['status']=='needs_input' and 'position' not in answer
    assert 'day is out of range' not in answer['notice']


@pytest.mark.parametrize('day',['2026-11-11','2026-11-12'])
def test_today_unfinished_and_future_utc_snapshots_are_not_positions(day,monkeypatch):
    answer,_=ask_position(f'해답호 {day} 위치',
        [('get_last_recorded_position',{'ship_mentions':['해답호']})],monkeypatch,
        [sim_record('2026-11-10','1, 2'),sim_record(day,'3, 4',projected=True)])
    assert answer['status']=='insufficient_evidence' and 'position' not in answer


def test_past_projection_is_still_marked_as_projection(monkeypatch):
    answer,_=ask_position('해답호 2026년 11월 9일 위치',
        [('get_last_recorded_position',{'ship_mentions':['해답호']})],monkeypatch,
        [sim_record('2026-11-09','1, 2',projected=True)])
    assert answer['position']['projected'] and answer['position']['sample']
    assert answer['position']['asOf']=='2026-11-10T00:00:00Z'
    assert 'PROJECTED_OPERATIONS' in answer['warnings']


def test_ordinary_position_does_not_invent_a_precise_timestamp(monkeypatch):
    row={**sim_record('2026-11-09','1, 2'),'note':'직접 기록'}
    answer,_=ask_position('해답호 2026년 11월 9일 위치',
        [('get_last_recorded_position',{'ship_mentions':['해답호']})],monkeypatch,[row])
    assert answer['position']['asOf'] is None


def test_position_followup_requeries_the_same_record_date(monkeypatch):
    first,store=ask_position('해답호 2026년 11월 9일 위치',
        [('get_last_recorded_position',{'ship_mentions':['해답호']})],monkeypatch,
        [sim_record('2026-11-09','1, 2')])
    store.records.append(sim_record('2026-11-10','3, 4'))
    answer=again(store,'방금 말한 위치는 언제 기록된 거야?',
                 [('get_last_recorded_position',{})],monkeypatch,first['id'])
    assert answer['position']['date']=='2026-11-09' and answer['position']['position']=='1, 2'
    assert store.position_calls[-1]==('a','2026-11-09')


@pytest.mark.parametrize('change',['owner','conversation','ship_version','permission'])
def test_position_context_is_not_reused_after_scope_or_permission_changes(change,monkeypatch):
    first,store=ask_position('해답호 마지막 위치',
        [('get_last_recorded_position',{'ship_mentions':['해답호']})],monkeypatch,
        [sim_record('2026-11-09','1, 2')])
    body={}
    if change=='owner':store.owners[first['id']]='other-user'
    if change=='conversation':body['conversationId']='other-conversation'
    if change=='ship_version':store.fleet[0]['version']+=1
    if change=='permission':store.allowed.clear()
    answer=again(store,'그 위치의 기록 시각은?',
                 [('get_last_recorded_position',{})],monkeypatch,first['id'],**body)
    assert answer['status']=='needs_input' and 'position' not in answer


def test_workflow_followup_inherits_year_and_ship_but_overrides_the_day(monkeypatch):
    first,store=ask('해답호 2026년 11월 9일 연료 사용량',
        [('summarize_operations',{'ship_mentions':['해답호'],'period_text':'2026년 11월 9일'})],monkeypatch)
    answer=again(store,'그럼 11월 10일은?',
        [('summarize_operations',{'period_text':'11월 10일'})],monkeypatch,first['id'])
    scope=answer['workflow']['scope']
    assert answer['status']=='completed' and scope['ships'][0]['id']=='a'
    assert scope['periods']==[{'start':'2026-11-10','end':'2026-11-10'}]


def test_explicit_new_ship_overrides_previous_ship(monkeypatch):
    first,store=ask('해답호 2026년 11월 9일 연료',
        [('summarize_operations',{'ship_mentions':['해답호'],'period_text':'2026년 11월 9일'})],monkeypatch)
    answer=again(store,'같은 날짜에 별빛호는?',
        [('summarize_operations',{'ship_mentions':['별빛호']})],monkeypatch,first['id'])
    assert answer['workflow']['scope']['ships'][0]['id']=='b'
    assert store.calls[-1]['ship']=='b' and store.calls[-1]['from']=='2026-11-09'


def test_model_cannot_erase_an_explicit_date_by_omitting_period_text(monkeypatch):
    answer,store=ask('해답호 2026년 11월 9일 연료',
        [('summarize_operations',{'ship_mentions':['해답호']})],monkeypatch)
    assert answer['status']=='completed' and store.calls[-1]['from']=='2026-11-09'


def test_partial_calendar_defaults_are_unambiguous():
    p=lambda a,b:intent.Period(start=a,end=b)
    assert calendar_defaults([p('2026-11-09','2026-11-10')])==(2026,11)
    assert calendar_defaults([p('2025-12-31','2026-01-01')])==(None,None)
    assert calendar_defaults([p('2026-10-31','2026-11-01')])==(2026,None)
    _,days=intent.date_slots('9일',today=date(2026,11,17),default_year=2026,default_month=11)
    assert days[0].start=='2026-11-09'


def test_invalid_calendar_error_is_a_korean_prompt(monkeypatch):
    answer,_=ask('해답호 2026년 11월 31일 연료',
        [('summarize_operations',{'ship_mentions':['해답호'],'period_text':'2026년 11월 31일'})],monkeypatch)
    assert answer['status']=='needs_input' and 'day is out of range' not in answer['notice']


def test_clarification_preserves_the_original_request_without_executing(monkeypatch):
    answer,store=ask('2026년 11월 9일 연료 얼마나 썼어?',
        [('request_clarification',{'missing':['ships']})],monkeypatch)
    assert answer['continuation']=={'question':'2026년 11월 9일 연료 얼마나 썼어?',
                                  'tool':'request_clarification','missing':['ships']}
    assert not store.calls


def test_ship_only_reply_can_reuse_the_requested_date_after_clarification(monkeypatch):
    first,store=ask('2026년 11월 9일 연료 얼마나 썼어?',
        [('request_clarification',{'missing':['ships']})],monkeypatch)
    answer=again(store,'해답호.',[('summarize_operations',{'ship_mentions':['해답호']})],
                 monkeypatch,first['id'])
    assert answer['status']=='completed'
    assert store.calls[-1]['from']=='2026-11-09' and store.calls[-1]['ship']=='a'


def test_excluded_single_date_is_not_queried(monkeypatch):
    first,store=ask('해답호 2026년 11월 9일 연료',
        [('summarize_operations',{'ship_mentions':['해답호'],'period_text':'2026년 11월 9일'})],monkeypatch)
    answer=again(store,'아니, 10일은 제외하고 9일만 보여줘.',
        [('summarize_operations',{'period_text':'10일은 제외하고 9일'})],monkeypatch,first['id'])
    assert answer['status']=='completed'
    assert answer['workflow']['scope']['periods']==[{'start':'2026-11-09','end':'2026-11-09'}]


def test_date_range_with_an_excluded_day_requests_explicit_ranges(monkeypatch):
    first,store=ask('해답호 2026년 11월 9일 연료',
        [('summarize_operations',{'ship_mentions':['해답호'],'period_text':'2026년 11월 9일'})],monkeypatch)
    before=len(store.calls)
    answer=again(store,'11월 9일부터 11일까지, 10일은 제외하고 보여줘.',
        [('summarize_operations',{'period_text':'11월 9일부터 11일까지'})],monkeypatch,first['id'])
    assert answer['status']=='needs_input' and len(store.calls)==before


def test_excluding_all_dates_does_not_expand_to_a_whole_year(monkeypatch):
    answer,store=ask('해답호 2026년 11월 10일은 제외하고 연료 조회',
        [('summarize_operations',{'ship_mentions':['해답호'],'period_text':'2026년 11월 10일'})],monkeypatch)
    assert answer['status']=='needs_input' and not store.calls


def test_explicit_new_question_does_not_inherit_the_old_period(monkeypatch):
    first,store=ask('해답호 2026년 11월 9일 연료',
        [('summarize_operations',{'ship_mentions':['해답호'],'period_text':'2026년 11월 9일'})],monkeypatch)
    before=len(store.calls)
    answer=again(store,'새 질문이야. 별빛호 연료 사용량',
        [('summarize_operations',{'ship_mentions':['별빛호']})],monkeypatch,first['id'])
    assert answer['status']=='needs_input' and len(store.calls)==before
    assert answer['workflow']['missing']==['periods']


def test_repeated_clarification_keeps_the_original_task_and_date(monkeypatch):
    request='2026년 11월 9일 연료 얼마나 썼어?'
    first,store=ask(request,[('request_clarification',{'missing':['ships']})],monkeypatch)
    second=again(store,'해답호.',[('request_clarification',{'missing':['task']})],monkeypatch,first['id'])
    assert second['continuation']['question']==request
    # Route-only tests do not attach conversations as the HTTP route normally does.
    store.answers[second['id']]['conversationId']=first['id']
    answer=again(store,'해답호.',[('summarize_operations',{'ship_mentions':['해답호']})],
        monkeypatch,second['id'],conversationId=first['id'])
    assert answer['status']=='completed' and store.calls[-1]['from']=='2026-11-09'


@pytest.mark.parametrize('phrase',['11/9','2026/11/9','11.9','2026년 11월 9일 오후 3시','2026-11-09T12:00:00Z'])
def test_unavailable_date_or_clock_precision_is_not_silently_ignored(phrase,monkeypatch):
    answer,_=ask_position(f'해답호 {phrase} 위치',
        [('get_last_recorded_position',{'ship_mentions':['해답호']})],monkeypatch,
        [sim_record('2026-11-09','1, 2')])
    assert answer['status']=='needs_input' and 'position' not in answer


def test_new_explicit_date_overrides_a_model_echo_of_the_previous_period(monkeypatch):
    first,store=ask('해답호 2026년 11월 9일 연료',
        [('summarize_operations',{'ship_mentions':['해답호'],'period_text':'2026년 11월 9일'})],monkeypatch)
    answer=again(store,'그럼 11월 10일은?',
        [('summarize_operations',{'period_text':'2026년 11월 9일'})],monkeypatch,first['id'])
    assert answer['status']=='completed' and store.calls[-1]['from']=='2026-11-10'


def test_new_explicit_ship_overrides_a_model_echo_of_the_previous_ship(monkeypatch):
    first,store=ask('해답호 2026년 11월 9일 연료',
        [('summarize_operations',{'ship_mentions':['해답호'],'period_text':'2026년 11월 9일'})],monkeypatch)
    answer=again(store,'같은 날짜에 별빛호는?',
        [('summarize_operations',{'ship_mentions':['해답호']})],monkeypatch,first['id'])
    assert answer['status']=='completed' and store.calls[-1]['ship']=='b'


def test_explicit_ship_is_not_lost_when_the_model_omits_it(monkeypatch):
    answer,store=ask('해답호 2026년 11월 9일 연료',
        [('summarize_operations',{'period_text':'2026년 11월 9일'})],monkeypatch)
    assert answer['status']=='completed' and store.calls[-1]['ship']=='a'
