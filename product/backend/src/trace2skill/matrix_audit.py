from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .security import sanitize


def load_agentteams_credentials(path: Path) -> tuple[str, str]:
    values: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    try:
        return values["HICLAW_ADMIN_USER"], values["HICLAW_ADMIN_PASSWORD"]
    except KeyError as exc:
        raise RuntimeError("AgentTeams admin credentials are incomplete") from exc


class MatrixAuditClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        login = self._request(
            "POST",
            "/_matrix/client/v3/login",
            {
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": username},
                "password": password,
            },
            token=None,
        )
        self._token = str(login["access_token"])
        self._self_user_id = str(login["user_id"])

    def manager_room(self) -> str:
        joined = self._request("GET", "/_matrix/client/v3/joined_rooms", token=self._token)
        candidates: list[str] = []
        for room_id in joined.get("joined_rooms", []):
            members = self.joined_members(room_id)
            if (
                len(members) == 2
                and self._self_user_id in members
                and any(member.startswith("@manager:") for member in members)
            ):
                candidates.append(room_id)
        if len(candidates) != 1:
            raise RuntimeError(f"expected one human/Manager DM room, found {len(candidates)}")
        return candidates[0]

    def joined_members(self, room_id: str) -> set[str]:
        value = self._request(
            "GET",
            f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/joined_members",
            token=self._token,
        )
        return set((value.get("joined") or {}).keys())

    def send(self, room_id: str, body: str) -> str:
        import uuid

        transaction = f"trace2skill-v03-{uuid.uuid4().hex}"
        result = self._request(
            "PUT",
            f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/send/m.room.message/{transaction}",
            {"msgtype": "m.text", "body": body},
            token=self._token,
        )
        return str(result["event_id"])

    def request_worker(self, name: str, runtime: str, model: str, identity: str) -> str:
        room = self.manager_room()
        prompt = (
            f"Create a fresh Worker named {name} with runtime {runtime} and model {model}. "
            "Do not reuse or rename an existing Worker. Add the human administrator and Manager to its "
            f"Matrix collaboration room. Use this fixed role identity:\n\n{identity}"
        )
        return self.send(room, prompt)

    def verify_human_visible_worker_room(self, room_id: str, worker_name: str) -> bool:
        members = self.joined_members(room_id)
        return (
            self._self_user_id in members
            and any(member.startswith("@manager:") for member in members)
            and any(member.startswith(f"@{worker_name}:") for member in members)
        )

    def task_events(self, task_id: str, page_limit: int = 5) -> list[dict[str, Any]]:
        joined = self._request("GET", "/_matrix/client/v3/joined_rooms", token=self._token)
        matches: dict[str, dict[str, Any]] = {}
        for room_id in joined.get("joined_rooms", []):
            cursor: str | None = None
            for _ in range(page_limit):
                query = {"dir": "b", "limit": "100"}
                if cursor:
                    query["from"] = cursor
                page = self._request(
                    "GET",
                    f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/messages?{urlencode(query)}",
                    token=self._token,
                )
                chunk = page.get("chunk") or []
                for event in chunk:
                    body = str(event.get("content", {}).get("body", ""))
                    if event.get("type") != "m.room.message" or task_id not in body:
                        continue
                    event_id = str(event.get("event_id", ""))
                    sender = str(event.get("sender", ""))
                    role = (
                        "human"
                        if sender == self._self_user_id
                        else "manager"
                        if sender.startswith("@manager:")
                        else "worker"
                    )
                    matches[event_id] = sanitize(
                        {
                            "source_event_id": event_id,
                            "timestamp_ms": int(event.get("origin_server_ts", 0)),
                            "sender_role": role,
                            "body": body,
                        }
                    )
                next_cursor = page.get("end")
                if not chunk or not next_cursor or next_cursor == cursor:
                    break
                cursor = str(next_cursor)
        return sorted(matches.values(), key=lambda item: (item["timestamp_ms"], item["source_event_id"]))

    def wait_for_worker_result(
        self,
        task_id: str,
        marker: str = "TRACE2SKILL_RESULT",
        timeout_seconds: int = 180,
        poll_seconds: float = 2.0,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            events = self.task_events(task_id)
            candidates = [
                event
                for event in events
                if event.get("sender_role") == "worker"
                and str(event.get("body", "")).count(marker) == 1
            ]
            if candidates:
                body = str(candidates[-1]["body"])
                prefix, payload = body.split(marker, 1)
                # Fresh workers sometimes add a natural-language explanation before obeying the
                # structured marker contract. Keep that explanation in the Matrix Trace, while
                # accepting exactly one marker and exactly one JSON result.
                payload = payload.lstrip(" :\t\r\n")
                if payload.startswith("```") and payload.endswith("```"):
                    lines = payload.splitlines()
                    payload = "\n".join(lines[1:-1]).strip()
                try:
                    value, end = json.JSONDecoder().raw_decode(payload)
                except json.JSONDecodeError:
                    raise
                trailing = payload[end:].strip()
                # Some chat runtimes append a short natural-language acknowledgement after the
                # requested object. Tolerate that transport noise, but never accept a second JSON
                # value or a repeated result marker.
                if trailing and (
                    trailing.startswith(("{", "[")) or marker in trailing
                ):
                    raise ValueError("Worker result contains more than one structured payload")
                if not isinstance(value, dict):
                    raise ValueError("Trace2Skill Worker result must be one JSON object")
                return sanitize(value), events
            time.sleep(poll_seconds)
        raise TimeoutError(f"Matrix Worker result timed out: {task_id}")

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        with urlopen(request, timeout=20) as response:
            value = json.load(response)
        if not isinstance(value, dict):
            raise RuntimeError("Matrix returned a non-object response")
        return value


def normalize_task_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for event in events:
        event_id = str(event.get("source_event_id", ""))
        if not event_id:
            raise ValueError("Matrix event is missing source_event_id")
        unique[event_id] = sanitize(event)
    return sorted(unique.values(), key=lambda item: (int(item["timestamp_ms"]), item["source_event_id"]))
