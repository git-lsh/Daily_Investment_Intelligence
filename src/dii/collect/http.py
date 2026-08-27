"""외부 HTTP 소스에 예의 있게 접근하기 위한 공용 클라이언트.

rate limit 을 지키는 코드는 기능이 아니라 **태도**다. 남의 무료 서비스를 쓰면서
초당 수백 번 때리면 차단당하는 것이 당연하다.
(`docs/tech-notes/06-data-collection-sec-edgar.md` 3절)
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request
from typing import Any

from dii.logging_setup import get_logger

logger = get_logger(__name__)

#: 재시도할 가치가 있는 HTTP 상태. 429(요청 과다)와 5xx(서버 문제)는 기다리면 풀릴 수 있다.
#: 404 는 재시도해도 소용없다 — 이 구분이 수집기 설계의 핵심이다.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class HttpError(Exception):
    """재시도해도 소용없는 실패."""


class RateLimitedClient:
    """요청 간 최소 간격을 강제하는 JSON 클라이언트.

    스레드를 쓰지 않는 단순 구현이다. 이 프로젝트는 순차 수집만 하므로 충분하고,
    동시 요청을 도입하는 순간 이 클래스로는 부족해진다는 것을 알고 쓴다.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        min_interval: float = 0.15,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        """
        Args:
            user_agent: 신원 표기. SEC 는 연락 가능한 이메일을 요구한다.
            min_interval: 요청 사이 최소 간격(초). SEC 제한은 초당 10회이므로
                0.1 초가 하한이고, 여유를 둬 0.15 를 기본으로 한다.
            timeout: 응답 대기 한계. 없으면 응답 없는 요청 하나가 배치를 붙잡는다.
            max_retries: 재시도 횟수. 간격은 지수적으로 늘린다.
        """
        self._user_agent = user_agent
        self._min_interval = min_interval
        self._timeout = timeout
        self._max_retries = max_retries
        self._last_request_at = 0.0

    def get_json(self, url: str) -> Any:
        """JSON 을 받아 온다.

        Raises:
            HttpError: 재시도해도 소용없거나, 재시도를 다 써도 실패했을 때.
        """
        last_error = ""
        for attempt in range(self._max_retries + 1):
            self._wait_for_slot()
            try:
                return self._fetch(url)
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE_STATUS:
                    raise HttpError(f"{exc.code} {exc.reason} — {url}") from exc
                last_error = f"{exc.code} {exc.reason}"
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt < self._max_retries:
                # 지수 백오프. 모두가 같은 간격으로 재시도하면 부하가 몰린다.
                delay = self._min_interval * (2 ** (attempt + 1))
                logger.warning(
                    "요청 실패(%s), %.1f초 후 재시도 %d/%d — %s",
                    last_error,
                    delay,
                    attempt + 1,
                    self._max_retries,
                    url,
                )
                time.sleep(delay)

        raise HttpError(f"재시도 {self._max_retries}회 실패 ({last_error}) — {url}")

    def _wait_for_slot(self) -> None:
        """직전 요청으로부터 최소 간격이 지날 때까지 기다린다."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def _fetch(self, url: str) -> Any:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self._user_agent,
                # 상대 서버의 대역폭을 아낀다. 압축 해제는 우리 쪽 비용이다.
                "Accept-Encoding": "gzip",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            payload = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                payload = gzip.decompress(payload)
        return json.loads(payload)
