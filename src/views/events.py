import asyncio
from typing import List

from discord import ButtonStyle, Interaction, ScheduledEvent
from discord.ui import View, Button

from database.missions import edit_mission
from models.actor import Actor
from models.missions import Mission
from utils import getLogger

logger = getLogger(__name__)


class CharacterSelectView(View):
    mission: Mission
    event: ScheduledEvent
    initiator: int

    def __init__(
        self,
        event: ScheduledEvent,
        initiator: int,
        mission: Mission,
        actors: List[Actor],
    ):
        self.event = event
        self.initiator = initiator
        self.mission = mission

        super().__init__(*[CharacterSelectButton(actor, self) for actor in actors])


class CharacterSelectButton(Button):
    actor: Actor
    parent: CharacterSelectView

    def __init__(self, actor: Actor, parent: CharacterSelectView):
        self.actor = actor
        self.parent = parent
        super().__init__(label=actor.name, style=ButtonStyle.primary)

    async def callback(self, interaction: Interaction):
        if interaction.user.id != self.parent.initiator:
            return

        self.style = ButtonStyle.green
        for button in self.parent.children:
            if button != self:
                button.style = ButtonStyle.gray
        self.parent.disable_all_items()

        content = interaction.message.content

        if len(self.parent.mission.pcs) < self.parent.mission.max_pc_count:
            self.parent.mission.pcs.append(self.actor.id)
            content = f"{content[:-1]} with {self.actor.name}!"
        else:
            self.parent.mission.pcs_standby.append(self.actor.id)
            content = f"{content[:-1]} with {self.actor.name} tentatively!"

        message_edit = interaction.response.edit_message(content=content, view=None)

        mission_edit = edit_mission(interaction.guild, self.parent.mission)
        await asyncio.gather(message_edit, mission_edit)
