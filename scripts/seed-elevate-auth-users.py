#!/usr/bin/env python3
"""Create or update the two local Elevate Supabase auth users."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from typing import Any

TEST_USER_EMAILS = ("test-user-1@elevate.local", "test-user-2@elevate.local")


def supabase_settings() -> dict[str, str]:
    output = subprocess.check_output(
        ["supabase", "status", "-o", "env"],
        text=True,
        stderr=subprocess.DEVNULL,
    )
    settings: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            settings[key] = value.strip().strip('"')
    return settings


def request_json(
    url: str,
    *,
    headers: dict[str, str],
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def main() -> int:
    password = os.environ.get("ELEVATE_TEST_USER_PASSWORD")
    if not password:
        print("ELEVATE_TEST_USER_PASSWORD is required", file=sys.stderr)
        return 2

    settings = supabase_settings()
    api_url = settings["API_URL"]
    service_key = settings["SERVICE_ROLE_KEY"]
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }

    users = request_json(
        f"{api_url}/auth/v1/admin/users?page=1&per_page=1000",
        headers=headers,
    ).get("users", [])
    users_by_email = {user.get("email"): user for user in users}

    for email in TEST_USER_EMAILS:
        existing = users_by_email.get(email)
        if existing:
            user = request_json(
                f"{api_url}/auth/v1/admin/users/{existing['id']}",
                method="PUT",
                headers=headers,
                payload={"password": password, "email_confirm": True},
            )
        else:
            user = request_json(
                f"{api_url}/auth/v1/admin/users",
                method="POST",
                headers=headers,
                payload={"email": email, "password": password, "email_confirm": True},
            )
        print(f"Local auth user ready: {email} ({user['id']})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
