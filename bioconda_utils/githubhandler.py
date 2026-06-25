"""Wrappers for Github Web-API Bindings"""

from __future__ import annotations

import abc
import logging

from copy import copy
from enum import Enum
from typing import Any, TYPE_CHECKING
from collections.abc import AsyncIterator

import backoff
import cachetools
import gidgethub
import gidgethub.abc
import gidgethub.aiohttp

if TYPE_CHECKING:
    import aiohttp

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name

#: State for Github Issues
IssueState = Enum("IssueState", "open closed all")  # pylint: disable=invalid-name


class GitHubHandler:
    """Handles interaction with GitHub

    Arguments:
      token: OAUTH token granting permissions to GH
      dry_run: Don't actually modify things if set
      to_user: Target User/Org for PRs
      to_repo: Target repository within **to_user**
    """

    USER = "/user"

    PULLS = "/repos/{user}/{repo}/pulls{/number}{?head,base,state}"
    ISSUES = "/repos/{user}/{repo}/issues{/number}"
    ORG_MEMBERS = "/orgs/{user}/members{/username}"
    ORG_TEAMS = "/orgs/{user}/teams{/team_slug}"

    TEAMS_MEMBERSHIP = "/teams/{team_id}/memberships/{username}"

    STATE = IssueState

    def __init__(
        self,
        token: str | None = None,
        dry_run: bool = False,
        to_user: str = "bioconda",
        to_repo: str = "bioconda-recipes",
        installation: int | None = None,
    ) -> None:
        #: API Bearer Token
        self.token = token
        #: If set, no actual modifying actions are taken
        self.dry_run = dry_run
        #: The installation ID if this instance is connected to an App
        self.installation = installation
        #: Owner of the Repo
        self.user = to_user
        #: Name of the Repo
        self.repo = to_repo
        #: Default variables for API calls
        self.var_default: dict[str, Any] = {"user": to_user, "repo": to_repo}

        # filled in by login():
        #: Gidgethub API object
        self.api: Any = None
        #: Login username
        self.username: str | None = None

    def __str__(self):
        return f"{self.user}/{self.repo}"

    def __repr__(self):
        return f"{self.__class__.__name__}({self.user}/{self.repo})"

    @abc.abstractmethod
    def create_api_object(self, *args, **kwargs):
        """Create API object"""

    def get_file_relurl(self, path: str, branch_name: str = "master") -> str:
        """Format domain relative url for **path** on **branch_name**"""
        return "/{user}/{repo}/tree/{branch_name}/{path}".format(
            branch_name=branch_name, path=path, **self.var_default
        )

    async def login(self, *args, **kwargs) -> bool:
        """Log into API (fills `username`)"""

        if self.api is None:
            self.create_api_object(*args, **kwargs)

        self.username = "UNKNOWN [no token]"

        if self.token:
            user = await self.get_user()
            if user:
                self.username = user["login"]
                return True

        return False

    async def get_user(self) -> dict[str, Any]:
        """Fetches the user's info

        Returns:
          Empty dict if the request failed
        """
        try:
            return await self.api.getitem(self.USER)
        except gidgethub.GitHubException:
            return {}

    async def iter_teams(self) -> AsyncIterator[dict[str, Any]]:
        """List organization teams

        Returns:
          Async iterator over dicts, containing **id**,
          **name**, **slug**, **description**, etc.
        """
        async for team in self.api.getiter(self.ORG_TEAMS, self.var_default):
            yield team

    async def get_team_id(
        self, team_slug: str | None = None, team_name: str | None = None
    ) -> int | None:
        """Get the Team ID from the Team slug

        If both are set, **team_slug** is tried first.

        Args:
          team_slug: urlized team name, e.g. "justice-league" for "Justice League"
          team_name: alternative, use normal name (requires extra API call internally)

        Returns:
          Team ID if found, otherwise `None`
        """
        if team_slug:
            var_data = copy(self.var_default)
            var_data["team_slug"] = team_slug
            try:
                team = await self.api.getitem(self.ORG_TEAMS, var_data)
                if team and "id" in team:
                    return team["id"]
            except gidgethub.BadRequest as exc:
                if exc.status_code != 404:
                    raise

        if team_name:
            async for team in self.iter_teams():
                if team.get("name") == team_name:
                    return team.get("id")

    async def is_team_member(self, username: str, team: str | int) -> bool:
        """Check if user is a member of given team

        Args:
          username: Name of user to check
          team: ID, Slug or Name of team to check
        Returns:
          True if the user is a member of the team
        """
        if isinstance(team, int):
            team_id = team
        else:
            team_id = await self.get_team_id(team, team)
            if not team:
                logger.error("Could not find team for name '%s'", team)
                return False

        var_data = {
            "username": username,
            "team_id": team_id,
        }
        accept = "application/vnd.github.hellcat-preview+json"
        try:
            data = await self.api.getitem(
                self.TEAMS_MEMBERSHIP, var_data, accept=accept
            )
            if data["state"] == "active":
                return True
        except gidgethub.BadRequest as exc:
            if exc.status_code != 404:
                raise
        return False

    async def is_member(self, username: str, team: str | int | None = None) -> bool:
        """Check user membership

        Args:
          username: Name of user for whom to check membership
          team: Name, Slug or ID of team to check
        Returns:
          True if the user is member of the organization, and, if **team**
          is provided, if user is member of that team.
        """
        if not username:
            return False
        var_data = copy(self.var_default)
        var_data["username"] = username
        try:
            await self.api.getitem(self.ORG_MEMBERS, var_data)
        except gidgethub.BadRequest:
            logger.debug("User %s is not a member of %s", username, var_data["user"])
            return False
        logger.debug("User %s IS a member of %s", username, var_data["user"])

        if team:
            return await self.is_team_member(username, team)
        return True

    # pylint: disable=too-many-arguments
    @backoff.on_exception(
        backoff.fibo,
        gidgethub.BadRequest,
        max_tries=10,
        giveup=lambda ex: (
            isinstance(ex, gidgethub.BadRequest)
            and ex.status_code not in [429, 502, 503, 504]
        ),
    )
    async def get_prs(
        self,
        from_branch: str | None = None,
        from_user: str | None = None,
        to_branch: str | None = None,
        number: int | None = None,
        state: IssueState | None = None,
    ) -> Any:
        """Retrieve list of PRs matching parameters

        Arguments:
          from_branch: Name of branch from which PR asks to pull
          from_user: Name of user/org in from which to pull
                     (default: from auth)
          to_branch: Name of branch into which to pull (default: master)
          number: PR number
        """
        var_data = copy(self.var_default)
        if not from_user:
            from_user = self.username
        if from_branch:
            if from_user:
                var_data["head"] = f"{from_user}:{from_branch}"
            else:
                var_data["head"] = from_branch
        if to_branch:
            var_data["base"] = to_branch
        if number:
            var_data["number"] = str(number)
        if state:
            var_data["state"] = state.name.lower()

        accept = "application/vnd.github.shadow-cat-preview"  # for draft
        try:
            return await self.api.getitem(self.PULLS, var_data, accept=accept)
        except gidgethub.BadRequest as exc:
            if exc.status_code == 404:
                if number:
                    return {}
                return []
            raise

    # pylint: disable=too-many-arguments
    async def create_pr(
        self,
        title: str,
        from_branch: str,
        from_user: str | None = None,
        to_branch: str | None = "master",
        body: str | None = None,
        maintainer_can_modify: bool = True,
        draft: bool = False,
    ) -> dict[Any, Any]:
        """Create new PR

        Arguments:
          title: Title of new PR
          from_branch: Name of branch from which PR asks to pull (aka head)
          from_user: Name of user/org in from which to pull
          to_branch: Name of branch into which to pull (aka base, default: master)
          body: Body text of PR
          maintainer_can_modify: Whether to allow maintainer to modify from_branch
          draft: whether PR is draft
        """
        var_data = copy(self.var_default)
        if not from_user:
            from_user = self.username
        data: dict[str, Any] = {
            "title": title,
            "base": to_branch,
            "body": body or "",
            "maintainer_can_modify": maintainer_can_modify,
            "draft": draft,
        }

        if from_user and from_user != self.username:
            data["head"] = f"{from_user}:{from_branch}"
        else:
            data["head"] = from_branch

        logger.debug("PR data %s", data)
        if self.dry_run:
            logger.info("Would create PR '%s'", title)
            if title:
                logger.info(" title: %s", title)
            if body:
                logger.info(" body:\n%s\n", body)

            return {"number": -1}
        logger.info("Creating PR '%s'", title)

        accept = "application/vnd.github.shadow-cat-preview"  # for draft
        return await self.api.post(self.PULLS, var_data, data=data, accept=accept)

    async def modify_issue(
        self,
        number: int,
        labels: list[str] | None = None,
        title: str | None = None,
        body: str | None = None,
    ) -> dict[Any, Any]:
        """Modify existing issue (PRs are issues)

        Arguments:
          labels: list of labels to assign to issue
          title: new title
          body: new body
        """
        var_data = copy(self.var_default)
        var_data["number"] = str(number)
        data: dict[str, Any] = {}
        if labels:
            data["labels"] = labels
        if title:
            data["title"] = title
        if body:
            data["body"] = body

        if self.dry_run:
            logger.info("Would modify PR %s", number)
            if title:
                logger.info("New title: %s", title)
            if labels:
                logger.info("New labels: %s", labels)
            if body:
                logger.info("New Body:\n%s\n", body)

            return {"number": number}
        logger.info("Modifying PR %s", number)
        return await self.api.patch(self.ISSUES, var_data, data=data)


class AiohttpGitHubHandler(GitHubHandler):
    """GitHubHandler using Aiohttp for HTTP requests

    Arguments:
      session: Aiohttp Client Session object
      requester: Identify self (e.g. user agent)
    """

    def create_api_object(
        self, session: aiohttp.ClientSession, requester: str, *args, **kwargs
    ) -> None:
        self.api = gidgethub.aiohttp.GitHubAPI(
            session,
            requester,
            oauth_token=self.token,
            cache=cachetools.LRUCache(maxsize=500),
        )
        self.session = session
