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
import gidgethub.sansio

if TYPE_CHECKING:
    import aiohttp

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name

#: State for Github Issues
IssueState = Enum("IssueState", "open closed all")  # pylint: disable=invalid-name

#: State of Github Check Run
#: Pull request review state
ReviewState = Enum(
    "ReviewState", "APPROVED CHANGES_REQUESTED COMMENTED DISMISSED PENDING"
)


class GitHubHandler:
    """Handles interaction with GitHub

    Arguments:
      token: OAUTH token granting permissions to GH
      dry_run: Don't actually modify things if set
      to_user: Target User/Org for PRs
      to_repo: Target repository within **to_user**
    """

    USER = "/user"
    USER_ORGS = "/user/orgs"

    PULLS = "/repos/{user}/{repo}/pulls{/number}{?head,base,state}"
    PULL_REVIEWS = "/repos/{user}/{repo}/pulls/{number}/reviews{/review_id}"
    BRANCH_PROTECTION = "/repos/{user}/{repo}/branches/{branch}/protection"
    ISSUES = "/repos/{user}/{repo}/issues{/number}"
    GET_CHECK_RUNS = "/repos/{user}/{repo}/commits/{commit}/check-runs"
    GET_STATUSES = "/repos/{user}/{repo}/commits/{commit}/statuses"
    ORG_MEMBERS = "/orgs/{user}/members{/username}"
    ORG = "/orgs/{user}"
    ORG_TEAMS = "/orgs/{user}/teams{/team_slug}"

    PROJECT_COL_CARDS = "/projects/columns/{column_id}/cards"
    PROJECT_CARDS = "/projects/columns/cards/{card_id}"

    TEAMS_MEMBERSHIP = "/teams/{team_id}/memberships/{username}"

    SEARCH_ISSUES = "/search/issues?q="

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

    @property
    def rate_limit(self) -> gidgethub.sansio.RateLimit:
        """Last recorded rate limit data"""
        return self.api.rate_limit

    def set_oauth_token(self, token: str) -> None:
        """Update oauth token for the wrapped GitHubAPI object"""
        self.token = token
        self.api.oauth_token = token

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

    async def search_issues(
        self, author=None, pr=False, issue=False, sha=None, closed=None
    ):
        """Search issues/PRs on our repos

        Arguments:
          author: login name of user to search
          sha: SHA of commit to search for
          pr: whether to consider only PRs
          issue: whether to consider only non-PR issues
          closed: search only closed if true, only open if false
        """
        query = ["org:" + self.user]

        if pr and not issue:
            query += ["is:pr"]
        elif issue and not pr:
            query += ["is:issue"]

        if closed is not None:
            if closed:
                query += ["is:closed"]
            else:
                query += ["is:open"]

        if author:
            query += ["author:" + author]
        if sha:
            query += ["sha:" + sha]

        return await self.api.getitem(self.SEARCH_ISSUES + "+".join(query))

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

    async def get_issue(self, number: int) -> dict[str, Any]:
        """Retrieve a single PR or Issue by its number

        Arguments:
          number: PR/Issue number
        Returns:
          The dict will contain a 'pull_request' key (containing dict)
          if the Issue is a PR.
        """
        var_data = copy(self.var_default)
        var_data["number"] = str(number)
        return await self.api.getitem(self.ISSUES, var_data)

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

    async def get_check_runs(self, sha: str) -> list[dict[str, Any]]:
        """List check runs for **sha**

        Arguments:
          sha: The commit SHA for which  to search for check runs
        Returns:
          List of check run "objects"
        """
        var_data = copy(self.var_default)
        var_data["commit"] = sha
        accept = "application/vnd.github.antiope-preview+json"
        res = await self.api.getitem(self.GET_CHECK_RUNS, var_data, accept=accept)
        return res["check_runs"]

    async def get_statuses(self, sha: str) -> list[dict[str, Any]]:
        """List status checks for **sha**

        Arguments:
          sha: The commit SHA for which to find statuses
        Returns:
          List of status "objects"
        """
        var_data = copy(self.var_default)
        var_data["commit"] = sha
        return await self.api.getitem(self.GET_STATUSES, var_data)

    async def get_pr_reviews(self, pr_number: int) -> list[dict[str, Any]]:
        """Get reviews filed for a PR

        Arguments:
          pr_number: Number of PR
        Returns:
          List of dictionaries each having ``body`` (`str`), ``state`` (`ReviewState`),
          and ``commit_id`` (SHA, `str`) as well as a ``user`` `dict`.
        """
        var_data = copy(self.var_default)
        var_data["number"] = str(pr_number)
        return await self.api.getitem(self.PULL_REVIEWS, var_data)

    async def get_branch_protection(self, branch: str = "master") -> dict[str, Any]:
        """Retrieve protection settings for branch

        Arguments:
          branch: Branch for which to get protection settings

        Returns:
          Deep dict as example below. Protections not in place will not be present
          in dict.

          .. code-block:: yaml

             required_status_checks:  # require status checks to pass
                 strict: False        # require PR branch to be up to date with base
                 contexts:            # list of status checks required
                    - bioconda-test
                 enforce_admins:      # admins, too, must follow rules
                    - enabled: True
             required_pull_request_reviews:          # require approving review
                 required_approving_review_count: 1  # 1 - 6 valid
                 dismiss_stale_reviews: False        # auto dismiss approval after push
                 require_code_owner_reviews: False
                 dismissal_restrictions:             # specify who may dismiss reviews
                    users:
                      - login: bla
                    teams:
                      - id: 1
                      - name: Bl Ub
                      - slug: bl-ub
             restrictions:             # specify who may push
               users:
                 - login: bla
               teams:
                 - id: 1
             enforce_admins:
               enabled: True  # apply to admins also
        """
        var_data = copy(self.var_default)
        var_data["branch"] = branch
        accept = "application/vnd.github.luke-cage-preview+json"
        res = await self.api.getitem(self.BRANCH_PROTECTION, var_data, accept=accept)
        return res

    def _deparse_card_pr_number(self, card: dict[str, Any]) -> dict[str, Any]:
        """Extracts the card's issue's number from the content_url

        This is a hack. The card data returned from github does not contain
        content_id or anything referencing the PR/issue except for the
        content_url. We deparse this here manually.

        Arguments:
          card: Card dict as returned from github
        Results:
          Card dict with ``issue_number`` field added if card is not a note
        """
        if "content_url" not in card:  # not content_url to parse
            return card
        if "issue_number" in card:  # target value already taken
            return card

        issue_url = gidgethub.sansio.format_url(self.ISSUES, self.var_default)
        content_url = card["content_url"]
        if content_url.startswith(issue_url):
            try:
                card["issue_number"] = int(content_url.lstrip(issue_url))
            except ValueError:
                pass
        if "issue_number" not in card:
            logger.error(
                "Failed to deparse content url to issue number.\ncontent_url=%s\nissue_url=%s\n",
                content_url,
                issue_url,
            )
        return card

    async def list_project_cards(self, column_id: int) -> list[dict[str, Any]]:
        """List cards in a project column

        Arguments:
          column_id: ID number of project column
        """
        var_data = {"column_id": str(column_id)}
        accept = "application/vnd.github.inertia-preview+json"
        res = await self.api.getitem(self.PROJECT_COL_CARDS, var_data, accept=accept)
        return [self._deparse_card_pr_number(card) for card in res]

    async def delete_project_card(self, card_id: int) -> bool:
        """Deletes a project card

        Arguments:
          card_id: ID of the card to delete
        Returns:
          True if the deletion succeeded
        """
        var_data = {"card_id": str(card_id)}
        accept = "application/vnd.github.inertia-preview+json"
        try:
            await self.api.delete(self.PROJECT_CARDS, var_data, accept=accept)
            return True
        except gidgethub.BadRequest:
            logger.exception("Failed to delete project cards %s", card_id)
            return False


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


class Event(gidgethub.sansio.Event):
    """Adds **get(path)** method to Github Webhook event"""

    def get(self, path: str, altvalue=KeyError) -> str:
        """Get subkeys from even data using slash separated path"""
        data = self.data
        try:
            for item in path.split("/"):
                data = data[item]
        except (KeyError, TypeError):
            if altvalue is KeyError:
                raise KeyError(f"No '{path}' in event type {self.event}") from None
            else:
                return altvalue
        return data
