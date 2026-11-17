"""Ship shorthand matches must preserve qualified identity and ambiguity."""
from copy import deepcopy

import pytest

from api.ship_identity import name_aliases, named_ships, resolve_ship_name


FLEET = [
    {'id': 'original', 'name': '해답호', 'imo': '7422881', 'version': 1},
    {'id': 'round-trip', 'name': '가상 선박 해답 2호 · 왕복 시나리오', 'imo': '', 'version': 2},
]


@pytest.mark.parametrize('mention', [
    '해답2호', '해답 2호', '가상 선박 해답 2호', '해답 2호 · 왕복 시나리오',
    '가상 선박 해답 2호 · 왕복 시나리오', '  해답\t2호  ',
])
def test_unique_simulation_name_resolves_to_the_registered_full_name(mention):
    assert resolve_ship_name(mention, FLEET) == FLEET[1]['name']


@pytest.mark.parametrize('question', [
    '해답2호의 지난달 연료 사용량을 알려줘', '해답 2호는?',
    '가상 선박 해답 2호 · 왕복 시나리오의 위치',
    '해답 2호 · 왕복 시나리오 위치', '해답2호를 확인해줘',
])
def test_question_matching_supports_spacing_and_korean_particles(question):
    assert named_ships(question, FLEET) == [FLEET[1]]


@pytest.mark.parametrize('mention', ['해답', '해답2', '가상 선박', '왕복 시나리오', '해답-2호', '해답3호'])
def test_arbitrary_substrings_and_similar_names_are_not_aliases(mention):
    assert resolve_ship_name(mention, FLEET) is None
    assert named_ships(mention, FLEET) == []


@pytest.mark.parametrize('question', ['신해답2호의 연료', '해답2호선의 연료', '해답2호_foo', '해답2호-foo'])
def test_aliases_inside_longer_names_do_not_match(question):
    assert named_ships(question, FLEET) == []


def test_numbered_ships_and_multiple_explicit_mentions_remain_distinct():
    assert resolve_ship_name('해답호', FLEET) == FLEET[0]['name']
    assert named_ships('해답호와 해답2호의 연료 비교', FLEET) == FLEET


@pytest.mark.parametrize('name', ['해답 2호', '가상 선박 해답 2호 · 왕복 시나리오'])
def test_shared_alias_or_duplicate_full_name_requires_disambiguation(name):
    fleet = [*FLEET, {'id': 'duplicate', 'name': name, 'imo': '9074729'}]
    with pytest.raises(ValueError, match='여러 척'):
        resolve_ship_name('해답2호', fleet)
    with pytest.raises(ValueError, match='여러 척'):
        named_ships('해답2호의 연료', fleet)
    assert named_ships('해답2호의 연료', fleet, allow_ambiguous=True) == fleet[1:]


def test_a_qualified_name_disambiguates_an_alias_shared_with_another_ship():
    fleet = [*FLEET, {'id': 'plain-name', 'name': '해답 2호'}]
    assert named_ships('가상 선박 해답 2호 · 왕복 시나리오의 연료', fleet) == [FLEET[1]]


def test_unrelated_suffixes_and_hyphens_are_preserved():
    fleet = [{'id': 'actual', 'name': 'SEA-ANSWER II · 연구선'}]
    assert resolve_ship_name('sea-answer ii · 연구선', fleet) == fleet[0]['name']
    assert resolve_ship_name('SEA ANSWER II · 연구선', fleet) is None
    assert resolve_ship_name('SEA-ANSWER II', fleet) is None
    assert named_ships('SEA-ANSWER II의 연료', fleet) == []


def test_name_matching_does_not_resolve_ids_or_imo_numbers():
    for mention in ['original', 'round-trip', '7422881', 'IMO 7422881', 'IMO 7422880']:
        assert resolve_ship_name(mention, FLEET) is None
        assert named_ships(mention, FLEET) == []


def test_aliases_do_not_mutate_the_current_ship_snapshots():
    original = deepcopy(FLEET)
    assert name_aliases(FLEET[1]['name']) == (
        '가상선박해답2호·왕복시나리오', '해답2호·왕복시나리오', '가상선박해답2호', '해답2호',
    )
    assert named_ships('해답2호의 연료', FLEET)[0] is FLEET[1]
    assert FLEET == original
