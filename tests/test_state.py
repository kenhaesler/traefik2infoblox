from traefik2infoblox.state import State


def test_roundtrip(tmp_path):
    path = str(tmp_path / "nested" / "state.json")
    state = State(path)
    state.mark_seen("a.example.com", 1234.5)
    state.mark_seen("b.example.com", 99.0)
    state.remove("b.example.com")
    state.save()

    loaded = State.load(path)
    assert loaded.last_seen == {"a.example.com": 1234.5}


def test_load_missing_file_starts_fresh(tmp_path):
    state = State.load(str(tmp_path / "missing.json"))
    assert state.last_seen == {}


def test_load_corrupt_file_starts_fresh(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    state = State.load(str(path))
    assert state.last_seen == {}


def test_save_is_atomic_replacement(tmp_path):
    path = str(tmp_path / "state.json")
    first = State(path)
    first.mark_seen("a.example.com", 1.0)
    first.save()
    second = State(path)
    second.mark_seen("b.example.com", 2.0)
    second.save()
    assert State.load(path).last_seen == {"b.example.com": 2.0}
    leftovers = [p for p in tmp_path.iterdir() if p.name != "state.json"]
    assert leftovers == []
