"""enqueue_xp merges new XP totals into whatever is already queued for Foundry."""

from models.guild_settings import GuildSettings


def _settings(pending=None) -> GuildSettings:
    return GuildSettings.model_construct(pending_xp=pending)


def test_empty_queue_takes_the_new_totals():
    gs = _settings(None)
    gs.enqueue_xp({"a": 100, "b": 200})
    assert gs.pending_xp == {"a": 100, "b": 200}


def test_merge_adds_new_characters_and_replaces_queued_totals():
    # The production failure: a second reward while the first is still queued.
    gs = _settings({"queued": 35825, "other": 28200})
    gs.enqueue_xp({"queued": 38825, "newcomer": 44415})
    assert gs.pending_xp == {"queued": 38825, "other": 28200, "newcomer": 44415}


def test_merge_does_not_alias_the_input():
    src = {"a": 1}
    gs = _settings(None)
    gs.enqueue_xp(src)
    src["a"] = 2
    assert gs.pending_xp == {"a": 1}
