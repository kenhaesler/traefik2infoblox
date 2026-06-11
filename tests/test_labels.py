from types import SimpleNamespace

from traefik2infoblox.labels import (
    collect_desired_hostnames,
    hostnames_from_labels,
    hostnames_from_rule,
    in_zone,
)


def test_single_host_backticks():
    assert hostnames_from_rule("Host(`app.example.com`)") == {"app.example.com"}


def test_multiple_hosts_in_one_matcher():
    rule = "Host(`a.example.com`, `b.example.com`)"
    assert hostnames_from_rule(rule) == {"a.example.com", "b.example.com"}


def test_multiple_host_matchers_with_logical_operators():
    rule = "Host(`a.example.com`) || (Host(`b.example.com`) && PathPrefix(`/api`))"
    assert hostnames_from_rule(rule) == {"a.example.com", "b.example.com"}


def test_double_and_single_quotes():
    assert hostnames_from_rule('Host("a.example.com")') == {"a.example.com"}
    assert hostnames_from_rule("Host('a.example.com')") == {"a.example.com"}


def test_hostsni_for_tcp_routers():
    assert hostnames_from_rule("HostSNI(`db.example.com`)") == {"db.example.com"}


def test_hostsni_wildcard_is_skipped():
    assert hostnames_from_rule("HostSNI(`*`)") == set()


def test_wildcard_hostname_is_skipped():
    assert hostnames_from_rule("Host(`*.example.com`)") == set()


def test_hostregexp_is_ignored():
    assert hostnames_from_rule("HostRegexp(`{sub:[a-z]+}.example.com`)") == set()


def test_hostname_is_normalized():
    assert hostnames_from_rule("Host(`App.Example.COM.`)") == {"app.example.com"}


def test_path_prefix_only_rule_yields_nothing():
    assert hostnames_from_rule("PathPrefix(`/api`)") == set()


def test_labels_with_http_and_tcp_routers():
    labels = {
        "traefik.http.routers.web.rule": "Host(`web.example.com`)",
        "traefik.tcp.routers.db.rule": "HostSNI(`db.example.com`)",
        "traefik.http.services.web.loadbalancer.server.port": "8080",
        "com.docker.compose.project": "mystack",
    }
    assert hostnames_from_labels(labels) == {"web.example.com", "db.example.com"}


def test_traefik_enable_false_excludes_container():
    labels = {
        "traefik.enable": "false",
        "traefik.http.routers.web.rule": "Host(`web.example.com`)",
    }
    assert hostnames_from_labels(labels) == set()


def test_require_traefik_enable():
    labels = {"traefik.http.routers.web.rule": "Host(`web.example.com`)"}
    assert hostnames_from_labels(labels, require_traefik_enable=True) == set()
    labels["traefik.enable"] = "true"
    assert hostnames_from_labels(labels, require_traefik_enable=True) == {"web.example.com"}


def test_in_zone():
    assert in_zone("app.example.com", "example.com")
    assert in_zone("a.b.example.com", "example.com")
    assert in_zone("example.com", "example.com")
    assert not in_zone("app.other.com", "example.com")
    assert not in_zone("notexample.com", "example.com")


def test_collect_desired_hostnames_splits_by_zone():
    containers = [
        SimpleNamespace(labels={"traefik.http.routers.a.rule": "Host(`a.example.com`)"}),
        SimpleNamespace(labels={"traefik.http.routers.b.rule": "Host(`b.other.org`)"}),
        SimpleNamespace(labels=None),
    ]
    desired, out_of_zone = collect_desired_hostnames(containers, "example.com")
    assert desired == {"a.example.com"}
    assert out_of_zone == {"b.other.org"}
