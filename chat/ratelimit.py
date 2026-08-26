"""사용자별 하루 질문 한도.

**왜 필요한가**

회원가입이 열려 있고, 질문 1건마다 임베딩 + LLM 호출이 나갑니다. 제한이
없으면 계정 하나로 API 비용을 무제한 태울 수 있습니다. OpenAI 대시보드의
월 한도는 "터지기 직전에 서비스 전체를 멈추는" 차단기라, 그것만 두면
한 명이 예산을 다 쓰고 나머지 사용자가 전부 막힙니다.

**왜 캐시가 아니라 DB 로 세는가**

gunicorn 워커가 3개인데 Django 기본 캐시(locmem)는 **프로세스마다 따로**
입니다. 캐시로 세면 실효 한도가 3배가 되고, 워커 수를 바꿀 때마다 조용히
달라집니다. `ChatLog` 는 이미 질문마다 한 행씩 쌓이고 `created_at` 에
인덱스가 있으므로(chat/models.py) 그걸 세는 편이 정확합니다.

Redis 를 붙이면 캐시로 옮길 수 있지만, 이 규모에서 COUNT 한 번은
LLM 호출 한 번 앞에서 무시할 만합니다.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone


@dataclass(frozen=True)
class Quota:
    limit: int          # 하루 한도 (0 이면 무제한)
    used: int           # 오늘 사용한 횟수
    exempt: bool        # 한도 면제 대상인가

    @property
    def unlimited(self) -> bool:
        return self.exempt or self.limit <= 0

    @property
    def remaining(self) -> int | None:
        """남은 횟수. 무제한이면 None."""
        return None if self.unlimited else max(0, self.limit - self.used)

    @property
    def exceeded(self) -> bool:
        return not self.unlimited and self.used >= self.limit


def _day_start():
    """오늘 0시(설정된 TIME_ZONE 기준).

    USE_TZ=True 이고 TIME_ZONE 이 Asia/Seoul 이므로, UTC 기준으로 세면
    한국 시각 오전 9시에 한도가 초기화되는 이상한 동작이 됩니다.
    localtime() 으로 현지 날짜를 잡고 다시 aware 로 만듭니다.
    """
    now = timezone.localtime()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def get_quota(user) -> Quota:
    from .models import ChatLog

    limit = int(getattr(settings, "CHAT_DAILY_LIMIT", 0) or 0)
    exempt = bool(
        getattr(settings, "CHAT_DAILY_LIMIT_EXEMPT_STAFF", True)
        and (user.is_superuser or user.is_staff)
    )

    if exempt or limit <= 0:
        # 셀 필요가 없으면 COUNT 쿼리도 날리지 않습니다.
        return Quota(limit=limit, used=0, exempt=exempt)

    used = ChatLog.objects.filter(user=user, created_at__gte=_day_start()).count()
    return Quota(limit=limit, used=used, exempt=False)


def limit_message(quota: Quota) -> str:
    """한도 초과 시 사용자에게 보여줄 문장.

    언제 풀리는지 같이 알려줍니다 — "잠시 후 다시" 류의 안내는 사용자가
    새로고침을 반복하게 만듭니다.
    """
    reset = (_day_start() + timezone.timedelta(days=1)).strftime("%m월 %d일 00시")
    return (
        f"하루 질문 한도({quota.limit}회)를 모두 사용했습니다. "
        f"{reset}에 초기화됩니다."
    )
