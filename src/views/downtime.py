from __future__ import annotations

from typing import Optional, Tuple, Callable

import d20
from discord import ButtonStyle, Interaction, Embed
from discord.ui import View, Button
from discord.utils import generate_snowflake
from sqlalchemy import select

from database import Session, DowntimeTable
from database.guild_settings_table import GuildSettingsTable
from system import items
from system.items import get_item_price_string
from models import DowntimeModel
from utils import getLogger, truncate

logger = getLogger(__name__)


class DowntimeRoll:
    roll: Callable
    _dc_fun: Optional[callable]
    _dc_int: Optional[int]
    group: Optional[int] = None

    def __init__(
        self,
        roll: Callable,
        dc_fun: Optional[callable] = None,
        dc_int: Optional[int] = None,
        group: Optional[int] = None,
    ):
        assert (dc_fun or dc_int) and not (dc_fun and dc_int)
        self.roll = roll
        self._dc_fun = dc_fun
        self._dc_int = dc_int
        self.group = group

    def dc(self) -> Tuple[str, int]:
        if self._dc_fun:
            dc_res = self._dc_fun()
            return str(dc_res), dc_res.total
        else:
            return f"DC {self._dc_int}", self._dc_int


class DowntimeButton(Button):
    downtime_roll: DowntimeRoll
    parent: DowntimeView

    def __init__(
        self, downtime_roll: DowntimeRoll, parent: DowntimeView, *args, **kwargs
    ):
        self.downtime_roll = downtime_roll
        self.parent = parent
        super().__init__(style=ButtonStyle.primary, *args, **kwargs)

    async def callback(self, interaction: Interaction):
        if interaction.user.id != self.parent.initiator_id:
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        if len(self.parent.selections) >= 3:
            await interaction.response.send_message("Excess selection.", ephemeral=True)
            return
        if self.label in self.parent.selections:
            await interaction.response.send_message(
                "Duplicate selection.", ephemeral=True
            )
            return

        roll_result = self.downtime_roll.roll()
        contested_roll_string, contested_roll_value = self.downtime_roll.dc()
        win = roll_result.total >= contested_roll_value
        if win:
            self.parent.wins += 1
            self.style = ButtonStyle.green
        else:
            self.style = ButtonStyle.red

        self.parent.disable_group(self)

        embed = self.parent.message.embeds[0]
        embed.add_field(name="", value="", inline=False)
        embed.add_field(name=f"**{self.label}**", value="", inline=False)
        embed.add_field(name="", value=f"{str(roll_result)}", inline=True)
        embed.add_field(
            name="",
            value=f"{'>' if roll_result.total > contested_roll_value else '>=' if win else '<'}",
            inline=True,
        )
        embed.add_field(name="", value=contested_roll_string, inline=True)

        self.parent.selections.add(self.label)
        if len(self.parent.selections) == 3:
            self.parent.disable_all()
            embed.add_field(
                name="**Complications**", value=str(d20.roll("1d100")), inline=False
            )
            embed.set_footer(
                text=self.parent.wins_to_outcome(self.parent.wins),
                # TODO icon_url=interaction.user.avatar.url
            )

        await interaction.response.edit_message(view=self.parent, embed=embed)

        if len(self.parent.selections) == 3:
            await gm_downtime_logic(interaction, embed)


class DowntimeView(View):
    selections: set
    wins: int
    wins_to_outcome: Callable
    initiator_id: int

    def __init__(
        self, stats_to_rolls: dict, wins_to_outcome: Callable, initiator_id: int
    ):
        self.wins_to_outcome = wins_to_outcome
        self.initiator_id = initiator_id
        self.selections = set()
        self.wins = 0
        buttons = [
            DowntimeButton(label=k, downtime_roll=v, parent=self)
            for (k, v) in stats_to_rolls.items()
        ]

        super().__init__(*buttons, timeout=None, disable_on_timeout=True)

    def disable_all(self):
        for button in self.children:
            if button.style == ButtonStyle.primary:
                button.style = ButtonStyle.gray

        self.disable_all_items()

    def disable_group(self, button: DowntimeButton):
        if button.downtime_roll.group:
            group_buttons = [
                b
                for b in self.children
                if isinstance(b, DowntimeButton)
                and b.downtime_roll.group == button.downtime_roll.group
            ]
        else:
            group_buttons = [button]
        others = [b for b in self.children if b not in group_buttons]
        for button in group_buttons:
            if button.style == ButtonStyle.primary:
                button.style = ButtonStyle.gray
        self.disable_all_items(exclusions=others)


class DowntimeBuyView(View):
    initiator_id: int
    main_item_found: bool

    def __init__(self, roll: int, main_item_found: bool, initiator_id: int):
        self.initiator_id = initiator_id
        self.main_item_found = main_item_found
        buttons: list[Button] = [
            DowntimeBuyButton(
                label=f"Magic Item Table {table}", table_letter=table, parent=self
            )
            for table in ["A", "B", "C", "D", "E", "F", "G", "H", "I"][
                : max(1, min(9, int((roll - 1) / 5) + 1))
            ]
        ]

        super().__init__(*buttons, timeout=None, disable_on_timeout=True)

    async def callback(self, interaction: Interaction, table_letter):
        for button in self.children:
            if button.style == ButtonStyle.primary:
                button.style = ButtonStyle.gray

        self.disable_all_items()

        item_count_roll_string = "1d6" if table_letter == "A" else "1d4"
        if self.main_item_found:
            item_count_roll_string += "-1"
        item_count_roll = d20.roll(item_count_roll_string)

        item_table = next(
            i for i in items.loot_table["magicItems"] if i["type"] == table_letter
        )["table"]
        rolls = [d20.roll("1d100").total for _ in range(item_count_roll.total)]
        rolled_items = [
            next(
                items.process_roll_table_item(i)
                for i in item_table
                if i["min"] <= roll <= i.get("max", i["min"])
            )
            for roll in rolls
        ]

        embed = self.message.embeds[0]
        embed.add_field(
            name="",
            value=f"*{item_count_roll} items from Magic Item Table {table_letter}*",
        )

        for rolled_item in rolled_items:
            item_actual, _ = items.get_item(rolled_item)
            consumable = (item_actual and item_actual["consumable"]) or any(
                i in rolled_item.lower() for i in ["potion", "scroll"]
            )
            embed.add_field(
                name=rolled_item,
                value=get_item_price_string(rolled_item, consumable),
                inline=False,
            )

        embed.add_field(
            name="Complications", value=str(d20.roll("1d100")), inline=False
        )
        embed.set_footer(
            text="*prices must be scaled by GM",
            # TODO icon_url=interaction.user.avatar.url
        )

        await interaction.response.edit_message(view=self, embed=embed)
        await gm_downtime_logic(interaction, embed)


class DowntimeBuyButton(Button):
    parent: DowntimeBuyView
    table_letter: str

    def __init__(self, parent: DowntimeBuyView, table_letter: str, *args, **kwargs):
        self.parent = parent
        self.table_letter = table_letter

        super().__init__(*args, **kwargs, style=ButtonStyle.primary)

    async def callback(self, interaction: Interaction):
        if interaction.user.id != self.parent.initiator_id:
            await interaction.response.send_message("Not for you.", ephemeral=True)
        else:
            self.style = ButtonStyle.success
            await self.parent.callback(interaction, self.table_letter)


class DowntimeGmView(View):
    def __init__(self, downtime_model: DowntimeModel | None = None):
        self.button = DowntimeGmButton(downtime_model, self)
        super().__init__(self.button, timeout=None)


class DowntimeGmButton(Button):
    downtime_model: DowntimeModel
    parent: DowntimeGmView

    def __init__(
        self, downtime_model: DowntimeModel, parent: DowntimeGmView, *args, **kwargs
    ):
        self.downtime_model = downtime_model
        self.parent = parent
        super().__init__(
            label="Claim", custom_id=downtime_model.gm_custom_id, *args, **kwargs
        )

    async def callback(self, interaction: Interaction):
        if self.downtime_model.player_id == interaction.user.id:
            await interaction.response.send_message(
                "Can not administer your own character's downtime.", ephemeral=True
            )
            return
        self.parent.disable_all_items()

        with Session() as session:
            dtt = session.scalars(
                select(DowntimeTable).where(
                    DowntimeTable.player_message_id
                    == self.downtime_model.player_message_id
                )
            ).one()
            session.delete(dtt)
            session.commit()

        self.parent.remove_item(self)
        await interaction.response.edit_message(view=self.parent)
        thread = await interaction.message.create_thread(
            name=truncate(interaction.message.embeds[0].title, 100)
        )
        await thread.send(interaction.user.mention)


async def gm_downtime_logic(interaction: Interaction, embed: Embed):
    guild_settings = GuildSettingsTable.lookup(interaction.guild_id)
    if (
        not guild_settings.downtime_gm_channel_id
        or guild_settings.downtime_channel_id == guild_settings.downtime_gm_channel_id
    ):
        return

    downtime_gm_channel = interaction.guild.get_channel(
        guild_settings.downtime_gm_channel_id
    )
    if not downtime_gm_channel:
        return

    downtime_model = DowntimeModel(
        player_message_id=interaction.message.id,
        player_channel_id=guild_settings.downtime_channel_id,
        player_id=interaction.user.id,
        gm_custom_id=str(generate_snowflake()),
        gm_channel_id=downtime_gm_channel.id,
        guild_id=guild_settings.id,
    )
    with Session() as session:
        session.add(DowntimeTable.from_model(downtime_model))
        session.commit()

    gm_view = DowntimeGmView(downtime_model)
    embed.add_field(
        name='Link',
        value=interaction.message.jump_url
    )
    await downtime_gm_channel.send(embed=embed, view=gm_view)
