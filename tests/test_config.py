import pytest

from tests.conftest import make_config
from traefik2infoblox.config import ConfigError, build_wapi_url, parse_duration


def test_defaults(tmp_path):
    cfg = make_config(tmp_path)
    assert cfg.wapi_url == "https://gm.example.com/wapi/v2.10"
    assert cfg.zone == "example.com"
    assert cfg.cname_target == "dockerhost01.example.com"
    assert cfg.view == "default"
    assert cfg.ssl_verify is True
    assert cfg.sync_interval == 60
    assert cfg.expire_after == 7 * 86400
    assert cfg.record_comment == "Managed by traefik2infoblox for dockerhost01.example.com"
    assert cfg.record_ttl is None
    assert cfg.dry_run is False
    assert cfg.require_traefik_enable is False


def test_missing_required_variables_listed():
    from traefik2infoblox.config import Config

    with pytest.raises(ConfigError) as excinfo:
        Config.from_env({})
    message = str(excinfo.value)
    for name in (
        "INFOBLOX_URL",
        "INFOBLOX_USERNAME",
        "INFOBLOX_PASSWORD",
        "INFOBLOX_ZONE",
    ):
        assert name in message
    # CNAME_TARGET is optional: it is auto-detected from the Docker host.
    assert "CNAME_TARGET" not in message


def test_wapi_url_variants():
    assert build_wapi_url("https://gm.example.com", "v2.10") == "https://gm.example.com/wapi/v2.10"
    assert build_wapi_url("https://gm.example.com/", "v2.10") == "https://gm.example.com/wapi/v2.10"
    assert build_wapi_url("https://gm.example.com/wapi", "v2.10") == "https://gm.example.com/wapi/v2.10"
    assert (
        build_wapi_url("https://gm.example.com/wapi/v2.12", "v2.10")
        == "https://gm.example.com/wapi/v2.12"
    )
    assert build_wapi_url("https://gm.example.com", "2.11") == "https://gm.example.com/wapi/v2.11"
    with pytest.raises(ConfigError):
        build_wapi_url("gm.example.com", "v2.10")


def test_ssl_verify_parsing(tmp_path):
    assert make_config(tmp_path, INFOBLOX_SSL_VERIFY="false").ssl_verify is False
    assert make_config(tmp_path, INFOBLOX_SSL_VERIFY="true").ssl_verify is True
    assert (
        make_config(tmp_path, INFOBLOX_SSL_VERIFY="/etc/ssl/corp-ca.pem").ssl_verify
        == "/etc/ssl/corp-ca.pem"
    )


def test_durations(tmp_path):
    assert parse_duration("300", "X") == 300
    assert parse_duration("90s", "X") == 90
    assert parse_duration("5m", "X") == 300
    assert parse_duration("12h", "X") == 43200
    assert parse_duration("2d", "X") == 172800
    assert parse_duration("1w", "X") == 604800
    with pytest.raises(ConfigError):
        parse_duration("soon", "X")
    with pytest.raises(ConfigError):
        parse_duration("-5m", "X")
    cfg = make_config(tmp_path, SYNC_INTERVAL="5m", EXPIRE_AFTER="3d")
    assert cfg.sync_interval == 300
    assert cfg.expire_after == 3 * 86400


def test_custom_record_comment(tmp_path):
    cfg = make_config(tmp_path, RECORD_COMMENT="my marker")
    assert cfg.record_comment == "my marker"


def test_cname_target_is_optional(tmp_path):
    cfg = make_config(tmp_path, CNAME_TARGET="")
    assert cfg.cname_target is None
    # The default comment depends on the detected target, so it stays empty
    # until startup fills it in.
    assert cfg.record_comment == ""


def test_cname_target_optional_keeps_custom_comment(tmp_path):
    cfg = make_config(tmp_path, CNAME_TARGET="", RECORD_COMMENT="my marker")
    assert cfg.record_comment == "my marker"


def test_record_ttl(tmp_path):
    assert make_config(tmp_path, RECORD_TTL="300").record_ttl == 300
    with pytest.raises(ConfigError):
        make_config(tmp_path, RECORD_TTL="fast")


def test_zone_and_target_normalization(tmp_path):
    cfg = make_config(tmp_path, INFOBLOX_ZONE="Example.COM.", CNAME_TARGET="Host01.Example.com.")
    assert cfg.zone == "example.com"
    assert cfg.cname_target == "host01.example.com"


def test_credentials_from_file(tmp_path):
    secret = tmp_path / "password"
    secret.write_text("from-file\n")
    cfg = make_config(tmp_path, INFOBLOX_PASSWORD="", INFOBLOX_PASSWORD_FILE=str(secret))
    assert cfg.password == "from-file"


def test_credentials_env_and_file_conflict(tmp_path):
    secret = tmp_path / "password"
    secret.write_text("x")
    with pytest.raises(ConfigError):
        make_config(tmp_path, INFOBLOX_PASSWORD_FILE=str(secret))


def test_invalid_log_level(tmp_path):
    with pytest.raises(ConfigError):
        make_config(tmp_path, LOG_LEVEL="LOUD")
