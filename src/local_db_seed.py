import argparse
import json
from pathlib import Path

import httpx
from sqlalchemy import select


def main(args):
    print(f"{args.port=}")
    resp = httpx.get(f"http://localhost:{args.port}/heartbeat")
    if resp.is_success:
        print(resp.content.decode())
    else:
        print(resp.reason_phrase)
        return

    from database import Session
    from database.guild_settings_table import GuildSettingsTable
    from groups.admin import no_init_err_msg
    from models.guild_settings import GuildSettings

    with Session() as session:
        guild_settings_tables = session.scalars(select(GuildSettingsTable)).all()
        if not guild_settings_tables:
            print(no_init_err_msg)
            return
        guild_settings = [
            GuildSettings.model_validate(g) for g in guild_settings_tables
        ]
    pc_data_path: Path = Path(args.pc_data)
    for guild_setting in guild_settings:
        assert pc_data_path.is_file()
        with pc_data_path.open(mode="r", encoding="utf-8") as pc_data_file:
            pc_data = json.load(pc_data_file)
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Guild-Id": str(guild_setting.id),
                "Authorization": str(guild_setting.auth_token),
            }

            for pc in pc_data:
                resp = httpx.put(
                    url=f"http://localhost:{args.port}/actor",
                    data=json.dumps(pc),
                    headers=headers,
                )
                if resp.is_success:
                    print(f"{pc['name']} success!")
                else:
                    print(f"{pc['name']} failed!: {resp.reason_phrase}")

    pc_data_path.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=65435)
    parser.add_argument("--pc_data", type=str)
    main(parser.parse_args())
