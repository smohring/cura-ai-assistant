"""Calls the standalone service (its own virtualenv) with one JSON request per process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
TIMEOUT_SECONDS = 900


class ServiceError(Exception):
    pass


class ServiceCancelled(ServiceError):
    """The user stopped the request."""


_current: subprocess.Popen | None = None
_cancelled = False
_lock = threading.Lock()


def cancel() -> None:
    """Stop the running request: the service process and whatever it started (claude, engine)."""
    global _cancelled
    with _lock:
        proc = _current
        _cancelled = True
    if proc is not None and proc.poll() is None:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            proc.kill()


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError) as exc:
        raise ServiceError("config.json missing; run scripts/install_plugin.ps1") from exc
    for key in ("python", "service_dir", "work_dir"):
        if not config.get(key):
            raise ServiceError(f"config.json has no '{key}'; run scripts/install_plugin.ps1")
    return config


def call(request: dict) -> dict:
    """Send one request; returns the service's answer (which may be {"error": ...})."""
    config = load_config()
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    env = dict(os.environ)
    if config.get("claude"):  # optional stand-in for the Claude CLI, used for UI tests
        env["CURA_AI_CLAUDE"] = json.dumps(config["claude"])
    global _current, _cancelled
    try:
        with _lock:
            _cancelled = False
            _current = subprocess.Popen(
                [config["python"], "-m", "cura_ai", "request"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=config["service_dir"],
                creationflags=flags,
            )
            proc = _current
        try:
            stdout, stderr = proc.communicate(
                json.dumps(request).encode("utf-8"), timeout=TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            raise ServiceError("the service did not answer in time") from exc
    except OSError as exc:
        raise ServiceError(f"could not start the service: {exc}") from exc
    finally:
        with _lock:
            _current = None
    if _cancelled:
        raise ServiceCancelled("cancelled")
    try:
        return json.loads(stdout.decode("utf-8-sig"))
    except ValueError as exc:
        tail = stderr.decode("utf-8", errors="replace").strip().splitlines()[-3:]
        raise ServiceError("service failed: " + " | ".join(tail)) from exc
