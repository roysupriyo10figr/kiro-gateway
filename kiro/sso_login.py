"""Conditional interactive SSO login before standalone server startup."""

import subprocess
from typing import Optional

from loguru import logger


def ensure_sso_login(
    start_url: Optional[str] = None, region: str = "us-east-1"
) -> None:
    """Skip an existing CLI login or wait for organization browser sign-in.

    Args:
        start_url: Organization IAM Identity Center start URL; CLI prompts if absent.
        region: Organization SSO region.

    Raises:
        RuntimeError: If the CLI is unavailable or login fails.
    """
    try:
        check = subprocess.run(
            ["kiro-cli", "whoami"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if check.returncode == 0:
            logger.info("Kiro CLI is already signed in; skipping browser login")
            return
        command = ["kiro-cli", "login", "--license", "pro"]
        if start_url:
            command.extend(["--identity-provider", start_url, "--region", region])
        logger.info("Complete Kiro SSO browser login to start the gateway")
        subprocess.run(command, check=True)
        subprocess.run(
            ["kiro-cli", "whoami"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Install Kiro CLI and make kiro-cli available on PATH before starting the gateway"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Kiro SSO login did not complete; gateway startup stopped. Run kiro-cli login and retry"
        ) from exc
