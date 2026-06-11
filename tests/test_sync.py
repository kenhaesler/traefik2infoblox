from tests.conftest import make_config
from traefik2infoblox.state import State
from traefik2infoblox.sync import sync_once

NOW = 1_000_000.0


def make_state(cfg):
    return State(cfg.state_file)


def test_creates_record_for_new_hostname(tmp_path, fake_infoblox):
    cfg = make_config(tmp_path)
    state = make_state(cfg)

    result = sync_once(cfg, fake_infoblox, state, {"app.example.com"}, NOW)

    assert fake_infoblox.created == ["app.example.com"]
    record = fake_infoblox.records["app.example.com"]
    assert record["canonical"] == "dockerhost01.example.com"
    assert record["comment"] == cfg.record_comment
    assert state.last_seen["app.example.com"] == NOW
    assert result.created == 1
    # State must survive a restart.
    assert State.load(cfg.state_file).last_seen == {"app.example.com": NOW}


def test_existing_correct_record_is_left_alone(tmp_path, fake_infoblox):
    cfg = make_config(tmp_path)
    fake_infoblox.add("app.example.com", cfg.cname_target, cfg.record_comment)
    state = make_state(cfg)

    result = sync_once(cfg, fake_infoblox, state, {"app.example.com"}, NOW)

    assert fake_infoblox.created == []
    assert fake_infoblox.updated == []
    assert result.created == result.updated == 0


def test_wrong_canonical_is_corrected(tmp_path, fake_infoblox):
    cfg = make_config(tmp_path)
    fake_infoblox.add("app.example.com", "oldhost.example.com", cfg.record_comment)
    state = make_state(cfg)

    result = sync_once(cfg, fake_infoblox, state, {"app.example.com"}, NOW)

    assert fake_infoblox.updated == ["app.example.com"]
    assert fake_infoblox.records["app.example.com"]["canonical"] == cfg.cname_target
    assert result.updated == 1


def test_foreign_record_is_never_touched(tmp_path, fake_infoblox):
    cfg = make_config(tmp_path)
    fake_infoblox.add("app.example.com", "somewhere.else.com", "created by hand")
    state = make_state(cfg)
    warned = set()

    result = sync_once(cfg, fake_infoblox, state, {"app.example.com"}, NOW, warned=warned)

    assert fake_infoblox.created == []
    assert fake_infoblox.updated == []
    assert fake_infoblox.records["app.example.com"]["canonical"] == "somewhere.else.com"
    assert result.foreign == 1
    assert warned == {"app.example.com"}


def test_recently_unseen_hostname_is_kept(tmp_path, fake_infoblox):
    cfg = make_config(tmp_path)
    fake_infoblox.add("app.example.com", cfg.cname_target, cfg.record_comment)
    state = make_state(cfg)
    state.mark_seen("app.example.com", NOW - cfg.expire_after + 60)

    result = sync_once(cfg, fake_infoblox, state, set(), NOW)

    assert fake_infoblox.deleted == []
    assert "app.example.com" in state.last_seen
    assert result.deleted == 0


def test_hostname_unseen_for_over_a_week_is_deleted(tmp_path, fake_infoblox):
    cfg = make_config(tmp_path)
    fake_infoblox.add("app.example.com", cfg.cname_target, cfg.record_comment)
    state = make_state(cfg)
    state.mark_seen("app.example.com", NOW - cfg.expire_after - 1)

    result = sync_once(cfg, fake_infoblox, state, set(), NOW)

    assert fake_infoblox.deleted == ["app.example.com"]
    assert "app.example.com" not in state.last_seen
    assert result.deleted == 1


def test_expired_foreign_record_is_not_deleted(tmp_path, fake_infoblox):
    cfg = make_config(tmp_path)
    fake_infoblox.add("app.example.com", "somewhere.else.com", "created by hand")
    state = make_state(cfg)
    state.mark_seen("app.example.com", NOW - cfg.expire_after - 1)

    result = sync_once(cfg, fake_infoblox, state, set(), NOW)

    assert fake_infoblox.deleted == []
    assert "app.example.com" in fake_infoblox.records
    # We stop tracking what we do not own.
    assert "app.example.com" not in state.last_seen
    assert result.deleted == 0


def test_orphaned_managed_record_is_adopted_then_expired(tmp_path, fake_infoblox):
    """A managed record with no state entry (lost state file) gets the full countdown."""
    cfg = make_config(tmp_path)
    fake_infoblox.add("orphan.example.com", cfg.cname_target, cfg.record_comment)
    state = make_state(cfg)

    result = sync_once(cfg, fake_infoblox, state, set(), NOW)
    assert result.adopted == 1
    assert state.last_seen["orphan.example.com"] == NOW
    assert fake_infoblox.deleted == []

    # Still within the grace period: kept.
    result = sync_once(cfg, fake_infoblox, state, set(), NOW + cfg.expire_after - 1)
    assert fake_infoblox.deleted == []

    # Past the grace period: deleted.
    result = sync_once(cfg, fake_infoblox, state, set(), NOW + cfg.expire_after + 1)
    assert fake_infoblox.deleted == ["orphan.example.com"]
    assert result.deleted == 1


def test_reappearing_hostname_resets_expiry(tmp_path, fake_infoblox):
    cfg = make_config(tmp_path)
    fake_infoblox.add("app.example.com", cfg.cname_target, cfg.record_comment)
    state = make_state(cfg)
    state.mark_seen("app.example.com", NOW - cfg.expire_after - 1)

    # The label is back right when it would have expired.
    result = sync_once(cfg, fake_infoblox, state, {"app.example.com"}, NOW)

    assert fake_infoblox.deleted == []
    assert state.last_seen["app.example.com"] == NOW
    assert result.deleted == 0


def test_dry_run_never_mutates_infoblox(tmp_path, fake_infoblox):
    cfg = make_config(tmp_path, DRY_RUN="true")
    fake_infoblox.add("stale.example.com", cfg.cname_target, cfg.record_comment)
    fake_infoblox.add("drift.example.com", "wrong.example.com", cfg.record_comment)
    state = make_state(cfg)
    state.mark_seen("stale.example.com", NOW - cfg.expire_after - 1)
    state.mark_seen("drift.example.com", NOW)

    result = sync_once(
        cfg, fake_infoblox, state, {"new.example.com", "drift.example.com"}, NOW
    )

    assert fake_infoblox.created == []
    assert fake_infoblox.updated == []
    assert fake_infoblox.deleted == []
    # Counters still report what would have happened.
    assert result.created == 1
    assert result.updated == 1
    # The stale entry stays tracked so a later real run can delete it.
    assert "stale.example.com" in state.last_seen


def test_out_of_zone_state_entries_are_dropped(tmp_path, fake_infoblox):
    cfg = make_config(tmp_path)
    state = make_state(cfg)
    state.mark_seen("app.other.org", NOW)

    sync_once(cfg, fake_infoblox, state, set(), NOW)

    assert "app.other.org" not in state.last_seen


def test_ttl_drift_is_corrected(tmp_path, fake_infoblox):
    cfg = make_config(tmp_path, RECORD_TTL="300")
    fake_infoblox.add("app.example.com", cfg.cname_target, cfg.record_comment)
    state = make_state(cfg)

    result = sync_once(cfg, fake_infoblox, state, {"app.example.com"}, NOW)

    assert result.updated == 1
    record = fake_infoblox.records["app.example.com"]
    assert record["ttl"] == 300
    assert record["use_ttl"] is True
