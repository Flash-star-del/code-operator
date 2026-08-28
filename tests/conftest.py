from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def block_unmocked_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every test fail if production code opens a real network socket."""

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("离线测试禁止真实网络连接")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
