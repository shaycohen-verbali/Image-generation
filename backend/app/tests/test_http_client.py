from __future__ import annotations

import threading

from app.services.http_client import get_http_session


def test_http_session_is_reused_within_a_worker_thread() -> None:
    assert get_http_session() is get_http_session()


def test_http_session_is_isolated_between_worker_threads() -> None:
    main_session = get_http_session()
    thread_sessions: list[object] = []

    thread = threading.Thread(target=lambda: thread_sessions.append(get_http_session()))
    thread.start()
    thread.join()

    assert len(thread_sessions) == 1
    assert thread_sessions[0] is not main_session
