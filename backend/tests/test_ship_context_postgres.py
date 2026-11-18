"""Opt-in authenticated HTTP/PostgreSQL regressions; planner replies are fixtures."""
from fastapi.testclient import TestClient

from api import config, main, question_router as router
from rag_postgres_support import login
from test_workflow_skills import database

registry = router.tool_planner.registry


def prepare(database, monkeypatch, calls):
    monkeypatch.setenv('HAEDAP_QUESTION_ROUTER', '')
    monkeypatch.setattr(router.config, 'llm_settings', lambda: config.LLMSettings('http://model.test/v1','fixture','','json_schema'))
    monkeypatch.setattr(type(database), 'search', lambda *args, **kwargs: [])
    replies=iter(calls)
    monkeypatch.setattr(router.tool_planner, 'plan', lambda *args, **kwargs:
        [registry.Call(*registry.parse_call(*next(replies)))])
    monkeypatch.setattr(router.tool_dispatch, 'utc_today', lambda:'2026-11-11')
    actor={'id':'fixture','role':'admin'}
    database.change(actor,'ship.save',{'id':'context-ship','name':'가상 선박 해답 2호 · 왕복 시나리오',
                                     'type':'Container','dwt':1000,'sample':True})
    for day,fuel,position in [('2026-11-09',1,'1, 2'),('2026-11-10',2,'3, 4')]:
        database.change(actor,'operation.save',{'id':'context-'+day,'ship':'context-ship','date':day,
            'fuel':fuel,'factor':3.114,'distance':100,'speed':10,'fuelType':'가상 연료','version':0,
            'position':position,'sample':True,'note':'가상 운항 · UTC 일자'})
    client=TestClient(main.app,base_url='http://localhost:5173')
    return client,login(client,'crew')


def post(client,headers,question,parent=None):
    body={'question':question,'task':'auto','context':{}}
    if parent:
        body.update(conversationId=parent.get('conversationId',parent['id']),
                    context={'previousQueryId':parent['id']})
    response=client.post('/api/ask',headers=headers,json=body)
    assert response.status_code==200,response.text
    return response.json()


def test_real_sql_selects_historical_date_and_restores_position_followup(database,monkeypatch):
    client,headers=prepare(database,monkeypatch,[
        ('get_last_recorded_position',{'ship_mentions':['해답 2호']}),
        ('get_last_recorded_position',{}),
        ('get_last_recorded_position',{'ship_mentions':['해답 2호']})])
    first=post(client,headers,'해답 2호 2026년 11월 9일 위치 알려줘')
    assert first['position']['position']=='1, 2' and first['position']['date']=='2026-11-09'
    followup=post(client,headers,'방금 위치는 언제 기록된 거야?',first)
    assert followup['position']['asOf']=='2026-11-10T00:00:00Z'
    assert followup['conversationId']==first['id']
    missing=post(client,headers,'해답 2호 2026년 11월 8일 위치 알려줘',followup)
    assert missing['status']=='insufficient_evidence' and 'position' not in missing
    turns=client.get('/api/conversations/'+first['id']).json()['turns']
    assert len(turns)==3 and turns[1]['answer']['position']['date']=='2026-11-09'


def test_real_sql_keeps_ship_and_year_for_a_short_date_reply(database,monkeypatch):
    client,headers=prepare(database,monkeypatch,[
        ('summarize_operations',{'ship_mentions':['해답 2호'],'period_text':'2026년 11월 9일'}),
        ('summarize_operations',{'period_text':'11월 10일'})])
    first=post(client,headers,'해답 2호 2026년 11월 9일 연료 사용량')
    second=post(client,headers,'그럼 11월 10일은?',first)
    result=second['workflow']['results'][0]['result']
    assert result['fuel_t']=='2.000000'
    assert second['workflow']['scope']['periods']==[{'start':'2026-11-10','end':'2026-11-10'}]
