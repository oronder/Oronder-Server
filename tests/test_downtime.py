"""Downtime coverage.

/downtime buy is driven end-to-end through /testing/downtime_buy (the roll
itself is server-side d20, so only shape is asserted, not totals). The
button/modal flows (DowntimeView selections, GM claim) are Discord-UI-bound,
so the model layer underneath (DowntimeRoll, DowntimeBuyView table fan-out)
is covered directly.
"""

import fake_discord
from harness import seed


async def _buy(api, **overrides):
    payload = {
        "guild_id": fake_discord.DEFAULT_GUILD_ID,
        "discord_id": fake_discord.DEFAULT_GM_USER_ID,
        "channel_id": fake_discord.DEFAULT_CHANNELS["downtime"],
        **overrides,
    }
    r = await api.post("/testing/downtime_buy", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


async def test_downtime_buy_smoke(api, seeded_guild):
    seed.seed_actor(
        seed.dnd5e_actor_payload(actor_id="dtbuy00000000001", name="Downtime Buyer")
    )
    body = await _buy(
        api,
        character="Downtime Buyer",
        item="+1 Longsword",
        extra_weeks=2,
        extra_gold=200,
    )
    assert body["responses"], body
    response = body["responses"][0]
    assert not response["ephemeral"]
    embed = response["embeds"][0]
    assert embed["title"] == "Downtime Buyer goes Shopping!"
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert "Persuasion" in fields
    assert fields["Downtime Duration"] == "3 weeks"
    assert fields["Search Cost"] == "`300 gp`"
    # item outcome depends on the d20 roll: either a price or a failed DC
    item_text = " ".join(f["value"] for f in embed["fields"])
    assert "gp" in item_text or "Failed DC" in item_text


async def test_downtime_buy_without_item_rolls_persuasion(api, seeded_guild):
    seed.seed_actor(
        seed.dnd5e_actor_payload(actor_id="dtbuy00000000002", name="Windowshopper")
    )
    body = await _buy(api, character="Windowshopper")
    embed = body["responses"][0]["embeds"][0]
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert "1d20" in fields["Persuasion"]
    assert fields["Downtime Duration"] == "1 week"
    assert fields["Search Cost"] == "`100 gp`"


async def test_downtime_buy_outside_downtime_channel_is_clean_error(
    api, seeded_guild
):
    seed.seed_actor(
        seed.dnd5e_actor_payload(actor_id="dtbuy00000000003", name="Lost Shopper")
    )
    body = await _buy(
        api,
        character="Lost Shopper",
        channel_id=fake_discord.DEFAULT_CHANNELS["general"],
    )
    response = body["responses"][0]
    assert "must be run in" in response["content"]
    assert response["ephemeral"] is True
    assert not response["embeds"]


async def test_downtime_roll_dc_variants():
    from views.downtime import DowntimeRoll

    fixed = DowntimeRoll(roll=lambda: None, dc_int=15)
    assert fixed.dc() == ("DC 15", 15)

    class FakeRollResult:
        total = 12

        def __str__(self):
            return "12 = 5 + 2d10 (3, 4)"

    contested = DowntimeRoll(roll=lambda: None, dc_fun=lambda: FakeRollResult())
    dc_string, dc_value = contested.dc()
    assert dc_value == 12
    assert dc_string == "12 = 5 + 2d10 (3, 4)"


async def test_downtime_buy_view_table_fanout():
    """Higher persuasion rolls unlock more magic item tables (5 per tier)."""
    from views.downtime import DowntimeBuyView

    def labels(roll):
        view = DowntimeBuyView(roll, False, initiator_id=1)
        return [b.label for b in view.children]

    assert labels(1) == ["Magic Item Table A"]
    assert labels(10) == ["Magic Item Table A", "Magic Item Table B"]
    assert len(labels(45)) == 9
    assert len(labels(999)) == 9  # capped at table I
