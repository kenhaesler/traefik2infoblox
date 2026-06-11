import json

import pytest

from traefik2infoblox.infoblox import InfobloxClient, InfobloxError


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        if text is not None:
            self.text = text
        elif payload is not None:
            self.text = json.dumps(payload)
        else:
            self.text = ""
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.auth = None
        self.verify = None

    def request(self, method, url, params=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params, "json": json})
        return self.responses.pop(0)


def make_client(responses):
    session = FakeSession(responses)
    client = InfobloxClient(
        "https://gm.example.com/wapi/v2.10",
        "user",
        "pass",
        view="internal",
        session=session,
    )
    return client, session


def test_session_configured_with_auth():
    client, session = make_client([])
    assert session.auth == ("user", "pass")
    assert session.verify is True


def test_get_cname_returns_first_match():
    payload = [{"_ref": "record:cname/abc", "name": "app.example.com"}]
    client, session = make_client([FakeResponse(payload=payload)])
    record = client.get_cname("app.example.com")
    assert record["_ref"] == "record:cname/abc"
    call = session.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/record:cname")
    assert call["params"]["name"] == "app.example.com"
    assert call["params"]["view"] == "internal"


def test_get_cname_returns_none_when_absent():
    client, _ = make_client([FakeResponse(payload=[])])
    assert client.get_cname("missing.example.com") is None


def test_create_cname_payload_includes_ttl():
    client, session = make_client([FakeResponse(status_code=201, payload="record:cname/new")])
    ref = client.create_cname("app.example.com", "host.example.com", "marker", ttl=300)
    assert ref == "record:cname/new"
    body = session.calls[0]["json"]
    assert body == {
        "name": "app.example.com",
        "canonical": "host.example.com",
        "comment": "marker",
        "view": "internal",
        "ttl": 300,
        "use_ttl": True,
    }


def test_list_managed_cnames_follows_paging():
    page1 = FakeResponse(
        payload={"result": [{"name": "a.example.com"}], "next_page_id": "page-2"}
    )
    page2 = FakeResponse(payload={"result": [{"name": "b.example.com"}]})
    client, session = make_client([page1, page2])

    records = client.list_managed_cnames("example.com", "marker")

    assert [r["name"] for r in records] == ["a.example.com", "b.example.com"]
    assert "_page_id" not in session.calls[0]["params"]
    assert session.calls[1]["params"]["_page_id"] == "page-2"
    assert session.calls[0]["params"]["comment"] == "marker"
    assert session.calls[0]["params"]["zone"] == "example.com"


def test_delete_targets_ref_url():
    client, session = make_client([FakeResponse(payload="record:cname/abc")])
    client.delete("record:cname/abc")
    assert session.calls[0]["method"] == "DELETE"
    assert session.calls[0]["url"] == "https://gm.example.com/wapi/v2.10/record:cname/abc"


def test_update_sends_put_with_fields():
    client, session = make_client([FakeResponse(payload="record:cname/abc")])
    client.update("record:cname/abc", {"canonical": "new.example.com"})
    assert session.calls[0]["method"] == "PUT"
    assert session.calls[0]["json"] == {"canonical": "new.example.com"}


def test_http_error_raises_with_wapi_message():
    error_body = {"Error": "AdmConProtoError: bad things", "text": "bad things happened"}
    client, _ = make_client([FakeResponse(status_code=400, payload=error_body)])
    with pytest.raises(InfobloxError) as excinfo:
        client.get_cname("app.example.com")
    assert "bad things happened" in str(excinfo.value)
    assert excinfo.value.status_code == 400


def test_zone_exists():
    client, _ = make_client([FakeResponse(payload=[{"fqdn": "example.com"}])])
    assert client.zone_exists("example.com") is True
    client, _ = make_client([FakeResponse(payload=[])])
    assert client.zone_exists("example.com") is False
