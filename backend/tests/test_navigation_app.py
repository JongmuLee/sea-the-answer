"""App adapter uses current app identity, cookie/CSRF and explicit real-ship mapping."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import ArgumentError

from api import deps, main
from api import navigation_service as service
from api.errors import ApiError
from api.maritime_context import RealShipContext, resolve_real_ship
from api.routes import navigation
from api.web import security
from test_fastapi_api import FakeWorkspace, ADMIN
from test_navigation_references import catalog_data, IMO, VESSEL, PORT, BERTH
from tools.navigation_references import NavigationCatalog


def mapped(**kwargs):
    return RealShipContext('app-ship', kwargs.get('version', 3),
        {'id': 'app-ship', 'imo': IMO, 'sample': kwargs.get('sample', False)},
        kwargs.get('vessel_id', VESSEL), kwargs.get('imo_number', IMO))


@pytest.fixture
def adapter(monkeypatch):
    engine = MagicMock()
    db = MagicMock()
    db.one.return_value = None
    ctx = SimpleNamespace(db=db, body={'ship': 'app-ship', 'port_id': PORT}, user=ADMIN, request=None, guest={})
    catalog = NavigationCatalog.model_validate(catalog_data())
    monkeypatch.setattr(service, 'create_engine_for', lambda url: engine)
    monkeypatch.setattr(service, 'resolve_real_ship', lambda *args: mapped())
    monkeypatch.setattr(service, 'load_catalog', lambda path: (catalog, 'a' * 64))
    monkeypatch.setattr(navigation.security, 'current_user', lambda *args: ADMIN)
    calculate = MagicMock(return_value={'admission': 'not_assessed', 'safe_draft_m': None})
    monkeypatch.setattr(service, 'screen_from_database', calculate)
    return ctx, engine, calculate


@pytest.mark.parametrize('extra', [{'vessel_id': VESSEL}, {'now': '2026-11-04'}, {'path': 'private.json'}, {'sql': 'SELECT 1'}, {'berth_id': 'WPI:1:BERTH:1'}])
def test_client_cannot_supply_source_identity_clock_path_sql_or_cross_port_berth(adapter, extra):
    ctx, engine, calculate = adapter
    ctx.body.update(extra)
    with pytest.raises(ApiError) as exc:
        navigation.screen(ctx)
    assert exc.value.status == 400
    calculate.assert_not_called()


def test_adapter_uses_mapping_and_preserves_dated_unverified_draught_as_context(adapter):
    ctx, engine, calculate = adapter
    ctx.body['berth_id'] = BERTH
    ctx.db.one.return_value = {'body': {'id': 'op-1', 'date': '2026-10-01', 'draft': 6.2, 'sample': False}, 'version': 4}
    reply = navigation.screen(ctx)
    body = calculate.call_args.args[1]
    assert body.vessel_id == VESSEL and body.berth_id == BERTH
    assert reply['context']['ship_version'] == 3 and reply['result']['admission'] == 'not_assessed'
    record = reply['latest_record_draught']
    assert record['date'] == '2026-10-01' and record['draught_m'] == '6.2'
    assert record['used_for_comparison'] is False and record['measurement_time'] is None
    engine.dispose.assert_called_once()


def test_unmapped_and_sample_ships_do_not_run_comparisons(adapter, monkeypatch):
    ctx, engine, calculate = adapter
    monkeypatch.setattr(service, 'resolve_real_ship', lambda *args: mapped(vessel_id=None))
    assert navigation.screen(ctx)['status'] == 'needs_mapping'
    monkeypatch.setattr(service, 'resolve_real_ship', lambda *args: mapped(sample=True))
    with pytest.raises(ApiError) as exc:
        navigation.screen(ctx)
    assert exc.value.code == 'SAMPLE_SHIP_NOT_SUPPORTED'
    calculate.assert_not_called()


def test_ship_version_or_account_change_discards_completed_result(adapter, monkeypatch):
    ctx, engine, calculate = adapter
    sequence = iter([mapped(), mapped(version=4)])
    monkeypatch.setattr(service, 'resolve_real_ship', lambda *args: next(sequence))
    with pytest.raises(ApiError) as exc:
        navigation.screen(ctx)
    assert exc.value.status == 409
    monkeypatch.setattr(service, 'resolve_real_ship', lambda *args: mapped())
    monkeypatch.setattr(navigation.security, 'current_user', lambda *args: {**ADMIN, 'role': 'guest'})
    with pytest.raises(ApiError) as exc:
        navigation.screen(ctx)
    assert exc.value.status == 401
    assert engine.dispose.call_count == 2


def test_configuration_errors_are_sanitized_and_engine_is_not_leaked(adapter, monkeypatch):
    ctx, engine, calculate = adapter
    def fail(url):
        raise ArgumentError('secret-dsn-password')
    monkeypatch.setattr(service, 'create_engine_for', fail)
    with pytest.raises(ApiError) as exc:
        navigation.screen(ctx)
    assert exc.value.status == 503 and 'secret' not in exc.value.message


def test_common_mapping_is_parameterized_and_checks_exact_imo_identity():
    db, engine = MagicMock(), MagicMock()
    db.one.return_value = {'body': {'imo': 'IMO ' + IMO, 'sample': False}, 'version': 7}
    conn = engine.connect.return_value.__enter__.return_value
    conn.execute.return_value.mappings.return_value.all.return_value = [{'vessel_id': VESSEL, 'imo_number': IMO}]
    result = resolve_real_ship(db, engine, 'app-ship')
    assert result.vessel_id == VESSEL and result.version == 7
    assert conn.execute.call_args.args[1] == {'ship': 'app-ship'}
    db.one.return_value['body']['imo'] = '9074729'
    with pytest.raises(ApiError) as exc:
        resolve_real_ship(db, engine, 'app-ship')
    assert exc.value.code == 'MARITIME_SHIP_IDENTITY_CONFLICT'
    conn.execute.return_value.mappings.return_value.all.return_value = [{'vessel_id': VESSEL, 'imo_number': IMO}] * 2
    assert resolve_real_ship(db, engine, 'app-ship').vessel_id is None


def test_app_route_uses_cookie_and_csrf_without_service_bearer(monkeypatch):
    monkeypatch.setattr(deps, 'Workspace', FakeWorkspace)
    monkeypatch.setattr(deps, 'initialized', False)
    security.sessions.clear()
    data = NavigationCatalog.model_validate(catalog_data())
    monkeypatch.setattr(navigation, 'load_catalog', lambda path: (data, 'a' * 64))
    client = TestClient(main.app, base_url='http://localhost:5173')
    assert client.get('/api/operations/port-references').status_code == 401
    token = client.get('/api/health').json()['csrfToken']
    headers = {'X-Haedap-Token': token}
    assert client.post('/api/auth/login', headers=headers, json={'username': 'admin', 'password': '1234'}).status_code == 200
    response = client.get('/api/operations/port-references')
    assert response.status_code == 200 and response.json()['admission'] == 'not_assessed'
    assert response.headers['cache-control'] == 'no-store'
    assert client.post('/api/operations/port-reference-screen', json={'ship': 'app-ship', 'port_id': PORT}).status_code == 403
    assert client.post('/api/operations/port-reference-screen', headers=headers, json={'ship': 'app-ship', 'port_id': PORT, 'vessel_id': VESSEL}).status_code == 400
    security.sessions.clear()
