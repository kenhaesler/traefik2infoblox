import pytest

from traefik2infoblox.hostinfo import resolve_host_fqdn


def test_daemon_name_already_fqdn_is_used_directly():
    fqdn, source = resolve_host_fqdn("Host01.Example.COM.", "other.org")
    assert fqdn == "host01.example.com"
    assert source == "Docker daemon hostname"


def test_short_name_resolved_via_dns():
    fqdn, source = resolve_host_fqdn(
        "host01", "example.com", dns_lookup=lambda name: "host01.corp.internal"
    )
    assert fqdn == "host01.corp.internal"
    assert "DNS lookup" in source


def test_short_name_falls_back_to_zone_qualification():
    fqdn, source = resolve_host_fqdn("host01", "example.com", dns_lookup=lambda name: name)
    assert fqdn == "host01.example.com"
    assert "qualified with zone" in source


def test_localhost_dns_answer_is_rejected():
    fqdn, _ = resolve_host_fqdn(
        "host01", "example.com", dns_lookup=lambda name: "localhost.localdomain"
    )
    assert fqdn == "host01.example.com"


def test_dns_error_falls_back_to_zone_qualification():
    def boom(name):
        raise OSError("resolver unavailable")

    fqdn, _ = resolve_host_fqdn("host01", "example.com", dns_lookup=boom)
    assert fqdn == "host01.example.com"


def test_empty_daemon_name_raises():
    with pytest.raises(ValueError):
        resolve_host_fqdn("", "example.com")
