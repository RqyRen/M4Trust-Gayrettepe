"""SERVICE_VERSION semver fail-fast testleri.

contracts/schemas/common/producer-1.0.0.schema.json'daki "version" pattern'i
ile ayni kural Settings kurulurken erken uygulanir (bkz. app/config.py).
"""
from __future__ import annotations

import pytest

from app.config import Settings


def test_valid_semver_is_accepted() -> None:
    Settings(_env_file=None, service_version="1.2.3")  # firlatmamali


def test_valid_semver_with_prerelease_and_build_metadata_is_accepted() -> None:
    Settings(_env_file=None, service_version="1.2.3-rc.1+build.5")  # firlatmamali


@pytest.mark.parametrize("bad_version", ["v1.0.0", "1.0", "1", "0.1.0.0", "", "latest"])
def test_non_semver_is_rejected(bad_version: str) -> None:
    with pytest.raises(Exception):
        Settings(_env_file=None, service_version=bad_version)
