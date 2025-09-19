import base64
import os
import re
import textwrap
from urllib import parse

import httpx
import unicodedata

from models.actor import Actor
from utils import getLogger

logger = getLogger(__name__)

_base_url = os.getenv("WIKIJS_URL")
_token = os.getenv("WIKIJS_TOKEN")

valid = _base_url and _token
if not valid:
    logger.warning(f"WikiJS endpoint unset {_base_url=} {_token=}")

_url = parse.urljoin(os.environ["WIKIJS_URL"], "graphql")
_headers = {
    "Accept-Encoding": "gzip, deflate",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Connection": "keep-alive",
    "Authorization": f"Bearer {_token}",
}


def _to_base64(url):
    content = httpx.get(url).content
    return base64.b64encode(content).decode("utf-8")


def _actor_to_graphql_vars(actor: Actor):
    name = unicodedata.normalize("NFD", actor.name)
    name = re.sub(r"[\u0300-\u036f]", "", name)
    name = re.sub(r"\s+", "-", name)
    name = name.replace('"', "").replace("'", "").lower()
    name = parse.quote(name)
    path = f"characters/{name}"

    if actor.details.level > 16:
        tier = "Tier 4: Masters of the World"
    elif actor.details.level > 10:
        tier = "Tier 3: Masters of the Realm"
    elif actor.details.level > 4:
        tier = "Tier 2: Heroes of the Realm"
    else:
        tier = "Tier 1: Local Heroes"

    graphql_vars = {
        # 'content': actor.details.biography.value or '&nbsp;',
        "content": actor.html_sheet(),
        "description": f"level {actor.details.level}",
        "path": path,
        "tags": [
            actor.details.race.split("(")[0].split(";")[0].strip().capitalize(),
            tier,
            *list(actor.classes.keys()),
        ],
        "title": actor.name,
    }

    if actor.details.dead:
        graphql_vars["tags"].append("dead")

    return graphql_vars


def _update_page_query(variables: dict):
    return {
        "query": textwrap.dedent(
            """mutation (
                $id:Int!,
                $content: String, 
                $description: String, 
                $path: String,
                $tags: [String], 
                $title: String) {
                    pages {
                        update(
                        id: $id,
                        content: $content,
                        description: $description,
                        editor: "code",
                        isPublished: true,
                        isPrivate: false,
                        locale: "en",
                        path: $path,
                        tags: $tags,
                        title: $title
                    ) {
                        responseResult {
                            message
                            errorCode
                        }
                    }
                }
            }"""
        ),
        "variables": variables,
    }


def _create_page_query(variables: dict):
    return {
        "query": textwrap.dedent(
            """mutation (
                $content: String!,
                $description: String!,
                $path: String!,
                $tags: [String]!,
                $title: String!) {
                    pages {
                        create(
                            content: $content,
                            description: $description,
                            editor: "code",
                            isPublished: true,
                            isPrivate: false,
                            locale: "en",
                            path: $path,
                            tags: $tags,
                            title: $title
                        ) {
                        responseResult {
                            message
                            errorCode
                        }
                    }
                }
            }"""
        ),
        "variables": variables,
    }


def _delete_page_query(page_id: str):
    return {
        "query": textwrap.dedent(
            """mutation ($id:Int!) {
                pages {
                    delete(id: $id) {
                        responseResult {
                            message
                            errorCode
                        }
                    }
                }
            }"""
        ),
        "variables": {"id": page_id},
    }


def _post_request(page_query):
    return (
        httpx.post(_url, headers=_headers, json=page_query, timeout=600)
        .raise_for_status()
        .json()
    )


def _get_existing_pages():
    return _post_request({"query": "query {pages {list {id path}}}"})["data"]["pages"][
        "list"
    ]


def _throw_on_err(gql):
    errors = [
        f"{name.capitalize()}: {page['responseResult']['message']}"
        for name, page in gql.get("data", {}).get("pages", {}).items()
        if page.get("responseResult", {}).get("errorCode", 0) != 0
    ]
    if errors:
        raise Exception(f"Failed uploading to wiki.\n{'; '.join(errors)}")


def upload_to_wiki(actor: Actor):
    graphql_vars = _actor_to_graphql_vars(actor)
    existing_pages = _get_existing_pages()
    page_id = next(
        (page["id"] for page in existing_pages if page["path"] == graphql_vars["path"]),
        None,
    )
    page_query = (
        _update_page_query({**graphql_vars, "id": page_id})
        if page_id
        else _create_page_query(graphql_vars)
    )
    _throw_on_err(_post_request(page_query))
    logger.info(f"Uploaded {actor.name} to wiki.")


def delete_from_wiki(actor: Actor):
    graphql_vars = _actor_to_graphql_vars(actor)
    existing_pages = _get_existing_pages()
    page_id = next(
        (page["id"] for page in existing_pages if page["path"] == graphql_vars["path"]),
        None,
    )
    if page_id:
        page_query = _delete_page_query(page_id)
        _throw_on_err(_post_request(page_query))
        logger.info(f'Deleted {actor.name} from wiki.')
