#!/usr/bin/env python3
"""Post this push's commit subjects to the Discord changelog channel.

The backend used to announce its own changes: rebuild.sh diffed the deployed
SHA against the pulled one and the bot posted the subjects on startup. That
went away with rebuild.sh, so the range is computed here instead and handed to
the API, which posts it as the bot.

Skips quietly when unconfigured, so forks and self-hosted copies are unaffected,
and never fails the build -- a missed changelog is not a broken deploy.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HEADER = "Discord Bot"

ATTEMPTS = 4
RETRY_SECONDS = 15
HEALTH_WAIT_SECONDS = 120

# Subjects not worth announcing. Carried over from the original implementation.
JUNK = {
    "cleanup",
    "debugging",
    "debug",
    "update reqs",
    "update deps",
    "update requirements",
    "update readme.md",
    "?",
    "why?",
    "???",
}


def subjects(before: str, after: str) -> list[str]:
    """Commit subjects introduced by this push, newest last."""
    # A new branch or a force push reports an all-zero "before"; there is no
    # range to read then, so announce the tip alone.
    if not before or set(before) <= {"0"}:
        args = ["git", "log", "--pretty=%s", "-1", after]
    else:
        args = ["git", "log", "--pretty=%s", "--no-merges", f"{before}..{after}"]

    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        print(f"could not read the commit range: {result.stderr.strip()}")
        return []

    out, seen = [], set()
    for line in reversed(result.stdout.splitlines()):
        subject = line.strip()
        if (
            not subject
            or subject in seen
            or subject.casefold() in JUNK
            or subject.startswith("Merge ")
        ):
            continue
        seen.add(subject)
        out.append(subject)
    return out


def main() -> int:
    changes = subjects(os.environ.get("BEFORE", ""), os.environ.get("AFTER", "HEAD"))
    if not changes:
        print("nothing worth announcing")
        return 0

    # Printed before the config check so a dry run shows what would be sent.
    print(f"{len(changes)} change(s) to announce:")
    for change in changes:
        print(f"  - {change}")

    api_url = os.environ.get("API_URL", "").rstrip("/")
    key = os.environ.get("UPDATE_DISCORD_KEY", "")
    if not api_url or not key:
        print("API_URL or UPDATE_DISCORD_KEY unset; not posting")
        return 0

    # This step runs moments after the image is pushed, which is exactly when
    # watchtower may be restarting the container, so the API can be briefly
    # unreachable: the first run of this got a 502 four seconds into a deploy.
    # Wait for it to answer, then retry a few times on transient failures.
    wait_for_api(api_url)

    body = json.dumps({"title": HEADER, "changes": changes}).encode()
    for attempt in range(1, ATTEMPTS + 1):
        request = urllib.request.Request(
            f"{api_url}/changelog",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                print(f"posted: HTTP {response.status}")
                return 0
        except urllib.error.HTTPError as e:
            detail = e.read()[:200]
            # 4xx is our own fault -- a bad key or payload -- so do not retry.
            if e.code < 500:
                print(f"changelog POST rejected: HTTP {e.code} {detail!r}")
                return 0
            print(f"attempt {attempt}/{ATTEMPTS}: HTTP {e.code} {detail!r}")
        except OSError as e:
            print(f"attempt {attempt}/{ATTEMPTS}: {e}")
        if attempt < ATTEMPTS:
            time.sleep(RETRY_SECONDS)
    print("changelog POST failed; giving up")
    return 0


def wait_for_api(api_url: str) -> None:
    """Give the API a chance to come back if a deploy is in progress."""
    deadline = time.monotonic() + HEALTH_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{api_url}/health", timeout=10) as response:
                if response.status == 200:
                    return
        except (urllib.error.HTTPError, OSError):
            pass
        print("waiting for the API to become reachable...")
        time.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
