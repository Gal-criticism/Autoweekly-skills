"""Read an ordered weekly-outro roster from a Feishu Bitable view.

The module uses only the Python standard library. Credentials are read from
the environment, never from task JSON:

* ``FEISHU_USER_ACCESS_TOKEN``, or
* ``FEISHU_APP_ID`` and ``FEISHU_APP_SECRET`` (tenant token refresh).

CLI integration
---------------
The weekly-outro CLI can keep Feishu details in a JSON payload and call:

.. code-block:: python

   import json
   from outro.roster import build_roster_from_config

   with open(task_path, encoding="utf-8") as stream:
       task = json.load(stream)
   roster = build_roster_from_config(task["feishu_roster"])

Expected config::

   {
     "wiki_url": "https://example.feishu.cn/wiki/wikcn...",
     "table_id": "tbl...",
     "view_id": "vew...",
     "issue_field_id": "fld...",
     "issue": "240",
     "roles": {
       "writer": "fld...",
       "reviewer": "fld..."
     },
     "expected_schema_sha256": "optional previous digest",
     "page_size": 100
   }

The returned value is JSON serializable and contains the exact issue's record
ID, ordered roles and deduplicated members, the field schema snapshot, and its
SHA-256 digest. The caller may persist that receipt and pass its
``schema_sha256`` as ``expected_schema_sha256`` on a later run.

For a local XLSX export, the API is optional::

   inspection = inspect_workbook("周报工作表.xlsx")
   snapshot = build_xlsx_roster_snapshot(
       "周报工作表.xlsx",
       sheet_name="...",
       issue_column="期数",
       issue=250,
       roles={"配音": "配音名单", "审核": "审核名单"},
   )

The XLSX reader is deliberately strict: selections are exact and explicit,
duplicate matching rows fail, every role must be nonempty, and a cell string
is treated as one member even when it contains commas or line breaks. The
reader resets each read-only worksheet dimension because Excel exporters can
leave an incorrect ``A1`` dimension in the sheet XML.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time as datetime_time
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, unquote
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)
import zipfile


DEFAULT_API_ORIGIN = "https://open.feishu.cn"
TENANT_TOKEN_PATH = "/open-apis/auth/v3/tenant_access_token/internal"
WIKI_NODE_PATH = "/open-apis/wiki/v2/spaces/get_node"
FIELDS_PATH = "/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
RECORDS_PATH = "/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"

_AUTH_ERROR_CODES = frozenset(
    {
        10003,
        99991661,
        99991663,
        99991668,
    }
)
_TOKEN_REFRESH_MARGIN_SECONDS = 300
_WIKI_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RosterError(Exception):
    """Base class for roster failures safe to show to a user."""


class ConfigurationError(RosterError):
    """The roster configuration or environment variables are invalid."""


class FeishuTransportError(RosterError):
    """The HTTPS transport failed without exposing request credentials."""


class FeishuAPIError(RosterError):
    """Feishu returned an HTTP or API-level error."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str,
        status_code: int | None = None,
        api_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.status_code = status_code
        self.api_code = api_code


class FeishuResponseError(RosterError):
    """A successful response did not conform to the documented shape."""


class SchemaError(RosterError):
    """The Bitable schema is missing, ambiguous, or changed."""


class IssueError(RosterError):
    """The requested issue cannot be selected uniquely."""


class IssueNotFoundError(IssueError):
    """No record contains the requested exact issue value."""


class IssueNotUniqueError(IssueError):
    """More than one record contains the requested exact issue value."""


class EmptyRoleError(RosterError):
    """A required role has no normalized members."""


class WorkbookError(RosterError):
    """A local XLSX file is unsafe, malformed, or cannot be read."""



@dataclass(frozen=True)
class FeishuCredentials:
    """Credentials selected from the environment.

    Secret and token fields are excluded from ``repr`` so routine diagnostics
    cannot accidentally disclose credentials.
    """

    app_id: str | None = None
    app_secret: str | None = field(default=None, repr=False)
    user_access_token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        app_id = (self.app_id or "").strip()
        app_secret = (self.app_secret or "").strip()
        user_token = (self.user_access_token or "").strip()
        if user_token:
            object.__setattr__(self, "user_access_token", user_token)
            return
        if not app_id or not app_secret:
            raise ConfigurationError(
                "Set FEISHU_USER_ACCESS_TOKEN, or set both FEISHU_APP_ID "
                "and FEISHU_APP_SECRET."
            )
        object.__setattr__(self, "app_id", app_id)
        object.__setattr__(self, "app_secret", app_secret)

    @property
    def uses_tenant_token(self) -> bool:
        return self.user_access_token is None


@dataclass(frozen=True)
class TransportResponse:
    """Minimal response returned by an injectable HTTPS transport."""

    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


Transport = Callable[
    [str, str, Mapping[str, str], bytes | None],
    TransportResponse,
]


def _assert_api_url(url: str) -> None:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("Feishu API URL has an invalid port.") from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "open.feishu.cn"
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ConfigurationError(
            "Feishu requests must use the allowlisted origin "
            "https://open.feishu.cn."
        )


class _AllowlistedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        _assert_api_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibTransport:
    """HTTPS transport with redirects restricted to the Feishu API origin."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        if timeout <= 0:
            raise ConfigurationError("Feishu transport timeout must be positive.")
        self._timeout = timeout
        self._opener = build_opener(_AllowlistedRedirectHandler())

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> TransportResponse:
        _assert_api_url(url)
        request = Request(
            url=url,
            data=body,
            headers=dict(headers),
            method=method,
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return TransportResponse(
                    status=int(response.status),
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as exc:
            try:
                body_bytes = exc.read()
            except OSError:
                body_bytes = b""
            return TransportResponse(
                status=int(exc.code),
                body=body_bytes,
                headers=dict(exc.headers.items()) if exc.headers else {},
            )
        except (URLError, OSError) as exc:
            raise FeishuTransportError(
                f"Feishu HTTPS request failed ({type(exc).__name__})."
            ) from None


def credentials_from_env(
    env: Mapping[str, str] | None = None,
) -> FeishuCredentials:
    """Select user or tenant credentials from an environment mapping."""

    values = os.environ if env is None else env
    user_token = (values.get("FEISHU_USER_ACCESS_TOKEN") or "").strip()
    if user_token:
        return FeishuCredentials(user_access_token=user_token)
    return FeishuCredentials(
        app_id=(values.get("FEISHU_APP_ID") or "").strip(),
        app_secret=(values.get("FEISHU_APP_SECRET") or "").strip(),
    )


class FeishuClient:
    """Small synchronous Feishu JSON client with tenant-token refresh."""

    def __init__(
        self,
        credentials: FeishuCredentials,
        *,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.time,
        api_origin: str = DEFAULT_API_ORIGIN,
    ) -> None:
        origin = api_origin.rstrip("/")
        _assert_api_url(origin)
        parsed = urlsplit(origin)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ConfigurationError(
                "Feishu API origin must not contain a path, query, or fragment."
            )
        self._credentials = credentials
        self._transport = transport or UrllibTransport()
        self._clock = clock
        self._origin = DEFAULT_API_ORIGIN
        self._tenant_access_token: str | None = None
        self._tenant_expires_at = 0.0

    def __repr__(self) -> str:
        mode = "tenant" if self._credentials.uses_tenant_token else "user"
        return f"FeishuClient(mode={mode!r})"

    @property
    def credentials(self) -> FeishuCredentials:
        return self._credentials

    def request_json(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        """Request a JSON object, refreshing tenant credentials once if needed."""

        if method not in {"GET", "POST"}:
            raise ConfigurationError(f"Unsupported Feishu HTTP method: {method!r}.")
        url = self._build_url(path, query)
        encoded_body = None
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        }
        if body is not None:
            encoded_body = json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")

        if not authenticated:
            return self._request_checked(method, url, headers, encoded_body)

        token = self._access_token()
        headers["Authorization"] = f"Bearer {token}"
        payload = self._request_checked(
            method,
            url,
            headers,
            encoded_body,
            allow_auth_failure=True,
        )
        if payload is not None:
            return payload

        if not self._credentials.uses_tenant_token:
            raise FeishuAPIError(
                "Feishu rejected FEISHU_USER_ACCESS_TOKEN.",
                endpoint=self._safe_endpoint(path),
                status_code=401,
            )

        token = self._tenant_token(force_refresh=True)
        headers["Authorization"] = f"Bearer {token}"
        return self._request_checked(method, url, headers, encoded_body)

    def _build_url(
        self,
        path: str,
        query: Mapping[str, Any] | None,
    ) -> str:
        if (
            not path.startswith("/open-apis/")
            or "://" in path
            or path.startswith("//")
            or ".." in path.split("/")
        ):
            raise ConfigurationError("Invalid Feishu API path.")
        url = f"{self._origin}{path}"
        if query:
            pairs = [
                (str(key), str(value))
                for key, value in query.items()
                if value is not None
            ]
            if pairs:
                url = f"{url}?{urlencode(pairs)}"
        _assert_api_url(url)
        return url

    def _access_token(self) -> str:
        if not self._credentials.uses_tenant_token:
            token = self._credentials.user_access_token
            if token is None:
                raise ConfigurationError("Feishu user access token is missing.")
            return token
        return self._tenant_token()

    def _tenant_token(self, *, force_refresh: bool = False) -> str:
        now = float(self._clock())
        if (
            not force_refresh
            and self._tenant_access_token
            and self._tenant_expires_at - now > _TOKEN_REFRESH_MARGIN_SECONDS
        ):
            return self._tenant_access_token

        payload = self.request_json(
            "POST",
            TENANT_TOKEN_PATH,
            body={
                "app_id": self._credentials.app_id,
                "app_secret": self._credentials.app_secret,
            },
            authenticated=False,
        )
        token = payload.get("tenant_access_token")
        expire = payload.get("expire")
        if not isinstance(token, str) or not token.strip():
            raise FeishuResponseError(
                "Tenant-token response omitted tenant_access_token."
            )
        try:
            expires_in = float(expire)
        except (TypeError, ValueError):
            raise FeishuResponseError(
                "Tenant-token response contained an invalid expire value."
            ) from None
        if expires_in <= 0:
            raise FeishuResponseError(
                "Tenant-token response contained a non-positive expire value."
            )
        self._tenant_access_token = token.strip()
        self._tenant_expires_at = now + expires_in
        return self._tenant_access_token

    def _request_checked(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        *,
        allow_auth_failure: bool = False,
    ) -> dict[str, Any] | None:
        try:
            response = self._transport(method, url, headers, body)
        except RosterError:
            raise
        except Exception as exc:
            raise FeishuTransportError(
                f"Feishu HTTPS transport failed ({type(exc).__name__})."
            ) from None
        if not isinstance(response, TransportResponse):
            raise FeishuTransportError(
                "Feishu transport returned an unsupported response object."
            )
        payload = self._decode_json_object(response.body, url)
        status = int(response.status)
        code = _api_code(payload)

        if allow_auth_failure and (
            status == 401 or (code is not None and code in _AUTH_ERROR_CODES)
        ):
            return None
        if status < 200 or status >= 300:
            raise self._api_error(
                url,
                status,
                code,
                payload.get("msg"),
            )
        if code != 0:
            raise self._api_error(
                url,
                status,
                code,
                payload.get("msg"),
            )
        return payload

    def _decode_json_object(self, body: bytes, url: str) -> dict[str, Any]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise FeishuResponseError(
                "Feishu returned a non-JSON response from "
                f"{self._safe_endpoint(urlsplit(url).path)}."
            ) from None
        if not isinstance(payload, dict):
            raise FeishuResponseError("Feishu returned a non-object JSON response.")
        return payload

    def _api_error(
        self,
        url: str,
        status: int,
        code: int | None,
        message: object,
    ) -> FeishuAPIError:
        endpoint = self._safe_endpoint(urlsplit(url).path)
        safe_message = self._sanitize(message)
        details = [f"HTTP {status}"]
        if code is not None:
            details.append(f"code {code}")
        if safe_message:
            details.append(safe_message)
        return FeishuAPIError(
            f"Feishu API {endpoint} failed ({', '.join(details)}).",
            endpoint=endpoint,
            status_code=status,
            api_code=code,
        )

    def _safe_endpoint(self, path: str) -> str:
        return path.split("?", 1)[0]

    def _sanitize(self, value: object) -> str:
        if value is None:
            return ""
        text = str(value)
        secrets = [
            self._credentials.app_secret,
            self._credentials.user_access_token,
            self._tenant_access_token,
        ]
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", text)


def _api_code(payload: Mapping[str, Any]) -> int | None:
    raw = payload.get("code")
    if raw is None:
        raise FeishuResponseError("Feishu response omitted the code field.")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise FeishuResponseError("Feishu response contained a non-integer code.") from None


def extract_wiki_node_token(wiki_url_or_token: str) -> str:
    """Return a node token from a raw token or a Feishu/Lark Wiki URL."""

    value = (wiki_url_or_token or "").strip()
    if not value:
        raise ConfigurationError("Wiki URL or node token is required.")
    if "://" not in value:
        if not _WIKI_TOKEN_RE.fullmatch(value):
            raise ConfigurationError("Wiki node token contains unsupported characters.")
        return value

    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed_host = any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in ("feishu.cn", "larksuite.com")
    )
    if (
        parsed.scheme != "https"
        or not allowed_host
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ConfigurationError(
            "Wiki URL must be an HTTPS Feishu or Lark URL."
        )
    segments = [segment for segment in parsed.path.split("/") if segment]
    try:
        wiki_index = segments.index("wiki")
        token = unquote(segments[wiki_index + 1])
    except (ValueError, IndexError):
        raise ConfigurationError(
            "Wiki URL must contain a /wiki/<node_token> path."
        ) from None
    if not _WIKI_TOKEN_RE.fullmatch(token):
        raise ConfigurationError("Wiki URL contains an invalid node token.")
    return token


def resolve_wiki_bitable(
    client: FeishuClient,
    wiki_url_or_token: str,
) -> dict[str, Any]:
    """Resolve a Wiki node to its underlying Bitable app token."""

    node_token = extract_wiki_node_token(wiki_url_or_token)
    payload = client.request_json(
        "GET",
        WIKI_NODE_PATH,
        query={"token": node_token},
    )
    data = _response_data(payload, "Wiki node")
    node = data.get("node")
    if not isinstance(node, dict):
        raise FeishuResponseError("Wiki node response omitted data.node.")
    obj_type = node.get("obj_type")
    obj_token = node.get("obj_token")
    if obj_type != "bitable":
        raise SchemaError(
            f"Wiki node {node_token!r} is {obj_type!r}, not a Bitable."
        )
    if not isinstance(obj_token, str) or not obj_token.strip():
        raise FeishuResponseError("Wiki Bitable node omitted obj_token.")
    return {
        "node_token": node.get("node_token") or node_token,
        "title": node.get("title"),
        "obj_type": obj_type,
        "obj_token": obj_token.strip(),
        "app_token": obj_token.strip(),
    }


def _response_data(
    payload: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise FeishuResponseError(f"{label} response omitted the data object.")
    return data


def _quote_segment(value: str, label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ConfigurationError(f"{label} is required.")
    return quote(text, safe="")


def _normalized_field(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise FeishuResponseError("Field-list item was not an object.")
    field_id = raw.get("field_id")
    field_name = raw.get("field_name")
    if not isinstance(field_id, str) or not field_id.strip():
        raise FeishuResponseError("Field-list item omitted field_id.")
    if not isinstance(field_name, str) or not field_name.strip():
        raise FeishuResponseError(
            f"Field {field_id.strip()!r} omitted field_name."
        )
    return {
        "field_id": field_id.strip(),
        "field_name": field_name.strip(),
        "type": raw.get("type"),
        "ui_type": raw.get("ui_type"),
        "property": raw.get("property"),
        "is_primary": bool(raw.get("is_primary", False)),
    }


def _paginate(
    client: FeishuClient,
    path: str,
    *,
    item_label: str,
    query: Mapping[str, Any],
    page_size: int,
) -> list[dict[str, Any]]:
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size <= 0:
        raise ConfigurationError("page_size must be a positive integer.")
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        params = dict(query)
        params["page_size"] = page_size
        if page_token:
            params["page_token"] = page_token
        payload = client.request_json("GET", path, query=params)
        data = _response_data(payload, item_label)
        page_items = data.get("items")
        has_more = data.get("has_more")
        if not isinstance(page_items, list) or not isinstance(has_more, bool):
            raise FeishuResponseError(
                f"{item_label} response omitted items or has_more."
            )
        for item in page_items:
            if not isinstance(item, dict):
                raise FeishuResponseError(
                    f"{item_label} response contained a non-object item."
                )
            items.append(item)
        if not has_more:
            return items
        next_token = data.get("page_token")
        if not isinstance(next_token, str) or not next_token.strip():
            raise FeishuResponseError(
                f"{item_label} response had has_more=true without page_token."
            )
        next_token = next_token.strip()
        if next_token in seen_tokens or next_token == page_token:
            raise FeishuResponseError(
                f"{item_label} pagination repeated page_token."
            )
        seen_tokens.add(next_token)
        page_token = next_token


def discover_fields(
    client: FeishuClient,
    app_token: str,
    table_id: str,
    *,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """Discover and normalize all fields in a Bitable table."""

    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or page_size <= 0
    ):
        raise ConfigurationError("page_size must be a positive integer.")
    app_segment = _quote_segment(app_token, "app_token")
    table_segment = _quote_segment(table_id, "table_id")
    path = FIELDS_PATH.format(
        app_token=app_segment,
        table_id=table_segment,
    )
    raw_items = _paginate(
        client,
        path,
        item_label="Field list",
        query={},
        page_size=min(page_size, 100),
    )
    fields = [_normalized_field(item) for item in raw_items]
    ids = [item["field_id"] for item in fields]
    names = [item["field_name"] for item in fields]
    if len(ids) != len(set(ids)):
        raise SchemaError("Bitable field IDs are not unique.")
    if len(names) != len(set(names)):
        raise SchemaError("Bitable field names are not unique.")
    return fields


def paginate_view_records(
    client: FeishuClient,
    app_token: str,
    table_id: str,
    view_id: str,
    *,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """Read every record reachable through an explicit Bitable view."""

    app_segment = _quote_segment(app_token, "app_token")
    table_segment = _quote_segment(table_id, "table_id")
    view_value = (view_id or "").strip()
    if not view_value:
        raise ConfigurationError("view_id is required.")
    path = RECORDS_PATH.format(
        app_token=app_segment,
        table_id=table_segment,
    )
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 500:
        raise ConfigurationError("Record page_size must be between 1 and 500.")
    return _paginate(
        client,
        path,
        item_label="Record list",
        query={"view_id": view_value},
        page_size=page_size,
    )


def normalize_role_bindings(
    raw_roles: Mapping[str, str] | Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    """Normalize ordered role-to-field mappings.

    A mapping preserves its insertion order. A sequence of
    ``{"role": ..., "field_id": ...}`` objects is also accepted for explicit
    JSON ordering.
    """

    entries: list[tuple[object, object]] = []
    if isinstance(raw_roles, Mapping):
        entries = list(raw_roles.items())
    elif isinstance(raw_roles, Sequence) and not isinstance(
        raw_roles, (str, bytes, bytearray)
    ):
        for raw in raw_roles:
            if not isinstance(raw, Mapping):
                raise ConfigurationError(
                    "Role list entries must contain role and field_id."
                )
            entries.append((raw.get("role"), raw.get("field_id")))
    else:
        raise ConfigurationError("roles must be an object or an ordered list.")
    if not entries:
        raise EmptyRoleError("At least one explicit role mapping is required.")

    output: list[dict[str, str]] = []
    seen_roles: set[str] = set()
    for raw_role, raw_field_id in entries:
        if not isinstance(raw_role, str) or not raw_role.strip():
            raise EmptyRoleError("Role names must be non-empty strings.")
        if not isinstance(raw_field_id, str) or not raw_field_id.strip():
            raise EmptyRoleError(
                f"Role {raw_role.strip()!r} has an empty field ID."
            )
        role = raw_role.strip()
        field_id = raw_field_id.strip()
        if role in seen_roles:
            raise ConfigurationError(f"Role {role!r} is duplicated.")
        seen_roles.add(role)
        output.append({"role": role, "field_id": field_id})
    return output


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def bind_schema(
    fields: Sequence[Mapping[str, Any]],
    *,
    table_id: str,
    view_id: str,
    issue_field_id: str,
    roles: Mapping[str, str] | Sequence[Mapping[str, str]],
    expected_schema_sha256: str | None = None,
) -> dict[str, Any]:
    """Bind discovered fields to the view, issue, and ordered role mappings."""

    normalized_roles = normalize_role_bindings(roles)
    normalized_fields = [_normalized_field(dict(item)) for item in fields]
    field_ids = {item["field_id"] for item in normalized_fields}
    issue_field = (issue_field_id or "").strip()
    if not issue_field:
        raise ConfigurationError("issue_field_id is required.")
    if issue_field not in field_ids:
        raise SchemaError(f"Issue field {issue_field!r} does not exist.")
    for binding in normalized_roles:
        if binding["field_id"] not in field_ids:
            raise SchemaError(
                f"Role {binding['role']!r} references missing field "
                f"{binding['field_id']!r}."
            )

    normalized_fields.sort(key=lambda item: item["field_id"])
    snapshot = {
        "table_id": (table_id or "").strip(),
        "view_id": (view_id or "").strip(),
        "issue_field_id": issue_field,
        "roles": normalized_roles,
        "fields": normalized_fields,
    }
    if not snapshot["table_id"] or not snapshot["view_id"]:
        raise ConfigurationError("table_id and view_id are required.")
    digest = _canonical_hash(snapshot)
    if expected_schema_sha256 is not None:
        expected = expected_schema_sha256.strip().lower()
        if not _SHA256_RE.fullmatch(expected):
            raise ConfigurationError(
                "expected_schema_sha256 must be a lowercase SHA-256 digest."
            )
        if expected != digest:
            raise SchemaError(
                "Feishu roster schema/binding changed: expected "
                f"{expected}, observed {digest}."
            )
    return {
        **snapshot,
        "sha256": digest,
        "schema_sha256": digest,
    }


def _field_value(
    record: Mapping[str, Any],
    field: Mapping[str, Any],
) -> object:
    raw_fields = record.get("fields")
    if not isinstance(raw_fields, dict):
        raise FeishuResponseError("Record omitted the fields object.")
    field_id = field["field_id"]
    field_name = field["field_name"]
    if field_id in raw_fields:
        return raw_fields[field_id]
    if field_name in raw_fields:
        return raw_fields[field_name]
    return None


def _issue_fragments(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, Mapping):
        if "text" in value:
            return _issue_fragments(value.get("text"))
        if "value" in value:
            return _issue_fragments(value.get("value"))
        if "identity" in value or "id" in value:
            return _issue_fragments(
                value.get("display")
                or value.get("name")
                or value.get("identity")
                or value.get("id")
            )
        return []
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        fragments: list[str] = []
        for item in value:
            fragments.extend(_issue_fragments(item))
        return fragments
    return [str(value)]


def _issue_text(value: object) -> str:
    return "".join(_issue_fragments(value)).strip()


def select_issue_record(
    records: Sequence[Mapping[str, Any]],
    *,
    issue: object,
    issue_field: Mapping[str, Any],
) -> dict[str, Any]:
    """Select exactly one record whose explicit issue field matches exactly."""

    target = _issue_text(issue)
    if not target:
        raise ConfigurationError("issue must not be empty.")
    matches = [
        dict(record)
        for record in records
        if _issue_text(_field_value(record, issue_field)) == target
    ]
    field_label = f"{issue_field['field_name']} ({issue_field['field_id']})"
    if not matches:
        raise IssueNotFoundError(
            f"No record matched issue {target!r} exactly in {field_label}."
        )
    if len(matches) > 1:
        record_ids = [
            str(record.get("record_id") or "<missing>")
            for record in matches
        ]
        raise IssueNotUniqueError(
            f"Issue {target!r} matched multiple records: {', '.join(record_ids)}."
        )
    return matches[0]


def _member_from_mapping(raw: Mapping[str, Any]) -> dict[str, str] | None:
    identity = raw.get("identity")
    display = raw.get("display")
    for key in ("id", "open_id", "union_id", "user_id"):
        if not identity and raw.get(key):
            identity = raw.get(key)
            break
    if not display:
        display = (
            raw.get("name")
            or raw.get("en_name")
            or raw.get("email")
            or raw.get("text")
        )
    if identity is None and raw.get("text") is not None:
        identity = raw.get("text")
    if display is None:
        display = identity
    if identity is None and display is None:
        return None
    identity_text = str(identity if identity is not None else display).strip()
    display_text = str(display if display is not None else identity).strip()
    if not identity_text and not display_text:
        return None
    return {
        "identity": identity_text or display_text,
        "display": display_text or identity_text,
    }


def _iter_members(value: object) -> list[dict[str, str]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        if set(value).issubset({"value"}) and "value" in value:
            return _iter_members(value.get("value"))
        member = _member_from_mapping(value)
        return [member] if member else []
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        members: list[dict[str, str]] = []
        for item in value:
            members.extend(_iter_members(item))
        return members
    text = str(value).strip()
    if not text:
        return []
    return [{"identity": text, "display": text}]


def normalize_members(value: object) -> list[dict[str, str]]:
    """Normalize list/person rich values and dedupe by identity in order."""

    members: list[dict[str, str]] = []
    seen: set[str] = set()
    for member in _iter_members(value):
        identity = member["identity"].strip()
        display = member["display"].strip()
        if not identity:
            continue
        if identity in seen:
            continue
        seen.add(identity)
        members.append({"identity": identity, "display": display})
    return members


def build_roster(
    *,
    client: FeishuClient,
    wiki_url_or_token: str,
    table_id: str,
    view_id: str,
    issue_field_id: str,
    issue: object,
    roles: Mapping[str, str] | Sequence[Mapping[str, str]],
    expected_schema_sha256: str | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    """Resolve, validate, page, select, and normalize a weekly roster."""

    normalized_roles = normalize_role_bindings(roles)
    resolved = resolve_wiki_bitable(client, wiki_url_or_token)
    app_token = resolved["app_token"]
    fields = discover_fields(
        client,
        app_token,
        table_id,
        page_size=page_size,
    )
    schema = bind_schema(
        fields,
        table_id=table_id,
        view_id=view_id,
        issue_field_id=issue_field_id,
        roles=normalized_roles,
        expected_schema_sha256=expected_schema_sha256,
    )
    records = paginate_view_records(
        client,
        app_token,
        table_id,
        view_id,
        page_size=page_size,
    )
    fields_by_id = {
        item["field_id"]: item
        for item in schema["fields"]
    }
    issue_field = fields_by_id[schema["issue_field_id"]]
    selected = select_issue_record(
        records,
        issue=issue,
        issue_field=issue_field,
    )

    role_results: list[dict[str, Any]] = []
    for binding in schema["roles"]:
        role = binding["role"]
        field = fields_by_id[binding["field_id"]]
        members = normalize_members(_field_value(selected, field))
        if not members:
            raise EmptyRoleError(
                f"Role {role!r} is empty in issue "
                f"{_issue_text(issue)!r} (field {field['field_id']})."
            )
        role_results.append(
            {
                "role": role,
                "field_id": field["field_id"],
                "field_name": field["field_name"],
                "members": members,
            }
        )

    return {
        "issue": _issue_text(issue),
        "record_id": selected.get("record_id"),
        "roles": role_results,
        "schema": schema,
        "schema_sha256": schema["sha256"],
        "bitable_app_token": app_token,
        "wiki_node_token": resolved["node_token"],
    }


def build_roster_from_config(
    config: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    transport: Transport | None = None,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Build a roster from JSON-compatible config and environment credentials."""

    if not isinstance(config, Mapping):
        raise ConfigurationError("Roster config must be an object.")
    allowed = {
        "wiki_url",
        "table_id",
        "view_id",
        "issue_field_id",
        "issue",
        "roles",
        "expected_schema_sha256",
        "page_size",
    }
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise ConfigurationError(
            f"Unknown roster config keys: {', '.join(unknown)}."
        )
    required = {
        "wiki_url",
        "table_id",
        "view_id",
        "issue_field_id",
        "issue",
        "roles",
    }
    missing = sorted(required - set(config))
    if missing:
        raise ConfigurationError(
            f"Missing roster config keys: {', '.join(missing)}."
        )

    credentials = credentials_from_env(env)
    client = FeishuClient(
        credentials,
        transport=transport,
        clock=clock,
    )
    page_size = config.get("page_size", 100)
    if isinstance(page_size, bool) or not isinstance(page_size, int):
        raise ConfigurationError("page_size must be an integer.")
    expected = config.get("expected_schema_sha256")
    if expected is not None and not isinstance(expected, str):
        raise ConfigurationError("expected_schema_sha256 must be a string.")
    return build_roster(
        client=client,
        wiki_url_or_token=config["wiki_url"],
        table_id=config["table_id"],
        view_id=config["view_id"],
        issue_field_id=config["issue_field_id"],
        issue=config["issue"],
        roles=config["roles"],
        expected_schema_sha256=expected,
        page_size=page_size,
    )


def _checked_workbook_path(
    path: str | os.PathLike[str],
    *,
    max_file_size: int,
    max_uncompressed_size: int,
) -> Path:
    try:
        workbook_path = Path(path).expanduser()
    except TypeError:
        raise ConfigurationError("XLSX path must be a filesystem path.") from None
    if workbook_path.suffix.casefold() != ".xlsx":
        raise WorkbookError("Only .xlsx workbook files are accepted.")
    try:
        if workbook_path.is_symlink():
            raise WorkbookError("Symbolic-link XLSX paths are not accepted.")
        resolved = workbook_path.resolve(strict=True)
    except FileNotFoundError:
        raise WorkbookError(f"XLSX workbook does not exist: {workbook_path}.") from None
    except OSError as exc:
        raise WorkbookError(
            f"XLSX workbook cannot be accessed ({type(exc).__name__})."
        ) from None
    if not resolved.is_file():
        raise WorkbookError("XLSX path must refer to a regular file.")
    file_size = resolved.stat().st_size
    if file_size <= 0:
        raise WorkbookError("XLSX workbook is empty.")
    if file_size > max_file_size:
        raise WorkbookError(
            f"XLSX workbook exceeds the {max_file_size}-byte file-size limit."
        )
    try:
        with zipfile.ZipFile(resolved) as archive:
            entries = archive.infolist()
            if len(entries) > 10_000:
                raise WorkbookError("XLSX package contains too many ZIP entries.")
            names: set[str] = set()
            total_uncompressed = 0
            for entry in entries:
                name = entry.filename
                if (
                    not name
                    or name.startswith(("/", "\\"))
                    or "\\" in name
                    or any(part == ".." for part in name.split("/"))
                ):
                    raise WorkbookError("XLSX package contains an unsafe ZIP path.")
                if name in names:
                    raise WorkbookError("XLSX package contains duplicate ZIP paths.")
                names.add(name)
                if entry.flag_bits & 0x1:
                    raise WorkbookError("Encrypted XLSX packages are not supported.")
                if entry.file_size < 0:
                    raise WorkbookError("XLSX package contains an invalid ZIP entry.")
                total_uncompressed += entry.file_size
                if total_uncompressed > max_uncompressed_size:
                    raise WorkbookError(
                        "XLSX package exceeds the uncompressed-size limit."
                    )
                if entry.file_size > 1_048_576 and entry.compress_size:
                    if entry.file_size / entry.compress_size > 500:
                        raise WorkbookError(
                            "XLSX package has a suspicious ZIP compression ratio."
                        )
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise WorkbookError("File is not a valid XLSX package.")
    except zipfile.BadZipFile:
        raise WorkbookError("File is not a valid XLSX package.") from None
    except OSError as exc:
        raise WorkbookError(
            f"XLSX package cannot be read ({type(exc).__name__})."
        ) from None
    return resolved


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _xlsx_scalar(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    return str(value)


def inspect_workbook(
    path: str | os.PathLike[str],
    *,
    max_file_size: int = 64 * 1024 * 1024,
    max_uncompressed_size: int = 512 * 1024 * 1024,
    max_rows_per_sheet: int = 100_000,
    max_cells: int = 2_000_000,
) -> dict[str, Any]:
    """Safely read all populated worksheet rows with ``openpyxl``.

    Worksheets are opened with ``read_only=True``, ``data_only=True``, and
    ``keep_links=False``. Every worksheet calls ``reset_dimensions()`` before
    iteration so a stale exporter dimension such as ``A1`` cannot hide the
    actual rows. String cells are returned unchanged.
    """

    for label, value in (
        ("max_file_size", max_file_size),
        ("max_uncompressed_size", max_uncompressed_size),
        ("max_rows_per_sheet", max_rows_per_sheet),
        ("max_cells", max_cells),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ConfigurationError(f"{label} must be a positive integer.")
    workbook_path = _checked_workbook_path(
        path,
        max_file_size=max_file_size,
        max_uncompressed_size=max_uncompressed_size,
    )
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ConfigurationError(
            "openpyxl is required to inspect an XLSX workbook."
        ) from None

    try:
        workbook = load_workbook(
            filename=workbook_path,
            read_only=True,
            data_only=True,
            keep_links=False,
        )
    except Exception as exc:
        raise WorkbookError(
            f"XLSX workbook could not be opened ({type(exc).__name__})."
        ) from None

    sheets: list[dict[str, Any]] = []
    total_cells = 0
    try:
        for sheet_index, worksheet in enumerate(workbook.worksheets):
            worksheet.reset_dimensions()
            rows: list[dict[str, Any]] = []
            nonempty_rows = 0
            column_count = 0
            for row_number, row in enumerate(
                worksheet.iter_rows(values_only=True),
                start=1,
            ):
                if row_number > max_rows_per_sheet:
                    raise WorkbookError(
                        f"Worksheet {worksheet.title!r} exceeds the "
                        f"{max_rows_per_sheet}-row limit."
                    )
                values = [_xlsx_scalar(value) for value in row]
                total_cells += len(values)
                if total_cells > max_cells:
                    raise WorkbookError(
                        f"XLSX workbook exceeds the {max_cells}-cell limit."
                    )
                column_count = max(column_count, len(values))
                if any(value not in (None, "") for value in values):
                    nonempty_rows += 1
                rows.append({"row": row_number, "values": values})
            sheets.append(
                {
                    "name": worksheet.title,
                    "index": sheet_index,
                    "state": worksheet.sheet_state,
                    "rows": rows,
                    "row_count": len(rows),
                    "nonempty_row_count": nonempty_rows,
                    "column_count": column_count,
                }
            )
    finally:
        workbook.close()

    return {
        "path": str(workbook_path),
        "file_name": workbook_path.name,
        "file_size": workbook_path.stat().st_size,
        "sha256": _sha256_path(workbook_path),
        "sheets": sheets,
    }


def _normalize_xlsx_roles(
    raw_roles: (
        Mapping[str, str | int]
        | Sequence[Mapping[str, str | int]]
    ),
) -> list[dict[str, str | int]]:
    entries: list[tuple[object, object]] = []
    if isinstance(raw_roles, Mapping):
        entries = list(raw_roles.items())
    elif isinstance(raw_roles, Sequence) and not isinstance(
        raw_roles, (str, bytes, bytearray)
    ):
        for raw in raw_roles:
            if not isinstance(raw, Mapping):
                raise ConfigurationError(
                    "XLSX role entries must contain role and column."
                )
            entries.append(
                (
                    raw.get("role"),
                    raw.get("column", raw.get("field_id")),
                )
            )
    else:
        raise ConfigurationError("roles must be an object or an ordered list.")
    if not entries:
        raise EmptyRoleError("At least one explicit XLSX role mapping is required.")

    output: list[dict[str, str | int]] = []
    seen_roles: set[str] = set()
    for raw_role, raw_column in entries:
        if not isinstance(raw_role, str) or not raw_role.strip():
            raise EmptyRoleError("Role names must be non-empty strings.")
        role = raw_role.strip()
        if role in seen_roles:
            raise ConfigurationError(f"Role {role!r} is duplicated.")
        if isinstance(raw_column, str):
            if not raw_column:
                raise EmptyRoleError(f"Role {role!r} has an empty column.")
            column: str | int = raw_column
        elif isinstance(raw_column, int) and not isinstance(raw_column, bool):
            if raw_column <= 0:
                raise ConfigurationError(
                    f"Role {role!r} column must be a positive integer."
                )
            column = raw_column
        else:
            raise EmptyRoleError(
                f"Role {role!r} needs an exact header name or column number."
            )
        seen_roles.add(role)
        output.append({"role": role, "column": column})
    return output


def _resolve_xlsx_column(
    header_values: Sequence[object],
    selector: str | int,
    *,
    label: str,
) -> int:
    if isinstance(selector, int):
        if selector > len(header_values):
            raise SchemaError(
                f"{label} column {selector} is outside the header row."
            )
        return selector
    matches = [
        index
        for index, value in enumerate(header_values, start=1)
        if value == selector
    ]
    if not matches:
        raise SchemaError(f"{label} header {selector!r} was not found exactly.")
    if len(matches) > 1:
        raise SchemaError(f"{label} header {selector!r} is not unique.")
    return matches[0]


def _xlsx_issue_key(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value))
        except Exception:
            return str(value).strip()
        if not number.is_finite():
            return str(value).strip()
        if number == number.to_integral_value():
            return str(number.quantize(Decimal(1)))
        return format(number.normalize(), "f")
    return str(value).strip()


def _xlsx_member(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    else:
        text = str(_xlsx_scalar(value))
    return text if text.strip() else None


def _xlsx_members(
    value: object,
    separators: Sequence[str],
) -> list[str]:
    member_text = _xlsx_member(value)
    if member_text is None:
        return []
    if not separators or not any(separator in member_text for separator in separators):
        return [member_text]
    pattern = "|".join(re.escape(separator) for separator in separators)
    members: list[str] = []
    seen: set[str] = set()
    for part in re.split(pattern, member_text):
        text = part.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        members.append(text)
    return members


def _normalize_separator_policy(
    raw_policy: Mapping[str, Sequence[str]] | None,
    resolved_roles: Sequence[Mapping[str, Any]],
) -> list[tuple[str, tuple[str, ...]]]:
    if raw_policy is None:
        return []
    if not isinstance(raw_policy, Mapping):
        raise ConfigurationError(
            "member_separators must map role/header names to separator lists."
        )
    known_keys = {
        str(item["role"])
        for item in resolved_roles
    } | {
        str(item["column_header"])
        for item in resolved_roles
    }
    invalid_keys = [
        key
        for key in raw_policy
        if not isinstance(key, str)
    ]
    if invalid_keys:
        raise ConfigurationError(
            "member_separators keys must be exact role/header names."
        )
    unknown = sorted(set(raw_policy) - known_keys)
    if unknown:
        raise ConfigurationError(
            "member_separators contains unknown roles/headers: "
            f"{', '.join(unknown)}."
        )
    output: list[tuple[str, tuple[str, ...]]] = []
    for key, raw_separators in raw_policy.items():
        if (
            not isinstance(raw_separators, Sequence)
            or isinstance(raw_separators, (str, bytes, bytearray))
            or not raw_separators
        ):
            raise ConfigurationError(
                f"member_separators[{key!r}] must be a nonempty list."
            )
        separators: list[str] = []
        for separator in raw_separators:
            if not isinstance(separator, str) or not separator:
                raise ConfigurationError(
                    f"member_separators[{key!r}] contains an empty separator."
                )
            separators.append(separator)
        output.append((key, tuple(separators)))
    return output


def _separators_for_role(
    policy: Sequence[tuple[str, tuple[str, ...]]],
    binding: Mapping[str, Any],
) -> tuple[str, ...]:
    selected: list[str] = []
    for key, separators in policy:
        if key in (binding["role"], binding["column_header"]):
            for separator in separators:
                if separator not in selected:
                    selected.append(separator)
    return tuple(selected)


def build_xlsx_roster_snapshot(
    path: str | os.PathLike[str],
    *,
    sheet_name: str,
    issue_column: str | int,
    issue: object,
    roles: (
        Mapping[str, str | int]
        | Sequence[Mapping[str, str | int]]
    ),
    member_separators: Mapping[str, Sequence[str]] | None = None,
    header_row: int = 1,
) -> dict[str, Any]:
    """Build a strict, nonempty snapshot from one exact XLSX issue row.

    ``issue_column`` and every role column must be selected explicitly, either
    as an exact header value or as a one-based column number. Each nonblank
    member cell is retained verbatim as one member; commas and line breaks are
    never interpreted as separators unless ``member_separators`` explicitly
    maps that exact role name or header to literal separators. Duplicate
    matching issue rows and empty roles are errors.
    """

    if isinstance(header_row, bool) or not isinstance(header_row, int) or header_row <= 0:
        raise ConfigurationError("header_row must be a positive integer.")
    role_bindings = _normalize_xlsx_roles(roles)
    inspection = inspect_workbook(path)
    matching_sheets = [
        sheet
        for sheet in inspection["sheets"]
        if sheet["name"] == sheet_name
    ]
    if not matching_sheets:
        raise SchemaError(f"Worksheet {sheet_name!r} was not found.")
    if len(matching_sheets) > 1:
        raise SchemaError(f"Worksheet {sheet_name!r} is not unique.")
    sheet = matching_sheets[0]
    rows_by_number = {
        row["row"]: row["values"]
        for row in sheet["rows"]
    }
    header_values = rows_by_number.get(header_row)
    if header_values is None:
        raise SchemaError(f"Header row {header_row} is missing.")

    issue_column_index = _resolve_xlsx_column(
        header_values,
        issue_column,
        label="Issue",
    )
    resolved_roles: list[dict[str, Any]] = []
    for binding in role_bindings:
        column_index = _resolve_xlsx_column(
            header_values,
            binding["column"],
            label=f"Role {binding['role']!r}",
        )
        resolved_roles.append(
            {
                **binding,
                "column_index": column_index,
                "column_header": _xlsx_scalar(header_values[column_index - 1]),
            }
        )
    separator_policy = _normalize_separator_policy(
        member_separators,
        resolved_roles,
    )
    for binding in resolved_roles:
        binding["separators"] = list(
            _separators_for_role(separator_policy, binding)
        )

    target_key = _xlsx_issue_key(issue)
    if not target_key:
        raise ConfigurationError("issue must not be empty.")
    matching_rows: list[tuple[int, Sequence[object]]] = []
    for row_number in sorted(rows_by_number):
        if row_number <= header_row:
            continue
        values = rows_by_number[row_number]
        cell = (
            values[issue_column_index - 1]
            if issue_column_index <= len(values)
            else None
        )
        if _xlsx_issue_key(cell) == target_key:
            matching_rows.append((row_number, values))
    if not matching_rows:
        raise IssueNotFoundError(
            f"No worksheet row matched issue {target_key!r} exactly."
        )
    if len(matching_rows) > 1:
        row_numbers = ", ".join(str(row_number) for row_number, _ in matching_rows)
        raise IssueNotUniqueError(
            f"Issue {target_key!r} matched multiple rows: {row_numbers}."
        )
    selected_row_number, selected_values = matching_rows[0]

    role_results: list[dict[str, Any]] = []
    for binding in resolved_roles:
        column_index = binding["column_index"]
        cell = (
            selected_values[column_index - 1]
            if column_index <= len(selected_values)
            else None
        )
        member_texts = _xlsx_members(cell, binding["separators"])
        if not member_texts:
            raise EmptyRoleError(
                f"Role {binding['role']!r} is empty in worksheet "
                f"{sheet_name!r}, row {selected_row_number}."
            )
        role_results.append(
            {
                "role": binding["role"],
                "column_header": binding["column_header"],
                "column_index": column_index,
                "separators": binding["separators"],
                "members": [
                    {
                        "identity": member_text,
                        "display": member_text,
                    }
                    for member_text in member_texts
                ],
            }
        )

    schema = {
        "source": "xlsx",
        "workbook_sha256": inspection["sha256"],
        "sheet_name": sheet_name,
        "header_row": header_row,
        "issue_column": {
            "selector": issue_column,
            "index": issue_column_index,
            "header": _xlsx_scalar(header_values[issue_column_index - 1]),
        },
        "roles": [
            {
                "role": item["role"],
                "column": item["column"],
                "column_index": item["column_index"],
                "column_header": item["column_header"],
                "separators": item["separators"],
            }
            for item in resolved_roles
        ],
    }
    schema_sha256 = _canonical_hash(schema)
    return {
        "source": "xlsx",
        "path": inspection["path"],
        "issue": target_key,
        "row_number": selected_row_number,
        "roles": role_results,
        "schema": schema,
        "schema_sha256": schema_sha256,
    }


__all__ = [
    "ConfigurationError",
    "DEFAULT_API_ORIGIN",
    "EmptyRoleError",
    "FeishuAPIError",
    "FeishuClient",
    "FeishuCredentials",
    "FeishuResponseError",
    "FeishuTransportError",
    "IssueError",
    "IssueNotFoundError",
    "IssueNotUniqueError",
    "RosterError",
    "SchemaError",
    "TransportResponse",
    "UrllibTransport",
    "WorkbookError",
    "bind_schema",
    "build_roster",
    "build_roster_from_config",
    "build_xlsx_roster_snapshot",
    "credentials_from_env",
    "discover_fields",
    "extract_wiki_node_token",
    "inspect_workbook",
    "normalize_members",
    "normalize_role_bindings",
    "paginate_view_records",
    "resolve_wiki_bitable",
    "select_issue_record",
]
