"""업로드 파일의 수명을 DB 레코드에 맞추는 모듈.

**문제**

Django 의 `FileField` 는 레코드를 지워도 **파일을 지우지 않습니다.**
1.3 에서 의도적으로 바뀐 동작인데(트랜잭션 롤백 시 파일을 되살릴 수 없어서),
그 결과 아무 처리도 안 하면 `media/` 에 고아 파일이 계속 쌓입니다.
파일을 교체 업로드할 때 밀려난 옛 파일도 마찬가지입니다.

이 프로젝트에는 `FileField`/`ImageField` 가 네 앱에 흩어져 있습니다:

    members.Member.photo
    boards.Post.attachment
    rag.Document.source_file
    apartments.ApartmentRule.source_file / .photo

**왜 각 앱이 아니라 여기 모았는가**

"레코드가 사라지면 파일도 사라진다"는 것은 앱별 규칙이 아니라 프로젝트
전체 정책입니다. 앱마다 흩어 두면 다섯 번째 `FileField` 를 추가하는 사람이
이 규칙의 존재를 모른 채 넘어가고, 그 앱만 조용히 새기 시작합니다.
여기서는 프로젝트 안의 모델을 훑어 `FileField` 를 **자동으로** 찾아 붙이므로
새 필드가 생겨도 따로 할 일이 없습니다.

**안전장치**

- 삭제는 `transaction.on_commit()` 이후에만 합니다. 커밋되지 않은 삭제
  때문에 파일을 날리면 되돌릴 방법이 없습니다.
- 같은 파일명을 다른 레코드가 참조하고 있으면 지우지 않습니다.
- 파일 삭제 실패는 로그만 남기고 넘어갑니다. 정리 작업 때문에 사용자의
  삭제 요청이 500 이 되면 안 됩니다.
"""
from __future__ import annotations

import logging
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.db import transaction
from django.db.models import FileField
from django.db.models.signals import post_delete, pre_save

logger = logging.getLogger(__name__)


def project_file_fields() -> list[tuple[type, list[str]]]:
    """프로젝트 안(BASE_DIR 하위) 모델의 FileField 목록을 돌려줍니다.

    앱 라벨을 손으로 적지 않는 이유는 새 앱·새 필드가 생겼을 때 이 목록을
    갱신하는 것을 잊기 때문입니다. django.contrib 같은 외부 앱은 경로가
    BASE_DIR 밖이라 자연히 걸러집니다.
    """
    base = Path(settings.BASE_DIR).resolve()
    found: list[tuple[type, list[str]]] = []

    for model in django_apps.get_models():
        app_path = Path(model._meta.app_config.path).resolve()
        if base not in app_path.parents and app_path != base:
            continue
        names = [f.name for f in model._meta.get_fields() if isinstance(f, FileField)]
        if names:
            found.append((model, names))

    return found


def _is_referenced_elsewhere(model, field_name: str, file_name: str, exclude_pk) -> bool:
    """같은 파일을 다른 레코드가 가리키고 있는지 확인합니다.

    Django 는 업로드 시 이름이 겹치면 접미사를 붙이므로 보통은 1:1 입니다.
    다만 fixture 로 넣었거나 손으로 값을 복사한 경우가 있을 수 있어,
    지우기 전에 한 번 확인합니다 — 잘못 지우면 남은 레코드의 파일이
    통째로 사라집니다.
    """
    qs = model._default_manager.filter(**{field_name: file_name})
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def _delete_file(model, field_name: str, file_name: str, exclude_pk=None) -> None:
    if not file_name:
        return
    try:
        if _is_referenced_elsewhere(model, field_name, file_name, exclude_pk):
            logger.info("파일 유지(다른 레코드가 참조 중): %s", file_name)
            return
        storage = model._meta.get_field(field_name).storage
        if storage.exists(file_name):
            storage.delete(file_name)
            logger.info("업로드 파일 삭제: %s", file_name)
    except Exception:
        # 파일 정리 실패가 사용자의 삭제 요청을 실패시키면 안 됩니다.
        logger.exception("업로드 파일 삭제 실패: %s", file_name)


def _make_post_delete(model, field_names: list[str]):
    def handler(sender, instance, **kwargs):
        names = [getattr(instance, f).name for f in field_names if getattr(instance, f, None)]
        pairs = list(zip(field_names, names))
        # 커밋된 뒤에만 지웁니다. 트랜잭션이 롤백되면 레코드는 살아 돌아오는데
        # 파일은 돌아오지 않습니다.
        transaction.on_commit(
            lambda: [
                _delete_file(model, fname, value, exclude_pk=instance.pk)
                for fname, value in pairs
                if value
            ]
        )

    return handler


def _make_pre_save(model, field_names: list[str]):
    def handler(sender, instance, **kwargs):
        if not instance.pk:
            return  # 신규 생성 — 밀려날 옛 파일이 없습니다.
        try:
            old = model._default_manager.get(pk=instance.pk)
        except model.DoesNotExist:
            return

        stale = []
        for fname in field_names:
            old_value = getattr(old, fname).name if getattr(old, fname, None) else ""
            new_value = getattr(instance, fname).name if getattr(instance, fname, None) else ""
            if old_value and old_value != new_value:
                stale.append((fname, old_value))

        if stale:
            transaction.on_commit(
                lambda: [
                    _delete_file(model, fname, value, exclude_pk=instance.pk)
                    for fname, value in stale
                ]
            )

    return handler


def connect() -> int:
    """시그널을 붙입니다. AppConfig.ready() 에서 한 번만 호출하십시오."""
    count = 0
    for model, field_names in project_file_fields():
        label = model._meta.label_lower
        # weak=False 가 **필수**입니다.
        # Django 시그널은 기본이 약한 참조인데, 여기서 넘기는 핸들러는
        # _make_*() 가 만든 클로저라 다른 곳에서 참조하지 않습니다. 기본값을
        # 쓰면 connect() 직후 GC 되어 **시그널이 조용히 아무 일도 하지
        # 않습니다** — 에러도 경고도 없이 고아 파일만 쌓입니다.
        post_delete.connect(
            _make_post_delete(model, field_names),
            sender=model,
            weak=False,
            dispatch_uid=f"file_cleanup_post_delete_{label}",
        )
        pre_save.connect(
            _make_pre_save(model, field_names),
            sender=model,
            weak=False,
            dispatch_uid=f"file_cleanup_pre_save_{label}",
        )
        count += 1
    return count
