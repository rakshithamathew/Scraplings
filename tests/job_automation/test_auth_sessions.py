from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from job_automation.auth import SessionManager


def test_save_load_and_clear_session(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "auth", project_root=tmp_path)
    manager.profile_directory("linkedin").mkdir(parents=True)

    saved = manager.save_session(
        "linkedin",
        status="connected",
        connected_at="2026-09-07T00:00:00+00:00",
    )

    assert saved["platform"] == "linkedin"
    assert manager.load_session("linkedin") == saved
    assert manager.is_connected("linkedin") is True

    manager.clear_session("linkedin")

    assert manager.load_session("linkedin") is None
    assert manager.is_connected("linkedin") is False


def test_session_updates_preserve_worker_metadata(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "auth", project_root=tmp_path)
    manager.save_session("linkedin", status="connecting", process_id=1234)

    manager.save_session("linkedin", status="connecting", navigation_warning="temporary failure")

    saved = manager.load_session("linkedin")
    assert saved is not None
    assert saved["process_id"] == 1234
    assert saved["navigation_warning"] == "temporary failure"


def test_connect_starts_detached_worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_popen(command: list[str], **_: object) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr("job_automation.auth.session_manager.subprocess.Popen", fake_popen)
    manager = SessionManager(tmp_path / "auth", project_root=tmp_path)

    state = manager.connect("naukri")

    assert state.status == "connecting"
    assert state.connected is False
    assert calls and calls[0][0]
    assert calls[0][calls[0].index("--worker") + 1] == "naukri"
    assert manager.load_session("naukri")["process_id"] == 4321  # type: ignore[index]


def test_connect_restarts_stale_connecting_worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = 0

    def fake_popen(command: list[str], **_: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(pid=9876)

    monkeypatch.setattr("job_automation.auth.session_manager.subprocess.Popen", fake_popen)
    monkeypatch.setattr(SessionManager, "_process_is_running", staticmethod(lambda _: False))
    manager = SessionManager(tmp_path / "auth", project_root=tmp_path)
    manager.save_session("indeed", status="connecting", process_id=1234)

    state = manager.connect("indeed")

    assert state.status == "connecting"
    assert calls == 1
    assert manager.load_session("indeed")["process_id"] == 9876  # type: ignore[index]


@pytest.mark.parametrize(
    ("url", "source", "expected"),
    [
        ("https://www.linkedin.com/jobs/view/1", None, "linkedin"),
        ("https://www.naukri.com/job-listings-x", None, "naukri"),
        ("https://in.indeed.com/viewjob?jk=1", None, "indeed"),
        ("https://jobs.lever.co/acme/1", "linkedin", None),
    ],
)
def test_platform_detection(url: str, source: str | None, expected: str | None) -> None:
    # ApplicationService intentionally calls URL-only detection first so a
    # direct public ATS link is not made dependent on the discovery source.
    detected = SessionManager.platform_for_url(url)
    if detected is None and "lever.co" not in url:
        detected = SessionManager.platform_for_url(None, source)
    assert detected == expected


def test_rejects_unknown_platform(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "auth", project_root=tmp_path)
    with pytest.raises(ValueError, match="Unsupported platform"):
        manager.status("unknown")
