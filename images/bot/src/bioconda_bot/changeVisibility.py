import logging
import os
import re
import sys
from asyncio import gather, sleep
from asyncio.subprocess import create_subprocess_exec
from enum import Enum, auto
from pathlib import Path
from shutil import which
from typing import Any, Dict, List, Optional, Set, Tuple
from zipfile import ZipFile, ZipInfo

from aiohttp import ClientSession
from yaml import safe_load

from .common import (
    async_exec,
    fetch_pr_sha_artifacts,
    get_job_context,
    get_pr_comment,
    get_pr_info,
    is_bioconda_member,
    send_comment,
)

logger = logging.getLogger(__name__)
log = logger.info


# Ensure uploaded containers are in repos that have public visibility
# TODO: This should ping @bioconda/core if it fails
async def toggle_visibility(session: ClientSession, container_repo: str) -> None:
    url = f"https://quay.io/api/v1/repository/biocontainers/{container_repo}/changevisibility"
    QUAY_OAUTH_TOKEN = os.environ["QUAY_OAUTH_TOKEN"]
    headers = {
        "Authorization": f"Bearer {QUAY_OAUTH_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {"visibility": "public"}
    rc = 0
    try:
        async with session.post(url, headers=headers, json=body) as response:
            rc = response.status
    except:
        # Do nothing
        pass
    log("Trying to toggle visibility (%s) returned %d", url, rc)


# This requires that a JOB_CONTEXT environment variable, which is made with `toJson(github)`
async def main() -> None:
    job_context = await get_job_context()
    issue_number, original_comment = await get_pr_comment(job_context)
    if issue_number is None or original_comment is None:
        return

    comment = original_comment.lower()
    if comment.startswith(("@bioconda-bot", "@biocondabot")):
        if " please toggle visibility" in comment:
            pkg = comment.split("please change visibility")[1].strip().split()[0]
            async with ClientSession() as session:
                await toggle_visibility(session, pkg)
                await send_comment(session, issue_number, "Visibility changed.")
