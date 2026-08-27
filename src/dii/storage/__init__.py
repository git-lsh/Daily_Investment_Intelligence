"""저장 계층.

바깥 코드는 리포지토리 인터페이스만 보고, SQL 이나 커넥션을 직접 다루지 않는다.
M3 에서 PostgreSQL 로 교체할 때 이 경계 안쪽만 바뀌게 하기 위함이다.
(`docs/architecture.md` 계층 경계 원칙 참고)
"""

from dii.storage.models import DailyBar, SecurityKind
from dii.storage.sqlite import SqliteStorage, connect

__all__ = ["DailyBar", "SecurityKind", "SqliteStorage", "connect"]
