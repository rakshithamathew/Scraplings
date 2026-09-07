"""Launch and track persistent browser profiles without storing passwords."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_PLATFORMS = ("linkedin", "naukri", "indeed")


@dataclass(frozen=True)
class ConnectionState:
    platform: str
    status: str
    connected: bool
    message: str
    connected_at: str | None = None
    last_verified_at: str | None = None


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _platform_module(platform: str):
    if platform == "linkedin":
        from . import linkedin as module
    elif platform == "naukri":
        from . import naukri as module
    elif platform == "indeed":
        from . import indeed as module
    else:
        raise ValueError(f"Unsupported platform: {platform}")
    return module


class SessionManager:
    """Manage one isolated, persistent Chromium profile per job platform."""

    def __init__(
        self,
        auth_root: str | Path = PROJECT_ROOT / "data" / "auth",
        *,
        project_root: str | Path = PROJECT_ROOT,
        browser_channel: str | None = "chrome",
    ) -> None:
        self.project_root = Path(project_root).resolve()
        raw_root = Path(auth_root)
        self.auth_root = (raw_root if raw_root.is_absolute() else self.project_root / raw_root).resolve()
        self.browser_channel = browser_channel
        self.auth_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def normalize_platform(platform: str) -> str:
        normalized = platform.strip().casefold()
        if normalized not in SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported platform {platform!r}; expected: {', '.join(SUPPORTED_PLATFORMS)}")
        return normalized

    def platform_directory(self, platform: str) -> Path:
        platform = self.normalize_platform(platform)
        path = (self.auth_root / platform).resolve()
        path.relative_to(self.auth_root)
        return path

    def profile_directory(self, platform: str) -> Path:
        return self.platform_directory(platform) / "profile"

    def _metadata_path(self, platform: str) -> Path:
        return self.platform_directory(platform) / "session.json"

    def load_session(self, platform: str) -> dict[str, Any] | None:
        path = self._metadata_path(platform)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def save_session(self, platform: str, **metadata: Any) -> dict[str, Any]:
        platform = self.normalize_platform(platform)
        directory = self.platform_directory(platform)
        directory.mkdir(parents=True, exist_ok=True)
        existing = self.load_session(platform) or {}
        payload = {
            **existing,
            "platform": platform,
            "status": metadata.pop("status", existing.get("status", "connected")),
            "connected_at": metadata.pop("connected_at", existing.get("connected_at")),
            "last_verified_at": metadata.pop("last_verified_at", existing.get("last_verified_at")),
            **metadata,
        }
        temporary = directory / "session.json.tmp"
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self._metadata_path(platform))
        return payload

    def status(self, platform: str) -> ConnectionState:
        platform = self.normalize_platform(platform)
        saved = self.load_session(platform) or {}
        state = str(saved.get("status", "disconnected"))
        connected = state == "connected" and self.profile_directory(platform).is_dir()
        messages = {
            "connected": "Authorization saved and ready for Auto Apply",
            "connecting": "Finish login in the browser, then close that window",
            "disconnected": "Connect to authorize this platform",
            "expired": "Session expired; connect again",
            "failed": str(saved.get("error") or "Connection failed; try again"),
        }
        return ConnectionState(
            platform=platform,
            status=state if state in messages else "disconnected",
            connected=connected,
            message=messages.get(state, messages["disconnected"]),
            connected_at=saved.get("connected_at"),
            last_verified_at=saved.get("last_verified_at"),
        )

    def list_connections(self) -> list[ConnectionState]:
        return [self.status(platform) for platform in SUPPORTED_PLATFORMS]

    def is_connected(self, platform: str) -> bool:
        return self.status(platform).connected

    @staticmethod
    def _process_is_running(process_id: object) -> bool:
        try:
            pid = int(process_id)  # type: ignore[arg-type]
            if pid < 1:
                return False
            os.kill(pid, 0)
            return True
        except (TypeError, ValueError, OSError):
            return False

    def connect(self, platform: str) -> ConnectionState:
        """Start a detached visible login worker and return immediately."""
        platform = self.normalize_platform(platform)
        current = self.load_session(platform) or {}
        if current.get("status") == "connecting" and self._process_is_running(current.get("process_id")):
            return self.status(platform)
        directory = self.platform_directory(platform)
        directory.mkdir(parents=True, exist_ok=True)
        self.profile_directory(platform).mkdir(parents=True, exist_ok=True)
        self.save_session(platform, status="connecting", started_at=_utc_iso(), error=None)
        command = [
            sys.executable,
            "-m",
            "job_automation.auth.session_manager",
            "--worker",
            platform,
            "--auth-root",
            str(self.auth_root),
        ]
        if self.browser_channel:
            command.extend(("--browser-channel", self.browser_channel))
        creationflags = 0
        popen_options: dict[str, Any] = {"cwd": str(self.project_root), "close_fds": True}
        if sys.platform == "win32":
            creationflags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            popen_options["creationflags"] = creationflags
        else:
            popen_options["start_new_session"] = True
        log_path = directory / "connection.log"
        try:
            with log_path.open("ab") as output:
                process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, **popen_options)
        except Exception as error:
            self.save_session(platform, status="failed", error=f"{type(error).__name__}: {error}")
            raise RuntimeError(f"Unable to start {platform} authorization: {error}") from error
        self.save_session(platform, status="connecting", process_id=process.pid, started_at=_utc_iso(), error=None)
        return self.status(platform)

    def mark_disconnected(self, platform: str, reason: str = "Session expired") -> ConnectionState:
        self.save_session(platform, status="expired", error=reason, last_verified_at=_utc_iso())
        return self.status(platform)

    def clear_session(self, platform: str) -> None:
        directory = self.platform_directory(platform)
        if directory.exists():
            shutil.rmtree(directory)

    @staticmethod
    def platform_for_url(url: str | None, source: str | None = None) -> str | None:
        source_key = (source or "").casefold()
        try:
            host = urlsplit(url or "").netloc.casefold()
        except ValueError:
            host = ""
        combined = f"{source_key} {host}"
        if "linkedin" in combined:
            return "linkedin"
        if "naukri" in combined:
            return "naukri"
        if "indeed" in combined:
            return "indeed"
        return None


async def _run_connection_worker(platform: str, auth_root: Path, browser_channel: str | None) -> int:
    manager = SessionManager(auth_root, browser_channel=browser_channel)
    module = _platform_module(platform)
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:
        manager.save_session(platform, status="failed", error="Playwright is not installed")
        raise RuntimeError("Playwright is not installed") from error

    authenticated = False
    playwright = await async_playwright().start()
    launch_options: dict[str, Any] = {
        "user_data_dir": str(manager.profile_directory(platform)),
        "headless": False,
    }
    if browser_channel:
        launch_options["channel"] = browser_channel
    try:
        context = await playwright.chromium.launch_persistent_context(**launch_options)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(module.HOME_URL, wait_until="domcontentloaded", timeout=60_000)
            if not await module.is_authenticated(page):
                await page.goto(module.LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
        except Exception as navigation_error:
            # A proxy, DNS, or temporary portal failure must not tear down the
            # visible authorization window. The user can refresh or enter the
            # URL manually while this worker continues owning the profile.
            LOGGER.warning("Initial %s navigation failed: %s", platform, navigation_error)
            manager.save_session(
                platform,
                status="connecting",
                navigation_warning=f"{type(navigation_error).__name__}: {navigation_error}",
            )
        while context.pages:
            for candidate in context.pages:
                if await module.is_authenticated(candidate):
                    authenticated = True
                    break
            await asyncio.sleep(2)
        if authenticated:
            now = _utc_iso()
            manager.save_session(
                platform,
                status="connected",
                connected_at=(manager.load_session(platform) or {}).get("connected_at") or now,
                last_verified_at=now,
                process_id=None,
                error=None,
            )
            return 0
        manager.save_session(platform, status="disconnected", process_id=None, error="Login was not completed")
        return 1
    except Exception as error:
        if authenticated and "closed" in str(error).casefold():
            now = _utc_iso()
            manager.save_session(platform, status="connected", connected_at=now, last_verified_at=now, process_id=None)
            return 0
        manager.save_session(platform, status="failed", process_id=None, error=f"{type(error).__name__}: {error}")
        LOGGER.exception("Authorization worker failed for %s", platform)
        return 1
    finally:
        await playwright.stop()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=SUPPORTED_PLATFORMS)
    parser.add_argument("--auth-root", type=Path, default=PROJECT_ROOT / "data" / "auth")
    parser.add_argument("--browser-channel", default="chrome")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.worker:
        raise SystemExit("--worker PLATFORM is required")
    return asyncio.run(_run_connection_worker(args.worker, args.auth_root, args.browser_channel or None))


if __name__ == "__main__":
    raise SystemExit(main())
