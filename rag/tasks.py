"""재색인을 요청 밖으로 빼는 얇은 계층.

**왜 있는가**

문서를 올리거나 지울 때마다 그 요청 안에서 `rebuild_index()` 가 전체 문서를
다시 임베딩하고 있었습니다(구 `rag/views.py:167`). 문서가 늘면 수십 초가
걸리는데 gunicorn 기본 타임아웃은 30초라, 넘기는 순간 워커가 죽어
업로드는 500 으로 끝나고 색인은 중간 상태로 남습니다. 배포 시점에는
`timeout = 180` 으로 버티고 있었지만 그건 임시방편이었습니다.

**어떻게 바꿨는가**

    웹 요청  →  request_reindex()  →  dirty=True + 트리거 파일 touch  →  즉시 반환
                                              │
                                     systemd path 유닛이 감지
                                              ▼
                              manage.py rag_reindex --if-needed
                                     (ecobot-reindex.service)

트리거를 놓쳐도 5분 주기 타이머(`ecobot-reindex.timer`)가 dirty 를 보고
같은 명령을 돌리므로, 알림이 유실돼도 결국 반영됩니다. 실패하면 dirty 가
남아 다음 실행이 다시 시도합니다.

**왜 Celery 를 안 쓰는가**

브로커(Redis)를 하나 더 띄워야 하고, 이 프로젝트에 비동기 작업은 이것
하나뿐입니다. systemd 는 이미 쓰고 있고 로그도 journald 로 모입니다.
작업 종류가 늘어나면 그때 옮기는 게 맞습니다.
"""
from __future__ import annotations

import logging
import os

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def request_reindex(reason: str = "") -> None:
    """재색인을 예약합니다. **블로킹하지 않습니다.**

    호출한 요청은 바로 응답을 돌려주면 됩니다. 실제 작업은 별도 프로세스가
    합니다. 예약 자체가 실패해도 예외를 올리지 않습니다 — 문서 저장은 이미
    끝났는데 색인 예약 때문에 사용자에게 500 을 주는 것이 더 나쁩니다.
    (5분 타이머가 뒤늦게라도 잡아냅니다)
    """
    from .models import ReindexState

    try:
        with transaction.atomic():
            state = ReindexState.get()
            state.dirty = True
            state.reason = (reason or "")[:200]
            state.requested_at = timezone.now()
            state.save(update_fields=["dirty", "reason", "requested_at"])
    except Exception:
        logger.exception("재색인 예약(DB) 실패 — reason=%s", reason)
        return

    _touch_trigger()
    logger.info("재색인 예약됨 — %s", reason or "(사유 없음)")


def _touch_trigger() -> None:
    """systemd path 유닛이 감시하는 파일의 mtime 을 갱신합니다.

    트리거가 실패해도 조용히 넘어갑니다. dirty 는 이미 DB 에 섰고, 타이머가
    최대 5분 뒤에 같은 일을 하기 때문입니다. 여기서 예외를 올리면 업로드
    요청이 실패하는데, 그건 늦게 반영되는 것보다 나쁩니다.

    open(...,"a") 로 여는 이유: touch 와 달리 파일이 없어도 만들고, 내용은
    건드리지 않으며, PathModified 를 확실히 발생시킵니다.
    """
    path = getattr(settings, "REINDEX_TRIGGER_FILE", None)
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a"):
            os.utime(path, None)
    except OSError as exc:
        logger.warning("재색인 트리거 파일을 건드리지 못했습니다(%s): %s", path, exc)


def run_if_needed(force: bool = False) -> dict:
    """dirty 일 때만 실제 재색인을 수행합니다. **워커 프로세스에서만** 부르십시오.

    웹 요청에서 부르면 예전과 똑같이 요청을 붙잡습니다.
    동시 실행 방지는 호출자(management command)의 flock 이 담당합니다 —
    DB 로 락을 흉내 내면 프로세스가 죽었을 때 status=running 이 영구히
    남아 이후 실행이 전부 막힙니다.
    """
    from . import service
    from .models import ReindexState

    state = ReindexState.get()
    if not (state.dirty or force):
        return {"skipped": True, "reason": "변경 없음"}

    state.status = ReindexState.Status.RUNNING
    state.started_at = timezone.now()
    state.save(update_fields=["status", "started_at"])

    try:
        result = service.rebuild_index()
    except Exception as exc:
        # dirty 를 내리지 않습니다 — 다음 실행이 다시 시도해야 합니다.
        state.status = ReindexState.Status.FAILED
        state.finished_at = timezone.now()
        state.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        state.save(update_fields=["status", "finished_at", "last_error"])
        logger.exception("재색인 실패")
        raise

    state.dirty = False
    state.status = ReindexState.Status.IDLE
    state.finished_at = timezone.now()
    state.last_error = ""
    state.last_result = result if isinstance(result, dict) else {"result": str(result)}
    state.save(
        update_fields=["dirty", "status", "finished_at", "last_error", "last_result"]
    )
    logger.info("재색인 완료 — %s", result)
    return result
