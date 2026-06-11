import copy

import pytest

from traefik2infoblox.config import Config


def make_config(tmp_path, **overrides) -> Config:
    env = {
        "INFOBLOX_URL": "https://gm.example.com",
        "INFOBLOX_USERNAME": "api-user",
        "INFOBLOX_PASSWORD": "secret",
        "INFOBLOX_ZONE": "example.com",
        "CNAME_TARGET": "dockerhost01.example.com",
        "STATE_FILE": str(tmp_path / "state.json"),
        "HEARTBEAT_FILE": str(tmp_path / "heartbeat"),
    }
    env.update(overrides)
    return Config.from_env(env)


class FakeInfoblox:
    """In-memory stand-in for InfobloxClient used by the sync tests."""

    def __init__(self):
        self.records = {}
        self.created = []
        self.updated = []
        self.deleted = []

    def add(self, name, canonical, comment, ttl=None, use_ttl=False):
        self.records[name] = {
            "_ref": f"record:cname/{name}",
            "name": name,
            "canonical": canonical,
            "comment": comment,
            "ttl": ttl,
            "use_ttl": use_ttl,
        }

    def get_cname(self, name):
        record = self.records.get(name)
        return copy.deepcopy(record) if record else None

    def list_managed_cnames(self, zone, comment):
        return [
            copy.deepcopy(record)
            for record in self.records.values()
            if record["comment"] == comment
            and (record["name"] == zone or record["name"].endswith(f".{zone}"))
        ]

    def create_cname(self, name, canonical, comment, ttl=None):
        self.add(name, canonical, comment, ttl=ttl, use_ttl=ttl is not None)
        self.created.append(name)
        return self.records[name]["_ref"]

    def update(self, ref, fields):
        for record in self.records.values():
            if record["_ref"] == ref:
                record.update(fields)
                self.updated.append(record["name"])
                return ref
        raise AssertionError(f"update called with unknown ref {ref}")

    def delete(self, ref):
        for name, record in list(self.records.items()):
            if record["_ref"] == ref:
                del self.records[name]
                self.deleted.append(name)
                return ref
        raise AssertionError(f"delete called with unknown ref {ref}")


@pytest.fixture
def fake_infoblox():
    return FakeInfoblox()
