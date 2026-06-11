"""Minimal Infoblox WAPI client for CNAME record management."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import requests
import urllib3

RETURN_FIELDS = "name,canonical,comment,ttl,use_ttl"
PAGE_SIZE = 1000


class InfobloxError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class InfobloxClient:
    def __init__(
        self,
        wapi_url: str,
        username: str,
        password: str,
        view: str = "default",
        ssl_verify: Union[bool, str] = True,
        timeout: int = 30,
        session: Optional[requests.Session] = None,
    ):
        self.base_url = wapi_url.rstrip("/")
        self.view = view
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.auth = (username, password)
        self.session.verify = ssl_verify
        if ssl_verify is False:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}/{path}"
        try:
            response = self.session.request(
                method, url, params=params, json=json_body, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise InfobloxError(f"{method} {path}: {exc}") from exc

        if response.status_code >= 400:
            detail = response.text[:500]
            try:
                body = response.json()
                if isinstance(body, dict):
                    detail = body.get("text") or body.get("Error") or detail
            except ValueError:
                pass
            raise InfobloxError(
                f"{method} {path} returned HTTP {response.status_code}: {detail}",
                status_code=response.status_code,
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise InfobloxError(f"{method} {path} returned invalid JSON: {exc}") from exc

    def zone_exists(self, zone: str) -> bool:
        result = self._request("GET", "zone_auth", params={"fqdn": zone, "view": self.view})
        return bool(result)

    def get_cname(self, name: str) -> Optional[Dict[str, Any]]:
        result = self._request(
            "GET",
            "record:cname",
            params={"name": name, "view": self.view, "_return_fields": RETURN_FIELDS},
        )
        if not result:
            return None
        return result[0]

    def list_managed_cnames(self, zone: str, comment: str) -> List[Dict[str, Any]]:
        """List every CNAME in the zone carrying our ownership comment."""
        params: Dict[str, Any] = {
            "zone": zone,
            "view": self.view,
            "comment": comment,
            "_return_fields": RETURN_FIELDS,
            "_paging": 1,
            "_max_results": PAGE_SIZE,
            "_return_as_object": 1,
        }
        records: List[Dict[str, Any]] = []
        page_id: Optional[str] = None
        while True:
            page_params = dict(params)
            if page_id:
                page_params["_page_id"] = page_id
            data = self._request("GET", "record:cname", params=page_params) or {}
            records.extend(data.get("result", []))
            page_id = data.get("next_page_id")
            if not page_id:
                break
        return records

    def create_cname(
        self, name: str, canonical: str, comment: str, ttl: Optional[int] = None
    ) -> str:
        payload: Dict[str, Any] = {
            "name": name,
            "canonical": canonical,
            "comment": comment,
            "view": self.view,
        }
        if ttl is not None:
            payload["ttl"] = ttl
            payload["use_ttl"] = True
        return self._request("POST", "record:cname", json_body=payload)

    def update(self, ref: str, fields: Dict[str, Any]) -> str:
        return self._request("PUT", ref, json_body=fields)

    def delete(self, ref: str) -> str:
        return self._request("DELETE", ref)
