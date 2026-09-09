"""Startup login checks must never invoke real CLI or network activity."""

import subprocess
from unittest.mock import Mock, patch

import pytest

from kiro.sso_login import ensure_sso_login


def test_existing_login_skips_browser() -> None:
    """Successful whoami skips the browser process."""
    with patch("kiro.sso_login.subprocess.run", return_value=Mock(returncode=0)) as run:
        ensure_sso_login()
        assert run.call_count == 1


def test_missing_login_waits_and_rechecks() -> None:
    """Login receives organization settings and must finish before rechecking."""
    with patch(
        "kiro.sso_login.subprocess.run",
        side_effect=[Mock(returncode=1), Mock(returncode=0), Mock(returncode=0)],
    ) as run:
        ensure_sso_login("https://test.awsapps.com/start", "eu-west-1")
        assert run.call_args_list[1].args[0] == [
            "kiro-cli",
            "login",
            "--license",
            "pro",
            "--identity-provider",
            "https://test.awsapps.com/start",
            "--region",
            "eu-west-1",
        ]
        assert run.call_count == 3


@pytest.mark.parametrize(
    "error", [FileNotFoundError(), subprocess.CalledProcessError(1, "kiro-cli")]
)
def test_cli_failure_stops_startup(error: Exception) -> None:
    """Missing CLI and failed login do not allow server startup."""
    with patch("kiro.sso_login.subprocess.run", side_effect=error):
        with pytest.raises(RuntimeError):
            ensure_sso_login()
