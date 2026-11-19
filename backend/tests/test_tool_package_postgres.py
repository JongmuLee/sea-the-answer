"""Actual HTTP/SQL checks of the new model-dispatch path; model output is a fixture."""
from fastapi.testclient import TestClient
import pytest

from api import config, main, question_router
from rag_postgres_support import login
from test_workflow_skills import database
from tools import registry


@pytest.fixture
def model_dispatch(database, monkeypatch):
    monkeypatch.delenv('HAEDAP_QUESTION_ROUTER', raising=False)
    settings=config.LLMSettings('http://fixture.invalid/v1','fixture-model','')
    monkeypatch.setattr(config,'llm_settings',lambda:settings)
    call=registry.Call(*registry.parse_call('summarize_operations',{
        'ship_mentions':['통합 검증호'],'period_text':'2025년 1월',
    }))
    monkeypatch.setattr(question_router.tool_planner,'plan',lambda *a,**k:[call])
    # Each HTTP request creates a Workspace over the fixture connection.
    monkeypatch.setattr(type(database),'search',lambda *a,**k:[])
    actor={'id':'fixture','role':'admin'}
    database.change(actor,'ship.save',{'id':'tool-package-ship','name':'통합 검증호','type':'Bulk carrier','dwt':1000,'sample':True})
    database.change(actor,'operation.save',{'ship':'tool-package-ship','date':'2025-01-01',
        'fuel':2,'factor':3.114,'distance':100,'speed':10,'fuelType':'가상 연료','version':0,'sample':True})
    client=TestClient(main.app,base_url='http://localhost:5173')
    return client, login(client,'crew')


def test_model_tool_dispatch_records_current_results_with_the_request_owner(database,model_dispatch):
    client,headers=model_dispatch
    response=client.post('/api/ask',headers=headers,json={'question':'통합 검증호 2025년 1월 연료 합계 알려줘','task':'auto'})
    assert response.status_code==200,response.text
    answer=response.json()
    assert answer['workflow']['results'][0]['result']['fuel_t']=='2.000000'
    owner=database.one('SELECT owner FROM query_owner WHERE id=%s',(answer['id'],))
    assert owner['owner']==database.one("SELECT id FROM users WHERE username='crew'")['id']


def test_model_tool_dispatch_rolls_back_history_when_account_is_revoked(database,model_dispatch,monkeypatch):
    client,headers=model_dispatch
    original=type(database).operation_summary
    crew_id=database.one("SELECT id FROM users WHERE username='crew'")['id']
    def revoked(workspace,*args,**kwargs):
        result=original(workspace,*args,**kwargs)
        workspace.run('UPDATE users SET active=false WHERE id=%s',(crew_id,))
        return result
    monkeypatch.setattr(type(database),'operation_summary',revoked)
    before=database.one('SELECT count(*) AS n FROM queries')['n']
    response=client.post('/api/ask',headers=headers,json={'question':'통합 검증호 2025년 1월 연료 합계 알려줘','task':'auto'})
    assert response.status_code==401,response.text
    assert database.one('SELECT count(*) AS n FROM queries')['n']==before
