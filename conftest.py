"""Repository-wide pytest configuration.

The OpenHands gate tests use ``tmp_path`` to materialise synthetic batch records.  On
this machine ``%TEMP%`` already contains a ``pytest-of-<user>`` directory owned by a
different Windows account, and pytest's temp-path rotation fails on it with
``PermissionError [WinError 5]`` before any test body runs.  Pinning the base temp
directory inside the repository keeps every gate runnable without a machine-level ACL
change; the directory is a normal build artifact and is removed by pytest itself.
"""
from __future__ import annotations

from pathlib import Path


def pytest_configure(config) -> None:
    if config.option.basetemp:
        return
    base = Path(__file__).resolve().parent / '.tooling' / 'tmp' / 'pytest-basetemp'
    base.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(base)
