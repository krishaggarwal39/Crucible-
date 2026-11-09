"""
Tests for the SSRF protection in the target-agent connector.

ADR-003 claimed "an integration test verifies the backend still works on
upgrades". No such test existed, which is how the protection came to be
disabled-by-default and unexercised. These tests cover both the address policy
and the fact that SSRFSafeBackend still matches httpcore's connect_tcp contract
(the thing most likely to break on an httpcore upgrade).
"""

import ipaddress
import socket
from inspect import signature

import httpcore
import pytest

from backend.connectors.target_agent import (
    SSRFSafeBackend,
    SSRFViolationError,
    TargetAgentConnector,
    _is_forbidden_ip,
    _resolve_safe_ip,
)


# ── Address policy ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "addr",
    [
        "127.0.0.1",              # loopback
        "10.0.0.5",               # RFC1918
        "172.16.4.4",             # RFC1918
        "192.168.1.1",            # RFC1918
        "169.254.169.254",        # cloud metadata endpoint
        "0.0.0.0",                # unspecified
        "224.0.0.1",              # multicast
        "240.0.0.1",              # reserved
        "::1",                    # IPv6 loopback
        "fe80::1",                # IPv6 link-local
        "fc00::1",                # IPv6 unique-local
        "::ffff:127.0.0.1",       # IPv4-mapped loopback (classic bypass)
        "::ffff:169.254.169.254", # IPv4-mapped metadata (classic bypass)
    ],
)
def test_forbidden_addresses_are_rejected(addr):
    assert _is_forbidden_ip(ipaddress.ip_address(addr)) is True


@pytest.mark.parametrize("addr", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700::1111"])
def test_public_addresses_are_allowed(addr):
    assert _is_forbidden_ip(ipaddress.ip_address(addr)) is False


# ── Resolution ───────────────────────────────────────────────────────────────

def _fake_addrinfo(*addresses):
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 443)) for addr in addresses
    ]


def test_resolve_rejects_private_result(mocker):
    mocker.patch("socket.getaddrinfo", return_value=_fake_addrinfo("10.1.2.3"))
    with pytest.raises(SSRFViolationError, match="restricted address"):
        _resolve_safe_ip("evil.example.com", 443)


def test_resolve_rejects_when_any_record_is_private(mocker):
    """
    A hostname returning a public record first and a private one second must
    still be rejected — validating only the first answer would let it through.
    """
    mocker.patch(
        "socket.getaddrinfo",
        return_value=_fake_addrinfo("93.184.216.34", "169.254.169.254"),
    )
    with pytest.raises(SSRFViolationError, match="restricted address"):
        _resolve_safe_ip("mixed.example.com", 443)


def test_resolve_returns_validated_public_ip(mocker):
    mocker.patch("socket.getaddrinfo", return_value=_fake_addrinfo("93.184.216.34"))
    assert _resolve_safe_ip("example.com", 443) == "93.184.216.34"


def test_resolve_failure_becomes_ssrf_violation(mocker):
    mocker.patch("socket.getaddrinfo", side_effect=socket.gaierror("nope"))
    with pytest.raises(SSRFViolationError, match="Could not resolve"):
        _resolve_safe_ip("nx.example.com", 443)


# ── httpcore contract ────────────────────────────────────────────────────────

def test_backend_is_an_httpcore_backend():
    assert issubclass(SSRFSafeBackend, httpcore.AnyIOBackend)


def test_connect_tcp_signature_matches_httpcore():
    """
    Guards against an httpcore upgrade changing connect_tcp's keyword arguments.
    httpcore calls it with host, port, local_address, timeout and socket_options
    as keywords; if our override stops accepting any of those, every outbound
    request in production breaks.
    """
    ours = signature(SSRFSafeBackend.connect_tcp).parameters
    theirs = signature(httpcore.AnyIOBackend.connect_tcp).parameters
    for name in theirs:
        assert name in ours, f"SSRFSafeBackend.connect_tcp is missing '{name}'"


@pytest.mark.asyncio
async def test_backend_connects_to_validated_ip_not_hostname(mocker):
    """The connection must be made to the checked IP, closing the DNS-rebinding window."""
    mocker.patch("socket.getaddrinfo", return_value=_fake_addrinfo("93.184.216.34"))
    parent = mocker.patch.object(
        httpcore.AnyIOBackend, "connect_tcp", new_callable=mocker.AsyncMock
    )

    backend = SSRFSafeBackend()
    await backend.connect_tcp("example.com", 443, timeout=5.0)

    assert parent.await_count == 1
    assert parent.await_args.args[0] == "93.184.216.34"


# ── Connector-level policy ───────────────────────────────────────────────────

def test_connector_rejects_http_when_protection_active(mocker):
    mocker.patch("backend.connectors.target_agent.settings.SSRF_PROTECTION_ENABLED", True)
    with pytest.raises(SSRFViolationError, match="Only HTTPS"):
        TargetAgentConnector("http://insecure.example.com/chat")


def test_connector_rejects_non_http_schemes(mocker):
    mocker.patch("backend.connectors.target_agent.settings.SSRF_PROTECTION_ENABLED", False)
    with pytest.raises(SSRFViolationError, match="Unsupported URL scheme"):
        TargetAgentConnector("file:///etc/passwd")


def test_connector_rejects_missing_hostname(mocker):
    mocker.patch("backend.connectors.target_agent.settings.SSRF_PROTECTION_ENABLED", False)
    with pytest.raises(SSRFViolationError, match="no hostname"):
        TargetAgentConnector("http:///chat")


@pytest.mark.asyncio
async def test_connector_allows_localhost_when_protection_disabled(mocker):
    """Local development against a mock agent on localhost must keep working."""
    mocker.patch("backend.connectors.target_agent.settings.SSRF_PROTECTION_ENABLED", False)
    connector = TargetAgentConnector("http://127.0.0.1:8001/chat")
    assert connector.ssrf_enforced is False
    await connector.close()


@pytest.mark.asyncio
async def test_connector_installs_safe_backend_when_protection_active(mocker):
    mocker.patch("backend.connectors.target_agent.settings.SSRF_PROTECTION_ENABLED", True)
    connector = TargetAgentConnector("https://api.example.com/chat")
    assert connector.ssrf_enforced is True
    backend = connector.client._transport._pool._network_backend
    assert isinstance(backend, SSRFSafeBackend)
    await connector.close()
