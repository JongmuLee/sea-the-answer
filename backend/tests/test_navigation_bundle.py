"""Offline distribution preserves bytes and source age; failed replacements preserve the target."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import navigation_bundle as bundle
from tools.navigation_references import NavigationCatalog, NavigationScreenRequest, screen_references
from test_navigation_expansion import expanded_data, HEL
from test_navigation_references import NOW, VESSEL


@pytest.fixture
def exported(tmp_path):
    path = tmp_path / 'input.json'
    data = expanded_data()
    data['sources'][0]['attribution'] = 'Fintraffic test fixture; CC BY 4.0; normalized'
    data['sources'][0]['license_url'] = 'https://creativecommons.org/licenses/by/4.0/'
    path.write_text(NavigationCatalog.model_validate(data).model_dump_json(), encoding='utf8')
    directory = tmp_path / 'bundle'
    bundle.export_bundle(path, directory)
    return path, directory


def test_export_is_byte_exact_and_offline_without_accounts_or_original_annual_rows(exported, monkeypatch):
    path, directory = exported
    network = Mock(side_effect=AssertionError('network is not allowed'))
    monkeypatch.setattr('urllib.request.urlopen', network)
    result = bundle.inspect_bundle(directory)
    assert (directory / 'catalog.json').read_bytes() == path.read_bytes()
    assert {p.name for p in directory.iterdir()} == {'catalog.json', 'manifest.json', 'SOURCES.txt'}
    assert result['version'] == 'NAVIGATION-REFERENCES-1.1' and len(result['ports']) == 2
    assert 'CC BY' in (directory / 'SOURCES.txt').read_text(encoding='utf8')
    network.assert_not_called()
    with pytest.raises(FileExistsError):
        bundle.export_bundle(path, directory)


@pytest.mark.parametrize('change', ['catalog-bytes', 'manifest-hash', 'manifest-path', 'manifest-source', 'invalid-schema'])
def test_tampered_bundles_never_create_target(exported, tmp_path, change):
    _path, directory = exported
    catalog = directory / 'catalog.json'
    manifest_path = directory / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf8'))
    if change == 'catalog-bytes':
        catalog.write_bytes(catalog.read_bytes() + b' ')
    elif change == 'manifest-hash':
        manifest['catalog_sha256'] = '0' * 64
    elif change == 'manifest-path':
        manifest['catalog_file'] = '../input.json'
    elif change == 'manifest-source':
        manifest['sources'][0]['attribution'] = 'changed source metadata'
    else:
        payload = b'{}'
        catalog.write_bytes(payload)
        manifest.update(catalog_sha256=bundle.sha(payload), catalog_bytes=len(payload))
    manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    target = tmp_path / 'installed.json'
    with pytest.raises(bundle.BundleError):
        bundle.install_bundle(directory, target)
    assert not target.exists()


def test_existing_target_requires_matching_hash_and_roundtrip_rollback(exported, tmp_path):
    source, directory = exported
    target = tmp_path / 'installed.json'
    original = NavigationCatalog.model_validate(expanded_data()).model_copy(update={'version': 'NAVIGATION-REFERENCES-1.0'}).model_dump_json().encode()
    target.write_bytes(original)
    for expected in (None, '0' * 64):
        with pytest.raises(bundle.BundleError, match='hash'):
            bundle.install_bundle(directory, target, expected_current_sha256=expected)
        assert target.read_bytes() == original
    reply = bundle.install_bundle(directory, target, expected_current_sha256=bundle.sha(original))
    assert target.read_bytes() == source.read_bytes()
    backup = target.parent / reply['backup']
    assert backup.read_bytes() == original
    assert bundle.install_bundle(directory, target, expected_current_sha256=bundle.sha(source.read_bytes()))['status'] == 'unchanged'
    previous = tmp_path / 'previous-bundle'
    bundle.export_bundle(backup, previous)
    bundle.install_bundle(previous, target, expected_current_sha256=bundle.sha(source.read_bytes()))
    assert target.read_bytes() == original


def test_initial_install_lock_and_failed_atomic_replace_leave_previous_snapshot(exported, tmp_path, monkeypatch):
    source, directory = exported
    target = tmp_path / 'installed.json'
    bundle.install_bundle(directory, target)
    original = target.read_bytes()
    lock = target.with_name('.' + target.name + '.install.lock')
    lock.write_text('another installer', encoding='utf8')
    with pytest.raises(bundle.BundleError, match='lock'):
        bundle.install_bundle(directory, target, expected_current_sha256=bundle.sha(original))
    assert lock.read_text() == 'another installer' and target.read_bytes() == original
    lock.unlink()
    changed = deepcopy(expanded_data())
    changed['observations'][0]['name'] = 'Changed public label'
    source.write_text(NavigationCatalog.model_validate(changed).model_dump_json(), encoding='utf8')
    next_bundle = tmp_path / 'next'
    bundle.export_bundle(source, next_bundle)
    def fail(*args):
        raise OSError('write failed')
    monkeypatch.setattr(bundle, 'atomic_write', fail)
    with pytest.raises(OSError):
        bundle.install_bundle(next_bundle, target, expected_current_sha256=bundle.sha(original))
    assert target.read_bytes() == original and not lock.exists()
    assert list(tmp_path.glob('installed.json.previous.*.json'))[0].read_bytes() == original


def test_repack_and_install_never_renew_stale_source_timestamps(tmp_path):
    data = expanded_data()
    data['sources'][0]['retrieved_at'] = (NOW - timedelta(days=2)).isoformat()
    source = tmp_path / 'stale.json'
    source.write_text(NavigationCatalog.model_validate(data).model_dump_json(), encoding='utf8')
    directory = tmp_path / 'bundle'
    result = bundle.export_bundle(source, directory)
    assert result['freshness']['ais_capture_issue'] == 'AIS_CAPTURE_STALE'
    target = tmp_path / 'installed.json'
    bundle.install_bundle(directory, target)
    installed = NavigationCatalog.model_validate_json(target.read_bytes())
    assert installed.sources[0].retrieved_at.isoformat() == data['sources'][0]['retrieved_at']
    reply = screen_references(installed, NavigationScreenRequest(vessel_id=VESSEL, port_id=HEL), now=NOW)
    assert reply['berths'][0]['status'] == 'insufficient_data' and reply['admission'] == 'not_assessed'


def test_symlink_target_is_rejected_before_lock_or_write(exported, tmp_path, monkeypatch):
    _source, directory = exported
    target = tmp_path / 'linked.json'
    real = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path == target or real(path))
    with pytest.raises(bundle.BundleError, match='symlink'):
        bundle.install_bundle(directory, target)
    assert not target.exists()


def test_oversized_catalog_is_rejected_before_creating_installation(exported,tmp_path):
    _source,directory=exported
    with (directory/'catalog.json').open('wb') as stream:
        stream.seek(bundle.LIMIT);stream.write(b'x')
    with pytest.raises(bundle.BundleError,match='size budget'):
        bundle.install_bundle(directory,tmp_path/'never-created.json')
    assert not (tmp_path/'never-created.json').exists()
