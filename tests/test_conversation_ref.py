"""Boundary contract for the unwired target conversation identity.

Kind must always be explicit: it is never guessed from the identifier
text, and bot membership is checked against the conversation's current
participants (DM = its own bot only).
"""
from __future__ import annotations

import pytest

from thyca.core.conversation import ConversationRef, bot_is_participant


def test_ref_dict_round_trip_keeps_kind_explicit() -> None:
    group = ConversationRef.new_group()
    # The same text may validly name a bot and a group; round-trip must
    # keep them distinct doors.
    dm_same_text = ConversationRef.dm(group.id)
    for ref in (ConversationRef.dm('coder'), group, dm_same_text):
        assert ref.to_dict() == {'kind': ref.kind, 'id': ref.id}
        assert ConversationRef.from_dict(ref.to_dict()) == ref
    assert ConversationRef.from_dict(dm_same_text.to_dict()) != group


@pytest.mark.parametrize('payload', [
    'coder',
    'group-' + 'a' * 32,
    None,
    ['dm', 'coder'],
    {'id': 'coder'},
    {'id': 'group-' + 'a' * 32},
    {'kind': 'dm'},
    {'kind': 'dm', 'id': 'coder', 'extra': True},
    {'kind': 'system', 'id': 'thyca'},
    {'kind': 'group', 'id': 'coder'},
    {'kind': 'dm', 'id': ''},
    {'kind': 'dm', 'id': 7},
    {'kind': 7, 'id': 'coder'},
])
def test_ref_from_dict_never_guesses_kind(payload) -> None:
    with pytest.raises(ValueError):
        ConversationRef.from_dict(payload)


def test_bot_participant_dm_is_own_bot_only() -> None:
    ref = ConversationRef.dm('coder')
    assert bot_is_participant(ref, ['coder'], 'coder') is True
    assert bot_is_participant(ref, ['coder'], 'thyca') is False
    with pytest.raises(ValueError):
        bot_is_participant(ref, ['thyca'], 'coder')
    with pytest.raises(ValueError):
        bot_is_participant(ref, ['coder', 'thyca'], 'coder')


def test_bot_participant_group_uses_current_membership() -> None:
    ref = ConversationRef.new_group()
    participants = ['coder', 'thyca']
    assert bot_is_participant(ref, participants, 'coder') is True
    assert bot_is_participant(ref, participants, 'scout') is False
    # A former participant is no longer a member.
    assert bot_is_participant(ref, ['thyca'], 'coder') is False


@pytest.mark.parametrize('participants,bot_id', [
    ([], 'coder'),
    (['coder', 'coder'], 'coder'),
    (['../x'], 'coder'),
    ('coder', 'coder'),
    (['coder'], ''),
    (['coder'], '../x'),
    (['coder'], 7),
])
def test_bot_participant_rejects_invalid_state_or_actor(participants, bot_id) -> None:
    with pytest.raises(ValueError):
        bot_is_participant(ConversationRef.new_group(), participants, bot_id)


def test_bot_participant_requires_typed_ref() -> None:
    with pytest.raises(ValueError):
        bot_is_participant('dm:coder', ['coder'], 'coder')
