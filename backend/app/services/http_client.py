from __future__ import annotations

import threading

import requests
from requests.adapters import HTTPAdapter


HTTP_POOL_CONNECTIONS = 20
HTTP_POOL_MAXSIZE = 20

_thread_local = threading.local()


def get_http_session() -> requests.Session:
    """Return one persistent, connection-pooled HTTP session per worker thread."""
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=HTTP_POOL_CONNECTIONS,
            pool_maxsize=HTTP_POOL_MAXSIZE,
            max_retries=0,
            pool_block=True,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return session
