"""Multiple official port sources retain their own scope, units, conditions and age."""
from copy import deepcopy
from datetime import timedelta
import json

from pydantic import ValidationError
import pytest

from scripts.prepare_helsinki_references import helsinki_berths
from scripts.prepare_navigation_references import berth_references
from tools.navigation_references import NavigationCatalog, NavigationScreenRequest, catalog_summary, screen_references
from test_navigation_references import catalog_data, NOW, VESSEL
from test_navigation_references import prepared_sources

HEL = 'WPI:28190'


def table(label='LHD', draft='10.8 m', length='313m (+47m) (max vessel length 360m)', extra=''):
    return ('<h1>Cruise Berth Details</h1><h2>West Harbour / Hernesaari</h2><table>'
        f'<tr><th>{label}</th><th></th></tr><tr><td>Berth length (in metres)</td><td>{length}</td></tr>'
        '<tr><td>Depth of water at berths</td><td>11.9m</td></tr>'
        f'<tr><td>Maximum vessel draft</td><td>{draft}</td></tr>'
        '<tr><td>Dock apron width (in metres)</td><td>40m-60m</td></tr>'
        '<tr><td>Date of last berth survey</td><td>2021</td></tr>'
        f'<tr><td>Maximum water level range</td><td>Weather changes; not measured today.</td></tr>{extra}</table>')


def expanded_data():
    data = catalog_data()
    data['version'] = 'NAVIGATION-REFERENCES-1.1'
    data['sources'].append({**deepcopy(data['sources'][2]), 'id': 'helsinki_berths'})
    data['ports'].append({'port_id': HEL, 'unlocode': 'FIHEL', 'name': 'Helsinki',
        'source_id': 'helsinki_berths', 'reference_scope': 'Cruise berths only',
        'berths': helsinki_berths(table(), HEL)})
    return data


def test_helsinki_explicit_vessel_limit_is_separate_from_quay_depth_and_apron():
    result = helsinki_berths(table(), HEL)[0]
    assert result['max_loa_m'] == '360' and result['max_draught_m'] == '10.8'
    assert result['max_beam_m'] is None
    text = result['conditions'][0]
    assert '2021' in text and 'Cruise berth' in text and 'Weather changes' in text
    assert 'West Harbour / Hernesaari' in text and '11.9m' in text


@pytest.mark.parametrize('value', ['330m', '300m(+78m)', '360m with pilot agreement',
    '313m (+47m) (max vessel length 360m) if approved'])
def test_quay_length_or_conditional_limit_does_not_become_permitted_vessel_loa(value):
    assert helsinki_berths(table(length=value), HEL)[0]['max_loa_m'] is None


@pytest.mark.parametrize('value', ['8', '0m', '8m if agreed', '8-9m'])
def test_missing_units_ranges_and_conditions_do_not_become_exact_draught(value):
    assert helsinki_berths(table(draft=value), HEL)[0]['max_draught_m'] is None


def test_comma_decimals_duplicate_variants_and_changed_source_are_not_guessed():
    assert helsinki_berths(table(draft='6,7m'), HEL)[0]['max_draught_m'] == '6.7'
    b = helsinki_berths(table(draft='8m') + table(draft='9m'), HEL)[0]
    assert b['max_draught_m'] is None and len(b['variants']) == 2
    assert 'DUPLICATE_BERTH_VARIANTS_REQUIRE_REVIEW' in b['issues']
    with pytest.raises(ValueError, match='header'):
        helsinki_berths(table(label='NEW-UNKNOWN'), HEL)
    with pytest.raises(ValueError, match='rowspan'):
        helsinki_berths(table().replace('<td>11.9m', '<td rowspan="2">11.9m'), HEL)
    with pytest.raises(ValueError, match='Incomplete'):
        helsinki_berths(table().removesuffix('</table>'), HEL)


def test_gothenburg_parent_section_is_preserved_without_leaking_into_next_section():
    html = '<h2>Energy Port</h2><h5>Berth 510</h5><p>Max. LOA: 100 metres</p>'
    html += '<h2>Another area</h2><h5>Berth 511</h5><p>Max. LOA: 200 metres</p>'
    first, second = berth_references(html, 'WPI:24020')
    assert 'Energy Port' in first['conditions'][0] and 'Energy Port' not in second['conditions'][0]
    assert 'Another area' in second['conditions'][0]


def test_selected_port_uses_its_own_source_age_and_only_returns_relevant_sources():
    data = expanded_data()
    data['sources'][2]['retrieved_at'] = (NOW - timedelta(days=31)).isoformat()
    catalog = NavigationCatalog.model_validate(data)
    result = screen_references(catalog, NavigationScreenRequest(vessel_id=VESSEL, port_id=HEL), now=NOW)
    assert result['berths'][0]['status'] == 'partial_reference_check'
    assert {s['id'] for s in result['sources']} == {'ais', 'schema', 'helsinki_berths'}
    assert result['port_reference_scope'] == 'Cruise berths only' and result['admission'] == 'not_assessed'
    summary = catalog_summary(catalog, 'a' * 64, now=NOW)
    assert summary['ports'][0]['capture_issue'] == 'PORT_CAPTURE_STALE'
    assert summary['ports'][1]['capture_issue'] is None
    data['sources'][3]['retrieved_at'] = (NOW - timedelta(days=31)).isoformat()
    result = screen_references(NavigationCatalog.model_validate(data), NavigationScreenRequest(vessel_id=VESSEL, port_id=HEL), now=NOW)
    assert result['berths'][0]['status'] == 'insufficient_data'


@pytest.mark.parametrize('change', ['missing-source', 'mixed-source', 'wrong-port', 'duplicate-source'])
def test_missing_or_mixed_sources_are_rejected(change):
    data = expanded_data()
    if change == 'missing-source':
        data['sources'].pop()
    elif change == 'mixed-source':
        data['ports'][1]['berths'][0]['source_ref']['source_id'] = 'berths'
    elif change == 'wrong-port':
        data['ports'][1]['unlocode'] = 'FITKU'
    else:
        data['sources'][-1] = deepcopy(data['sources'][0])
    with pytest.raises(ValidationError):
        NavigationCatalog.model_validate(data)


def test_legacy_v1_catalog_remains_accepted_with_explicit_default_source():
    catalog = NavigationCatalog.model_validate(catalog_data())
    assert catalog.version == 'NAVIGATION-REFERENCES-1.0' and catalog.ports[0].source_id == 'berths'


def test_conditions_cannot_be_removed_from_a_valid_numeric_snapshot():
    data = expanded_data()
    data['ports'][1]['berths'][0]['conditions'] = []
    with pytest.raises(ValidationError, match='conditions'):
        NavigationCatalog.model_validate(data)


def test_lower_bound_draught_explains_why_exact_comparison_is_unavailable():
    data = expanded_data()
    data['observations'][0].update(reported_draught_m=None, draught_lower_bound_m='25.5')
    result = screen_references(NavigationCatalog.model_validate(data), NavigationScreenRequest(vessel_id=VESSEL, port_id=HEL), now=NOW)
    check = next(c for c in result['berths'][0]['checks'] if c['kind'] == 'draught')
    assert check['status'] == 'unavailable' and 'AIS_DRAUGHT_LOWER_BOUND' in check['reasons']


def helsinki_capture(tmp_path):
    import csv
    from scripts.prepare_navigation_references import URLS, digest
    raw, files, _manifest = prepared_sources(tmp_path)
    vessels, wpi = files['vessels'], files['wpi']
    payload = table().encode()
    (raw / URLS['helsinki_berths'][1]).write_bytes(payload)
    path = raw / 'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf8'))
    manifest['helsinki_berths'] = {'id':'helsinki_berths','url':URLS['helsinki_berths'][0],
        'filename':URLS['helsinki_berths'][1],'sha256':digest(payload),'retrieved_at':NOW.isoformat(),
        'effective_at':None,'status':'captured'}
    path.write_text(json.dumps(manifest),encoding='utf8')
    with wpi.open(encoding='utf-8-sig') as stream:
        reader=csv.DictReader(stream); fields=reader.fieldnames; rows=list(reader)
    row={**rows[0],'World Port Index Number':'28190','Main Port Name':'Helsinki','UN/LOCODE':'FI HEL',
         'Country Code':'Finland','Latitude':'60.16','Longitude':'24.96'}
    with wpi.open('w',encoding='utf8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows+[row])
    return raw,vessels,wpi


def test_offline_prepare_links_each_source_to_its_reviewed_wpi(tmp_path):
    from scripts.prepare_navigation_references import prepare
    raw,vessels,wpi=helsinki_capture(tmp_path)
    output=tmp_path/'catalog.json'
    result=prepare(raw,vessels,wpi,output)
    catalog=NavigationCatalog.model_validate_json(output.read_bytes())
    assert result['ports']==2 and catalog.ports[1].port_id==HEL
    assert catalog.ports[1].source_id=='helsinki_berths'
    assert catalog.ports[1].berths[0].max_loa_m==360


def test_corrupt_optional_capture_preserves_previous_catalog(tmp_path):
    from scripts.prepare_navigation_references import prepare, URLS
    raw,vessels,wpi=helsinki_capture(tmp_path)
    output=tmp_path/'catalog.json'; output.write_bytes(b'previous complete snapshot')
    (raw/URLS['helsinki_berths'][1]).write_bytes(b'<p>changed without manifest</p>')
    with pytest.raises(ValueError,match='hash mismatch'):
        prepare(raw,vessels,wpi,output)
    assert output.read_bytes()==b'previous complete snapshot'
