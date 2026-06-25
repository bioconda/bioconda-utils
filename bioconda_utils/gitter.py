"""
`Gitter.im <https://gitter.im>`_ Web-API Bindings
"""

from __future__ import annotations

import abc
import logging
import json

from typing import Any, NamedTuple
from collections.abc import AsyncIterator, Mapping

import uritemplate


logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class User(NamedTuple):
    """Gitter User"""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> User:
        """Create `User` from `dict`"""
        return cls(**data)

    #: User ID
    id: str
    #: Gitter username (OAUTH - Github/Gitlab)
    username: str
    #: Gitter displayname (real name)
    displayName: str
    #: Profile URL (relative)
    url: str
    #: Avatar URL
    avatarUrl: str
    #: Small avatar URL
    avatarUrlSmall: str
    #: Medium avatar URL
    avatarUrlMedium: str
    #: Version
    v: str
    #: Gravatar Version (used to force cache flushing)
    gv: str
    #: List of OAUTH providers for user
    providers: list[str] | None = None


class Mention(NamedTuple):
    """Gitter User Mention"""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Mention:
        """Create `User` from `dict`"""
        return cls(**data)

    #: User Name
    screenName: str
    #: User ID
    userId: str | None = None
    #: User IDs
    userIds: list[str] | None = None


class Message(NamedTuple):
    """Gitter Chat Message"""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        """Create `Message` from `dict`"""
        if "mentions" in data:
            data["mentions"] = [Mention.from_dict(user) for user in data["mentions"]]
        if "fromUser" in data:
            data["fromUser"] = User.from_dict(data["fromUser"])
        return cls(**data)

    #: Message ID
    id: str
    #: Message content (markdown)
    text: str
    #: Message content (HTML)
    html: str
    #: Posting timestamp (ISO)
    sent: str
    #: User by whom message was sent
    fromUser: User
    #: Flag indicating whether we have read this
    unread: bool
    #: Number of users who read message
    readBy: int
    #: URLs present in message
    urls: list[str]
    #: @mentions in message
    mentions: list[Mention]
    #: Github #ISSUE references in message
    issues: list[str]
    #: (Unused)
    meta: str
    #: Version
    v: str
    #: Gravatar Version (used to force cache flushing)
    gv: str | None = None
    #: Edit timestamp (ISO)
    editedAt: str | None = None


class Room(NamedTuple):
    """Gitter Chat Room"""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Room:
        """Create `Room` from `dict`"""
        if "user" in data:
            data["user"] = User.from_dict(data["user"])
        return cls(**data)

    #: Room ID
    id: str
    #: Room Name (e.g. ``bioconda/Lobby``)
    name: str
    #: Room Topic
    topic: str
    #: List of users joined in room
    userCount: int
    #: Number of unread mentions of current user
    mentions: list[str]
    #: Flag marking this room as silenced (no notifications)
    lurk: bool
    #: URL to this room for browser
    url: str
    #: Type of room (``ORG``, ``REPO``, ``ONETOONE``,
    #: ``ORG_CHANNEL``, ``REPO_CHANNEL``, ``USER_CHANNEL``)
    githubType: str
    #: List of tags attached to room
    tags: list[str]
    #: Number of unread messages for current user
    unreadItems: int
    #: Gravatar URL
    avatarUrl: str
    #: unknown
    roomMember: str
    #: unknown
    groupId: str
    #: unknown
    public: str
    #: Last time (ISO) room was accessed
    lastAccessTime: str | None = None
    #: Flag marking this room as favorite
    favourite: bool = False
    #: Flag marking personal chats
    oneToOne: bool | None = None
    #: User if one-to-one
    user: User | None = None
    #: Room URI
    uri: str | None = None
    #: Unknown
    security: str | None = None
    #: unknown
    noindex: str | None = None
    #: Unknown
    group: str | None = None
    #: Version
    v: str | None = None


class GitterAPI:
    """Sans-IO Base Class for Gitter API"""

    #: Base URL for Gitter API calls
    _GITTER_API = "https://api.gitter.im/v1"
    #: Base URL for Streaming Gitter API calls
    _GITTER_STREAM_API = "https://stream.gitter.im/v1"

    #: Resource for chat messages (can stream)
    _MESSAGES = "/rooms/{roomId}/chatMessages{/messageId}"
    #: Resource for room listing / searching
    _ROOMS = "/rooms{/roomId}"
    #: Resource for rooms associated with user
    _USER_ROOMS = "/user/{userId}/rooms"
    #: Rsource for unread items
    _UNREAD = "/user/{userId}/rooms/{roomId}/unreadItems"
    #: Resource for rooms associated with user
    _ROOM_USERS = "/rooms/{roomId}/users{/userId}"
    #: Resource for group listing
    _LIST_GROUPS = "/groups"
    #: Resource for current user
    _GET_USER = "/user/me"

    def __init__(self, token: str) -> None:
        self.token = token
        self.debug_once = False

    @abc.abstractmethod
    async def _request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes = b"",
    ) -> tuple[int, Mapping[str, str], bytes]:
        """Execute HTTP request (implemented by IO providing subclass)

        Args:
          method: one of ``GET``, ``POST``, ``PATCH``, etc
          url: HTTP URL to make request at
          headers: Dictionary of headers to send
          body: Body data to send

        Returns:
          Tuple comprising return code, return header dictionary and return data
        """

    @abc.abstractmethod
    def _stream_request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes = b"",
    ) -> AsyncIterator[bytes]:
        """Execute streaming HTTP request (implement by IO providing subclass)

        Args:
          method: one of ``GET``, ``POST``, ``PATCH``, etc
          url: HTTP URL to make request at
          headers: Dictionary of headers to send
          body: Body data to send

        Returns:
          Async iterator over data chunks
        """

    def _prepare_request(
        self,
        url: str,
        var_dict: dict[str, Any],
        data: Any = None,
        charset: str = "utf-8",
        accept: str = "application/json",
    ) -> tuple[str, Mapping[str, str], bytes]:
        """Prepare url, headers and json body for request"""
        url = uritemplate.expand(url, var_dict=var_dict)
        headers = {}
        headers["accept"] = accept
        headers["Authorization"] = "Bearer " + self.token

        body = b""
        if isinstance(data, str):
            body = data.encode(charset)
        elif isinstance(data, Mapping):
            body = json.dumps(data).encode(charset)
            headers["content-type"] = "application/json; charset=" + charset
        headers["content-length"] = str(len(body))
        return url, headers, body

    async def _make_request(
        self,
        method: str,
        url: str,
        var_dict: dict[str, Any],
        data: Any = None,
        accept: str = "application/json",
    ) -> tuple[str, Any]:
        """Make HTTP request"""
        charset = "utf-8"
        url = "".join((self._GITTER_API, url))
        url, headers, body = self._prepare_request(url, var_dict, data, charset, accept)
        status, res_headers, response = await self._request(method, url, headers, body)

        if self.debug_once:
            self.debug_once = False
            logger.error("Called %s / %s", method, url)
            logger.error("Headers: %s", headers)
            logger.error("Body: %s", body)
            logger.error("Result Status: %s", status)
            logger.error("Result Headers: %s", res_headers)
            logger.error("Response: %s", response.decode(charset))

        response_text = response.decode(charset)
        try:
            return response_text, json.loads(response_text)
        except json.decoder.JSONDecodeError:
            logger.error(
                "Call to '%s' yielded text '%s' - not JSON",
                url.replace(self.token, "******"),
                response_text.replace(self.token, "******"),
            )
        return response_text, None

    async def _make_stream_request(
        self,
        method: str,
        url: str,
        var_dict: dict[str, Any],
        data: Any = None,
        accept: str = "application/json",
    ) -> AsyncIterator[Any]:
        """Make streaming HTTP request"""
        charset = "utf-8"
        url = "".join((self._GITTER_STREAM_API, url))
        url, headers, body = self._prepare_request(url, var_dict, data, charset, accept)
        async for line_bytes in self._stream_request(method, url, headers, body):
            line_str = line_bytes.decode(charset)
            if not line_str.strip():
                continue
            try:
                yield json.loads(line_str)
            except json.decoder.JSONDecodeError:
                logger.error("Failed to decode json in line %s", line_str)

    async def get_user(self) -> User:
        """Get current user"""
        _, data = await self._make_request("GET", self._GET_USER, {})
        return User.from_dict(data)
