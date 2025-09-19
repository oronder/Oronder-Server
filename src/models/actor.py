import html as _html
import textwrap
from typing import List, Optional, Literal, Any, Annotated, Tuple

import d20
from d20 import RollResult
from discord.utils import snowflake_time
from pydantic import Field, AliasChoices, BeforeValidator
from sqlalchemy import text, any_, func, select, TextClause

from database import Session, CampaignTable, XpAdjustmentsTable
from dnd import STAT_NAME_TO_ABRV, STAT_ABRV_TO_NAME, TOOLS
from dnd.items import attack_modes_machine, calculate_average_damage
from dnd.rules import lvl_to_xp
from models.base_model import OronderBaseModel
from models.guild_settings import GuildSettings
from utils import getLogger

logger = getLogger(__name__)


class Currency(OronderBaseModel):
    pp: int
    gp: int
    ep: int
    sp: int
    cp: int

    def stringify(self):
        msg = []

        if self.pp:
            msg.append(f"{self.pp} Platinum")
        if self.gp:
            msg.append(f"{self.gp} Gold")
        if self.ep:
            msg.append(f"{self.ep} Electrum")
        if self.sp:
            msg.append(f"{self.sp} Silver")
        if self.cp:
            msg.append(f"{self.cp} Copper")

        if len(msg) > 1:
            msg[-2] = f"{msg[-2]} and {msg[-1]}"
            msg.pop()

        if not msg:
            msg.append("BANKRUPT")

        return ", ".join(msg)


class AbilityBonuses(OronderBaseModel):
    check: str
    save: str
    skill: str


class Bonuses(OronderBaseModel):
    mwak: dict
    rwak: dict
    msak: dict
    rsak: dict
    abilities: AbilityBonuses
    spell: dict


class Rollable(OronderBaseModel):
    total: Optional[int] = None
    save: Optional[int] = None
    mod: Optional[int] = None

    def get_check(self) -> int:
        if self.total is not None:
            return self.total
        else:
            return self.mod

    def roll(
        self,
        advantage: Optional[Literal["adv", "dis"]] = None,
        is_save: bool = False,
        situational_bonus: int = 0,
        pre_min: int | None = None,
        post_min: int | None = None,
    ) -> RollResult:
        roll_bonus = self.get_check() if self.save is None or not is_save else self.save
        die_str = (
            "2d20kh1"
            if advantage == "adv"
            else "2d20kl1"
            if advantage == "dis"
            else "1d20"
        )

        if pre_min is not None:
            die_str = f"{die_str}mi{pre_min}"

        die_str = f"{die_str}+{str(roll_bonus)}".replace("+-", "-")
        if situational_bonus:
            die_str = f"{die_str}+{str(situational_bonus)}"

        if post_min is not None:
            die_str = f"({die_str},{post_min})kh1"

        return d20.roll(die_str)

    def roll_str(
        self,
        stat_abrv: str,
        advantage: Optional[Literal["adv", "dis"]] = None,
        is_save: bool = False,
        situational_bonus: int = 0,
        pre_min: int | None = None,
        post_min: int | None = None,
    ) -> Tuple[str, RollResult]:
        roll_result = self.roll(
            advantage, is_save, situational_bonus, pre_min, post_min
        )
        roll_type = "Saving Throw" if is_save else "Check"
        return f"{STAT_ABRV_TO_NAME.get(stat_abrv, stat_abrv)} {roll_type}", roll_result


class Skill(Rollable):
    value: float
    ability: str
    bonus: int
    proficient: float
    passive: int


class Skills(OronderBaseModel):
    acr: Skill
    ani: Skill
    arc: Skill
    ath: Skill
    dec: Skill
    his: Skill
    ins: Skill
    itm: Skill
    inv: Skill
    med: Skill
    nat: Skill
    prc: Skill
    prf: Skill
    per: Skill
    rel: Skill
    slt: Skill
    ste: Skill
    sur: Skill


class Ability(Rollable):
    value: int
    proficient: int
    saveBonus: int
    checkBonus: int
    save: int
    dc: int


class Abilities(OronderBaseModel):
    str: Ability
    dex: Ability
    con: Ability
    int: Ability
    wis: Ability
    cha: Ability


class Hp(OronderBaseModel):
    max: int


class Attributes(OronderBaseModel):
    hp: Hp
    movement: dict
    attunement: dict
    senses: dict
    spellcaster: int = -1  # -1 for nothing, 0 for cantrip, 1 for first lvl etc
    init: Rollable | None = None
    spellcasting: str
    ac: dict
    exhaustion: int
    inspiration: bool
    prof: int
    spelldc: int
    spellmod: int


class Xp(OronderBaseModel):
    value: int
    max: int


class Biography(OronderBaseModel):
    value: str
    public: str


class Item(OronderBaseModel):
    name: str
    img: str | None = None
    id: str
    type: Literal[
        "background",
        "feat",
        "equipment",
        "container",
        "class",
        "loot",
        "consumable",
        "weapon",
        "race",
        "spell",
        "subclass",
        "tool",
    ]


class Attack(Item):
    attack: str


class Weapon(Attack):
    # noinspection PyTypeHints
    attack_modes: List[Literal[*attack_modes_machine]]
    type: Literal["weapon"] = "weapon"


class Spell(Attack):
    level: int
    type: Literal["spell"] = "spell"


class Details(OronderBaseModel):
    biography: Biography
    alignment: str
    background: str
    xp: Xp
    appearance: str
    trait: str
    ideal: str
    bond: str
    flaw: str
    level: int
    race: str
    dead: bool = False
    items: List[Item] = []


class Tool(Rollable):
    value: int
    ability: str
    bonus: int
    prof: int = 0


class Tools(OronderBaseModel):
    alchemist: Optional[Tool] = None  # Alchemist's Supplies
    brewer: Optional[Tool] = None  # Brewer's Supplies
    calligrapher: Optional[Tool] = None  # Calligrapher's Supplies
    carpenter: Optional[Tool] = None  # Carpenter's Tools
    cartographer: Optional[Tool] = None  # Cartographer's Tools
    cobbler: Optional[Tool] = None  # Cobbler's Tools
    cook: Optional[Tool] = None  # Cook's Utensils
    glassblower: Optional[Tool] = None  # Glassblower's Tools
    jeweler: Optional[Tool] = None  # Jeweler's Tools
    leatherworker: Optional[Tool] = None  # Leatherworker's Tools
    mason: Optional[Tool] = None  # Mason's Tools
    painter: Optional[Tool] = None  # Painter's Supplies
    potter: Optional[Tool] = None  # Potter's Tools
    smith: Optional[Tool] = None  # Smith's Tools
    tinker: Optional[Tool] = None  # Tinker's Tools
    weaver: Optional[Tool] = None  # Weaver's Tools
    woodcarver: Optional[Tool] = None  # Woodcarver's Tools
    disg: Optional[Tool] = None  # Disguise Kit
    forg: Optional[Tool] = None  # Forgery Kit
    chess: Optional[Tool] = None  # Chess Set
    dice: Optional[Tool] = None  # Dice Set
    card: Optional[Tool] = None  # Playing Cards Set
    herb: Optional[Tool] = None  # Herbalism Kit
    bagpipes: Optional[Tool] = None  # Bagpipes
    drum: Optional[Tool] = None  # Drum
    dulcimer: Optional[Tool] = None  # Dulcimer
    flute: Optional[Tool] = None  # Flute
    horn: Optional[Tool] = None  # Horn
    lute: Optional[Tool] = None  # Lute
    lyre: Optional[Tool] = None  # Lyre
    panflute: Optional[Tool] = None  # Pan Flute
    shawm: Optional[Tool] = None  # Shawm
    viol: Optional[Tool] = None  # Viol
    navg: Optional[Tool] = None  # Navigator's Tools
    pois: Optional[Tool] = None  # Poisoner's Kit
    thief: Optional[Tool] = None  # Thieves' Tools
    air: Optional[Tool] = None  # Air Vehicle
    land: Optional[Tool] = None  # Land Vehicle
    space: Optional[Tool] = None  # Space Vehicle
    water: Optional[Tool] = None  # Water Vehicle

    def known_tool_strings(self):
        return [TOOLS[k] for (k, v) in vars(self).items() if v]


class World(OronderBaseModel):
    id: str
    coreVersion: str
    system: str
    systemVersion: str


def validate_weapons(weapons: Any) -> Any:
    if not isinstance(weapons, list):
        return weapons
    else:
        validated_weapons = []
        for weapon in weapons:
            if isinstance(weapon, (Weapon, Spell)):
                validated_weapons.append(weapon)
            elif isinstance(weapon, dict):
                if weapon.get("type") == "spell":
                    validated_weapons.append(Spell.model_validate(weapon))
                elif weapon.get("type") == "weapon":
                    validated_weapons.append(Weapon.model_validate(weapon))
        return validated_weapons


class Actor(OronderBaseModel):
    currency: Currency
    abilities: Abilities
    bonuses: Bonuses
    skills: Skills
    tools: Tools
    attributes: Attributes
    details: Details
    traits: dict
    classes: dict
    id: str
    name: str
    discord_ids: List[int]
    weapons: Annotated[
        List[Weapon | Spell],
        Field(validation_alias=AliasChoices("weapons")),
        BeforeValidator(validate_weapons),
    ]
    equipment: List[str]
    portrait_url: str
    world: Optional[World] = None

    def first_name(self):
        return self.name.split(" ")[0]

    def stat(self, stat_abrv: str) -> Tuple[Rollable | None, str | None]:
        if stat_abrv == "init":
            return self.attributes.init or self.abilities.dex, None
        if hasattr(self.abilities, stat_abrv):
            ability: Ability = getattr(self.abilities, stat_abrv)
            return ability, None
        elif hasattr(self.skills, stat_abrv):
            skill: Skill = getattr(self.skills, stat_abrv)
            return skill, None
        elif hasattr(self.tools, stat_abrv):
            tool: Tool = getattr(self.tools, stat_abrv)
            return tool, None
        else:
            return None, "Trait not found!"

    def check(self, stat_abrv: str) -> int:
        stat: Rollable
        stat, error = self.stat(stat_abrv)
        if error:
            return error

        return stat.get_check()

    def get_min(self, stat_abrv: str) -> Tuple[int | None, int | None]:
        stat, err = self.stat(stat_abrv)
        if err:
            logger.warning(stat_abrv)

        classes = self.classes.items()
        # reliable talent - rogue 11
        proficient = (hasattr(stat, "proficient") and stat.proficient > 0) or (
            hasattr(stat, "prof") and stat.prof > 0
        )
        lvl_11_rogue = next(
            (True for c, d in classes if c == "rogue" and d["levels"] >= 11), False
        )
        if proficient and lvl_11_rogue:
            return 10, None

        # silver tongue - eloquence bard 3
        lvl_3_eloquence_bard = next(
            (
                True
                for c, d in classes
                if c == "bard"
                and d["levels"] >= 3
                and d["subclass"]["identifier"] == "college-of-eloquence"
            ),
            False,
        )
        if stat_abrv in ["dec", "per"] and lvl_3_eloquence_bard:
            return 10, None

        #  Indomitable Might - barb 18
        lvl_18_barb = next(
            (True for c, d in classes if c == "barbarian" and d["levels"] >= 18), False
        )
        if stat_abrv in ["ath", "str"] and lvl_18_barb:
            return None, self.abilities.str.value

        return None, None

    def roll(
        self,
        stat_name: str,
        adv: bool = False,
        is_save: bool = False,
        situational_bonus: int = 0,
    ) -> RollResult:
        stat_abrv = STAT_NAME_TO_ABRV.get(stat_name, stat_name)
        stat: Rollable
        stat, error = self.stat(stat_abrv)
        if error:
            return error

        pre_min, post_min = (None, None) if is_save else self.get_min(stat_abrv)

        return stat.roll(
            "adv" if adv else None,
            is_save and isinstance(stat, Ability),
            situational_bonus,
            pre_min,
            post_min,
        )

    def roll_str(
        self,
        stat_name: str,
        advantage: Optional[Literal["adv", "dis"]] = None,
        is_save: bool = False,
        situational_bonus: int = 0,
    ) -> Tuple[str, RollResult]:
        stat_abrv = STAT_NAME_TO_ABRV.get(stat_name, stat_name)
        stat: Rollable
        stat, error = self.stat(stat_abrv)
        if error:
            return error
        pre_min, post_min = (None, None) if is_save else self.get_min(stat_abrv)

        return stat.roll_str(
            stat_abrv,
            advantage,
            is_save and isinstance(stat, Ability),
            situational_bonus,
            pre_min,
            post_min,
        )

    def save(self, ability_abrv: str):
        ability, error = self.stat(ability_abrv)
        if error:
            return error
        if not isinstance(ability, Ability):
            ability_name = STAT_ABRV_TO_NAME.get(ability_abrv, ability_abrv)
            return (
                f"Only abilities have saving throws. {ability_name} is not an ability!"
            )

        return ability.save

    def mod(self, stat_name: str, is_save: bool = False):
        stat_abrv = STAT_NAME_TO_ABRV.get(stat_name, stat_name)
        if hasattr(self.abilities, stat_abrv):
            ability = getattr(self.abilities, stat_abrv)
            return ability.save if is_save else ability.mod, None
        elif hasattr(self.skills, stat_abrv):
            return getattr(self.skills, stat_abrv).total, None
        elif hasattr(self.tools, stat_abrv):
            tool = getattr(self.tools, stat_abrv)
            return tool.total if tool else 0, None
        else:
            return None, "Trait not found!"

    """
    Orc 5 Priest/1 Rogue
    """

    def desc_string(self):
        return f"""{self.details.race.split("(")[0].rstrip()} {"/".join([f"{k.title()} {v['levels']}" for k, v in self.classes.items()])}"""

    def markdown_sheet(self) -> str:
        """
        Generate a Wiki.js-friendly character sheet:
        - Title then portrait HTML on its own line (no tables around it)
        - All sections use standard Markdown headings/lists (no HTML except the image)
        - Clean formatting for movement/senses
        - Avoids multi-line content inside Markdown tables
        """

        def fmt_signed(n: int | None) -> str:
            if n is None:
                return "—"
            return f"{n:+d}"

        # Portrait (kept as HTML as requested)
        portrait_html = (
            f'<img src="{self.portrait_url}" alt="{self.name} portrait" width="220" />\n\n'
            if getattr(self, "portrait_url", None)
            else ""
        )

        # Core info
        desc = self.desc_string()
        background = getattr(self.details, "background", "") or ""
        alignment = getattr(self.details, "alignment", "") or ""
        level = getattr(self.details, "level", None)
        xp_val = getattr(getattr(self.details, "xp", None), "value", None)

        summary_lines = ["## Summary\n"]
        summary_lines.append(f"- {desc}")
        if level is not None:
            summary_lines.append(f"- Level: {level}")
        if background:
            summary_lines.append(f"- Background: {background}")
        if alignment:
            summary_lines.append(f"- Alignment: {alignment}")
        if xp_val is not None:
            summary_lines.append(f"- Experience: {xp_val}")
        summary_md = "\n".join(summary_lines) + "\n\n"

        # Wealth
        currency_md = (
            f"**Wealth:** {self.currency.stringify()}\n\n"
            if getattr(self, "currency", None)
            else ""
        )

        # Quick attributes
        # AC
        try:
            ac_val = (
                self.attributes.ac.get("value")
                if isinstance(self.attributes.ac, dict)
                else None
            )
        except Exception:
            ac_val = None
        # HP, Prof, Init, Inspiration, Exhaustion
        hp_max = getattr(getattr(self.attributes, "hp", None), "max", None)
        prof_bonus = getattr(self.attributes, "prof", None)
        init_total = None
        if self.attributes.init is not None:
            try:
                init_total = self.attributes.init.get_check()
            except Exception:
                init_total = None
        inspiration = getattr(self.attributes, "inspiration", False)
        exhaustion = getattr(self.attributes, "exhaustion", 0)

        # Movement and senses (clean formatting)
        movement = (getattr(self.attributes, "movement", {}) or {}).copy()
        senses = (getattr(self.attributes, "senses", {}) or {}).copy()

        unit_mv = movement.pop("units", None)
        unit_sn = senses.pop("units", None)

        def fmt_dist(val, unit):
            if val in (None, "", 0):
                return None
            if unit:
                return f"{val} {unit}"
            return str(val)

        def fmt_caps(s: str) -> str:
            s = s.replace("_", " ").replace("-", " ")
            return s.capitalize()

        def fmt_movement(mv: dict) -> str:
            parts = []
            for k, v in mv.items():
                if v in (None, "", 0) or isinstance(v, (list, dict)):
                    continue
                d = fmt_dist(v, unit_mv)
                if not d:
                    continue
                parts.append(f"{k}: {d}")
            return ", ".join(parts)

        def fmt_senses(sn: dict) -> str:
            parts = []
            for k, v in sn.items():
                if v in (None, "", 0) or isinstance(v, (list, dict)):
                    continue
                d = fmt_dist(v, unit_sn)
                if not d:
                    continue
                parts.append(f"{fmt_caps(k)} {d}")
            return ", ".join(parts)

        movement_str = fmt_movement(movement)
        senses_str = fmt_senses(senses)

        attr_lines = ["## Attributes\n"]
        if ac_val is not None:
            attr_lines.append(f"- AC: {ac_val}")
        if hp_max is not None:
            attr_lines.append(f"- HP: {hp_max} max")
        if prof_bonus is not None:
            attr_lines.append(f"- Proficiency: {fmt_signed(prof_bonus)}")
        if init_total is not None:
            attr_lines.append(f"- Initiative: {fmt_signed(init_total)}")
        attr_lines.append(f"- Inspiration: {'Yes' if inspiration else 'No'}")
        if exhaustion:
            attr_lines.append(f"- Exhaustion: {exhaustion}")
        if movement_str:
            attr_lines.append(f"- Movement: {movement_str}")
        if senses_str:
            attr_lines.append(f"- Senses: {senses_str}")
        attributes_block = "\n".join(attr_lines) + "\n\n"

        # Abilities (simple table; not nested inside other tables)
        abilities_rows = []
        for abrv in ["str", "dex", "con", "int", "wis", "cha"]:
            ability = getattr(self.abilities, abrv)
            name = STAT_ABRV_TO_NAME.get(abrv, abrv.upper())
            score = getattr(ability, "value", None)
            mod = getattr(ability, "mod", None)
            save = getattr(ability, "save", None)
            dc = getattr(ability, "dc", None)
            abilities_rows.append(
                f"| {name} | {score if score is not None else '—'} | {fmt_signed(mod)} | {fmt_signed(save)} | {dc if dc is not None else '—'} |"
            )
        abilities_table = (
            "## Abilities\n"
            "| Ability | Score | Mod | Save | DC |\n"
            "|---|---:|---:|---:|---:|\n" + "\n".join(abilities_rows) + "\n\n"
        )

        # Skills
        skill_order = [
            "acr",
            "ani",
            "arc",
            "ath",
            "dec",
            "his",
            "ins",
            "itm",
            "inv",
            "med",
            "nat",
            "prc",
            "prf",
            "per",
            "rel",
            "slt",
            "ste",
            "sur",
        ]
        skill_lines = ["## Skills\n"]
        for s in skill_order:
            try:
                sk = getattr(self.skills, s)
            except AttributeError:
                continue
            name = STAT_ABRV_TO_NAME.get(s, s.upper())
            total = getattr(sk, "total", None)
            passive = getattr(sk, "passive", None)
            prof = getattr(sk, "proficient", 0)
            prof_str = ""
            try:
                if prof:
                    prof_str = " (proficient)"
            except Exception:
                prof_str = ""
            line = f"- {name}: {fmt_signed(total)}"
            if passive is not None:
                line += f" — passive {passive}"
            line += prof_str
            skill_lines.append(line)
        skills_block = "\n".join(skill_lines) + "\n\n"

        # Tools
        tools_block = ""
        try:
            tools_known = self.tools.known_tool_strings()
        except Exception:
            tools_known = []
        if tools_known:
            tools_block = (
                "## Tools\n" + "\n".join([f"- {t}" for t in tools_known]) + "\n\n"
            )

        # Equipment
        equipment_block = ""
        if self.equipment:
            equipment_block = (
                "## Equipment\n"
                + "\n".join([f"- {e}" for e in self.equipment])
                + "\n\n"
            )

        # Spellcasting (summary only)
        spellcasting_block = ""
        try:
            if getattr(self.attributes, "spellcaster", -1) != -1:
                spelldc = getattr(self.attributes, "spelldc", None)
                spellmod = getattr(self.attributes, "spellmod", None)
                spell_ability = getattr(self.attributes, "spellcasting", "")
                if spell_ability:
                    spell_ability = STAT_ABRV_TO_NAME.get(spell_ability, spell_ability)
                pieces = []
                if spell_ability:
                    pieces.append(f"Ability: {spell_ability}")
                if spelldc is not None:
                    pieces.append(f"DC: {spelldc}")
                if spellmod is not None:
                    pieces.append(f"Mod: +{spellmod}")
                if pieces:
                    spellcasting_block = (
                        "## Spellcasting\n- " + ", ".join(pieces) + "\n\n"
                    )
        except Exception:
            spellcasting_block = ""

        # Attacks & Spells (weapons table; spells grouped by level)
        attacks_block = ""
        if self.weapons:
            weapon_items = [
                w for w in self.weapons if getattr(w, "type", None) == "weapon"
            ]
            spell_items = [
                w for w in self.weapons if getattr(w, "type", None) == "spell"
            ]

            blocks = ["## Attacks & Spells\n"]
            if weapon_items:
                rows = ["| Weapon | Attack | Avg Dmg |", "|---|---|---:|"]
                for w in weapon_items:
                    try:
                        avg_val = calculate_average_damage(
                            getattr(w, "attack", "") or "0"
                        )
                        avg = (
                            f"{avg_val:.1f}"
                            if isinstance(avg_val, (int, float))
                            else "—"
                        )
                    except Exception:
                        avg = "—"
                    attack_str = getattr(w, "attack", "") or ""
                    rows.append(f"| {w.name} | `{attack_str}` | {avg} |")
                blocks.append("\n".join(rows) + "\n\n")
            if spell_items:
                levels: dict[int, list] = {}
                for sp in spell_items:
                    levels.setdefault(getattr(sp, "level", 0), []).append(sp)
                for lvl in sorted(levels.keys()):
                    names = ", ".join(sorted([s.name for s in levels[lvl]]))
                    blocks.append(f"### Spells — Level {lvl}\n")
                    blocks.append(f"{names}\n\n")
            attacks_block = "".join(blocks)

        # Traits & Backstory
        d = self.details
        narrative_lines = ["## Traits & Backstory\n"]
        if getattr(d, "appearance", ""):
            narrative_lines.append(f"- Appearance: {d.appearance}")
        if getattr(d, "trait", ""):
            narrative_lines.append(f"- Trait: {d.trait}")
        if getattr(d, "ideal", ""):
            narrative_lines.append(f"- Ideal: {d.ideal}")
        if getattr(d, "bond", ""):
            narrative_lines.append(f"- Bond: {d.bond}")
        if getattr(d, "flaw", ""):
            narrative_lines.append(f"- Flaw: {d.flaw}")
        try:
            if d.items:
                narrative_lines.append(
                    "- Notable Features: "
                    + ", ".join(sorted([i.name for i in d.items]))
                )
        except Exception:
            pass
        narrative_block = "\n".join(narrative_lines) + "\n\n"

        # Biography (Markdown as-is)
        bio_md = ""
        try:
            biography = d.biography
            bio_text = (biography.public or biography.value or "").strip()
            if bio_text:
                bio_md = "## Biography\n\n" + bio_text + "\n\n"
        except Exception:
            bio_md = ""

        # Assemble
        parts = [
            portrait_html,
            summary_md,
            currency_md,
            attributes_block,
            abilities_table,
            skills_block,
            tools_block,
            equipment_block,
            spellcasting_block,
            attacks_block,
            narrative_block,
            bio_md,
        ]
        return "".join([p for p in parts if p])

    def html_sheet(self) -> str:
        """
        Generate a clean, modern HTML character sheet suitable for a Wiki.js page.
        - Pure HTML output (no Markdown) with semantic sections
        - Minimal inline CSS for readability in Wiki.js
        - No getattr usage; direct attribute access for clarity
        - Converts biography Markdown into simple HTML (headings, lists, paragraphs)
        """

        def e(x: Any) -> str:
            return _html.escape(str(x)) if x is not None else ""

        def fmt_signed(n: int | None) -> str:
            return "—" if n is None else f"{n:+d}"

        # Basic Markdown to HTML (small subset)
        def md_to_html_basic(md: str) -> str:
            if not md:
                return ""
            lines = md.splitlines()
            html_lines: list[str] = []
            in_ul = False
            in_p = False

            def close_ul():
                nonlocal in_ul
                if in_ul:
                    html_lines.append("</ul>")
                    in_ul = False

            def close_p():
                nonlocal in_p
                if in_p:
                    html_lines.append("</p>")
                    in_p = False

            for raw in lines:
                line = raw.rstrip()
                if not line.strip():
                    close_ul()
                    close_p()
                    continue
                if line.startswith("### "):
                    close_ul()
                    close_p()
                    html_lines.append(f"<h3>{e(line[4:])}</h3>")
                    continue
                if line.startswith("## "):
                    close_ul()
                    close_p()
                    html_lines.append(f"<h2>{e(line[3:])}</h2>")
                    continue
                if line.startswith("# "):
                    close_ul()
                    close_p()
                    html_lines.append(f"<h1>{e(line[2:])}</h1>")
                    continue
                if line.startswith("- "):
                    if not in_ul:
                        close_p()
                        html_lines.append("<ul>")
                        in_ul = True
                    html_lines.append(f"<li>{e(line[2:])}</li>")
                    continue
                # paragraph text
                if not in_p:
                    close_ul()
                    html_lines.append("<p>")
                    in_p = True
                html_lines.append(e(line))
            close_ul()
            close_p()
            return "\n".join(html_lines)

        # portrait
        portrait_html = (
            f'<img src="{e(self.portrait_url)}" alt="{e(self.name)} portrait" width="220" />'
            if self.portrait_url
            else ""
        )

        # Core info
        desc = self.desc_string()
        background = self.details.background or ""
        alignment = self.details.alignment or ""
        level = self.details.level
        xp_val = self.details.xp.value

        summary_items = [e(desc)]
        if level is not None:
            summary_items.append(f"Level: {e(level)}")
        if background:
            summary_items.append(f"Background: {e(background)}")
        if alignment:
            summary_items.append(f"Alignment: {e(alignment)}")
        if xp_val is not None:
            summary_items.append(f"Experience: {e(xp_val)}")
        summary_html = "".join([f"<li>{item}</li>" for item in summary_items])
        summary_block = f"<section><h2>Summary</h2><ul>{summary_html}</ul></section>"

        # Wealth
        currency_html = (
            f"<p><strong>Wealth:</strong> {e(self.currency.stringify())}</p>"
            if self.currency
            else ""
        )

        # Attributes quick stats
        ac_val = (
            self.attributes.ac.get("value")
            if isinstance(self.attributes.ac, dict)
            else None
        )
        hp_max = self.attributes.hp.max
        prof_bonus = self.attributes.prof
        init_total = (
            self.attributes.init.get_check()
            if self.attributes.init is not None
            else None
        )
        inspiration = self.attributes.inspiration
        exhaustion = self.attributes.exhaustion

        movement = (self.attributes.movement or {}).copy()
        senses = (self.attributes.senses or {}).copy()
        unit_mv = movement.pop("units", None)
        unit_sn = senses.pop("units", None)

        def fmt_dist(val, unit):
            if val in (None, "", 0):
                return None
            return f"{val} {unit}" if unit else str(val)

        def fmt_caps(s: str) -> str:
            s = s.replace("_", " ").replace("-", " ")
            return s.capitalize()

        def fmt_movement(mv: dict) -> str:
            parts = []
            for k, v in mv.items():
                if v in (None, "", 0) or isinstance(v, (list, dict)):
                    continue
                d = fmt_dist(v, unit_mv)
                if not d:
                    continue
                parts.append(f"{k}: {d}")
            return ", ".join(parts)

        def fmt_senses(sn: dict) -> str:
            parts = []
            for k, v in sn.items():
                if v in (None, "", 0) or isinstance(v, (list, dict)):
                    continue
                d = fmt_dist(v, unit_sn)
                if not d:
                    continue
                parts.append(f"{fmt_caps(k)} {d}")
            return ", ".join(parts)

        movement_str = fmt_movement(movement)
        senses_str = fmt_senses(senses)

        attrs = []
        if ac_val is not None:
            attrs.append(f"<li>AC: {e(ac_val)}</li>")
        if hp_max is not None:
            attrs.append(f"<li>HP: {e(hp_max)} max</li>")
        if prof_bonus is not None:
            attrs.append(f"<li>Proficiency: {e(fmt_signed(prof_bonus))}</li>")
        if init_total is not None:
            attrs.append(f"<li>Initiative: {e(fmt_signed(init_total))}</li>")
        attrs.append(f"<li>Inspiration: {'Yes' if inspiration else 'No'}</li>")
        if exhaustion:
            attrs.append(f"<li>Exhaustion: {e(exhaustion)}</li>")
        if movement_str:
            attrs.append(f"<li>Movement: {e(movement_str)}</li>")
        if senses_str:
            attrs.append(f"<li>Senses: {e(senses_str)}</li>")
        attributes_block = (
            f"<section><h2>Attributes</h2><ul>{''.join(attrs)}</ul></section>"
        )

        # Abilities table
        abilities_rows = []
        for abrv in ["str", "dex", "con", "int", "wis", "cha"]:
            ability = getattr(self.abilities, abrv)
            name = e(STAT_ABRV_TO_NAME.get(abrv, abrv.upper()))
            score = ability.value if hasattr(ability, "value") else None
            mod = ability.mod if hasattr(ability, "mod") else None
            save = ability.save if hasattr(ability, "save") else None
            dc = ability.dc if hasattr(ability, "dc") else None
            abilities_rows.append(
                f'<tr><td>{name}</td><td class="num">{e(score) if score is not None else "—"}</td>'
                f'<td class="num">{e(fmt_signed(mod))}</td><td class="num">{e(fmt_signed(save))}</td>'
                f'<td class="num">{e(dc) if dc is not None else "—"}</td></tr>'
            )
        abilities_table = (
            "<section><h2>Abilities</h2>"
            '<table class="simple"><thead><tr><th>Ability</th><th>Score</th><th>Mod</th><th>Save</th><th>DC</th></tr></thead>'
            f"<tbody>{''.join(abilities_rows)}</tbody></table></section>"
        )

        # Skills list
        skill_order = [
            "acr",
            "ani",
            "arc",
            "ath",
            "dec",
            "his",
            "ins",
            "itm",
            "inv",
            "med",
            "nat",
            "prc",
            "prf",
            "per",
            "rel",
            "slt",
            "ste",
            "sur",
        ]
        skill_items = []
        for s in skill_order:
            sk = getattr(self.skills, s)
            name = e(STAT_ABRV_TO_NAME.get(s, s.upper()))
            total = sk.total if hasattr(sk, "total") else None
            passive = sk.passive if hasattr(sk, "passive") else None
            prof = sk.proficient if hasattr(sk, "proficient") else 0
            prof_str = " (proficient)" if prof else ""
            line = f"{name}: {e(fmt_signed(total))}"
            if passive is not None:
                line += f" — passive {e(passive)}"
            line += prof_str
            skill_items.append(f"<li>{line}</li>")
        skills_block = (
            f"<section><h2>Skills</h2><ul>{''.join(skill_items)}</ul></section>"
        )

        # Tools
        tools_known = self.tools.known_tool_strings()
        tools_block = (
            "<section><h2>Tools</h2><ul>"
            + "".join([f"<li>{e(t)}</li>" for t in tools_known])
            + "</ul></section>"
            if tools_known
            else ""
        )

        # Equipment
        equipment_block = (
            "<section><h2>Equipment</h2><ul>"
            + "".join([f"<li>{e(it)}</li>" for it in self.equipment])
            + "</ul></section>"
            if self.equipment
            else ""
        )

        # Spellcasting summary
        spellcasting_block = ""
        if self.attributes.spellcaster != -1:
            spelldc = self.attributes.spelldc
            spellmod = self.attributes.spellmod
            spell_ability_code = self.attributes.spellcasting
            spell_ability = (
                STAT_ABRV_TO_NAME.get(spell_ability_code, spell_ability_code)
                if spell_ability_code
                else ""
            )
            pieces: list[str] = []
            if spell_ability:
                pieces.append(f"Ability: {e(spell_ability)}")
            if spelldc is not None:
                pieces.append(f"DC: {e(spelldc)}")
            if spellmod is not None:
                pieces.append(f"Mod: +{e(spellmod)}")
            if pieces:
                spellcasting_block = (
                    "<section><h2>Spellcasting</h2><ul>"
                    + "".join([f"<li>{p}</li>" for p in pieces])
                    + "</ul></section>"
                )

        # Attacks & Spells
        attacks_block = ""
        if self.weapons:
            weapon_items = [
                w for w in self.weapons if getattr(w, "type", None) == "weapon"
            ]
            spell_items = [
                w for w in self.weapons if getattr(w, "type", None) == "spell"
            ]
            blocks: list[str] = ["<section><h2>Attacks &amp; Spells</h2>"]
            if weapon_items:
                rows = [
                    '<thead><tr><th>Weapon</th><th>Attack</th><th class="num">Avg Dmg</th></tr></thead>'
                ]
                body: list[str] = []
                for w in weapon_items:
                    try:
                        avg_val = calculate_average_damage(w.attack or "0")
                        avg = (
                            f"{avg_val:.1f}"
                            if isinstance(avg_val, (int, float))
                            else "—"
                        )
                    except Exception:
                        avg = "—"
                    attack_str = w.attack or ""
                    body.append(
                        f'<tr><td>{e(w.name)}</td><td><code>{e(attack_str)}</code></td><td class="num">{e(avg)}</td></tr>'
                    )
                blocks.append(
                    f'<table class="simple">{"".join(rows)}<tbody>{"".join(body)}</tbody></table>'
                )
            if spell_items:
                levels: dict[int, list] = {}
                for sp in spell_items:
                    levels.setdefault(sp.level, []).append(sp)
                for lvl in sorted(levels.keys()):
                    names = ", ".join(sorted([s.name for s in levels[lvl]]))
                    blocks.append(f"<h3>Spells — Level {e(lvl)}</h3>")
                    blocks.append(f"<p>{e(names)}</p>")
            blocks.append("</section>")
            attacks_block = "".join(blocks)

        # Traits & Backstory
        d = self.details
        traits_items: list[str] = []
        if d.appearance:
            traits_items.append(f"<li>Appearance: {e(d.appearance)}</li>")
        if d.trait:
            traits_items.append(f"<li>Trait: {e(d.trait)}</li>")
        if d.ideal:
            traits_items.append(f"<li>Ideal: {e(d.ideal)}</li>")
        if d.bond:
            traits_items.append(f"<li>Bond: {e(d.bond)}</li>")
        if d.flaw:
            traits_items.append(f"<li>Flaw: {e(d.flaw)}</li>")
        if d.items:
            traits_items.append(
                "<li>Notable Features: "
                + e(", ".join(sorted([i.name for i in d.items])))
                + "</li>"
            )
        narrative_block = f"<section><h2>Traits &amp; Backstory</h2><ul>{''.join(traits_items)}</ul></section>"

        # Biography
        bio_html = ""
        bio_text = (d.biography.public or d.biography.value or "").strip()
        if bio_text:
            bio_html = f"<section><h2>Biography</h2>{bio_text}</section>"

        # Styles
        styles = """
        <style>
            :root { --border:#e5e7eb; --muted:#6b7280; --bg:#f8fafc; }
            body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji"; line-height: 1.45; color: #111827; }
            h1, h2, h3 { margin: 0.6em 0 0.4em; }
            img { float: right; margin: 0 0 1rem 1rem; border-radius: 6px; box-shadow: 0 1px 2px rgba(0,0,0,.08); }
            section { margin-bottom: 1rem; clear: both; }
            ul { margin: 0.25rem 0 0.25rem 1.2rem; }
            table.simple { border-collapse: collapse; width: 100%; margin-top: 0.25rem; }
            table.simple th, table.simple td { border: 1px solid var(--border); padding: 6px 8px; }
            table.simple thead th { background: var(--bg); text-align: left; }
            td.num, th.num { text-align: right; }
            code { background: var(--bg); padding: 2px 4px; border-radius: 3px; }
            p { margin: 0.4rem 0; }
            .clearfix::after { content: ""; display: table; clear: both; }
        </style>
        """

        # Assemble full HTML document
        html_doc = [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            f"<title>{e(self.name)}</title>",
            styles,
            "</head>",
            "<body>",
            f'<div class="clearfix">{portrait_html}</div>',
            currency_html,
            summary_block,
            attributes_block,
            abilities_table,
            skills_block,
            tools_block,
            equipment_block,
            spellcasting_block,
            attacks_block,
            narrative_block,
            bio_html,
            "</body>",
            "</html>",
        ]
        return "".join([p for p in html_doc if p])

    @staticmethod
    def _max_weapon_fun(weapon: Weapon) -> int:
        try:
            out = calculate_average_damage(weapon.attack or 0)
        except SyntaxError as e:
            logger.warning(f"{e=}\n{weapon=}")
            out = -1
        return out

    def best_weapon(self) -> Weapon | None:
        weapons = [a for a in self.weapons if isinstance(a, Weapon)]
        return None if not weapons else max(weapons, key=self._max_weapon_fun)

    def elven_accuracy(self) -> bool:
        return next(
            (True for i in self.details.items if i.name == "Elven Accuracy"), False
        )

    def halfling_lucky(self) -> bool:
        return next(
            (True for i in self.details.items if i.name == "Halfling Lucky"), False
        )

    def get_exp(self, guild_settings: GuildSettings) -> int:
        with Session() as session:
            earned = session.scalar(_mission_xp_query(self.id, guild_settings.id)) or 0

            adjustments = (
                session.scalar(
                    select(func.sum(XpAdjustmentsTable.xp)).where(
                        (XpAdjustmentsTable.guild_id == guild_settings.id)
                        & (XpAdjustmentsTable.actor_id == self.id)
                    )
                )
                or 0
            )

            campaigns = session.scalars(
                select(CampaignTable).filter(
                    (CampaignTable.guild_id == guild_settings.id) &
                    (any_(CampaignTable.actor_ids) == self.id)
                )
            ).all()

            if campaigns:
                first_campaign_group: CampaignTable = min(campaigns, key=lambda campaign: snowflake_time(campaign.id))
                starting_lvl = first_campaign_group.starting_level
            else:
                starting_lvl = guild_settings.starting_level

        out = lvl_to_xp[starting_lvl] + earned + adjustments
        return out


def _mission_xp_query(pc_id: str, guild_id: int) -> TextClause:
    return text(textwrap.dedent(f"""
        select sum(q.xp) as xp from (
            select unnest(pcs::text[]) as pc, xp 
            from missions where guild_id={guild_id}
            union all
            select gm_pc as pc, gm_xp as xp 
            from missions where guild_id={guild_id}
        ) as q 
        where pc='{pc_id}'
        group by pc
    """))
