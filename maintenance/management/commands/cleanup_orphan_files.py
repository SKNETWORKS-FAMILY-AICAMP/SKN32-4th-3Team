"""MEDIA_ROOT 에서 어떤 레코드도 참조하지 않는 파일을 찾아 정리합니다.

`maintenance/file_cleanup.py` 의 시그널은 **앞으로** 생길 고아 파일을 막습니다.
이 명령은 그 전에 이미 쌓인 것과, 시그널이 놓친 경우(직접 SQL 로 지웠다거나
파일 삭제가 실패했을 때)를 회수합니다.

    python manage.py cleanup_orphan_files              # 목록만 (기본값)
    python manage.py cleanup_orphan_files --delete     # 실제로 삭제
    python manage.py cleanup_orphan_files --delete --min-age-hours 1

**기본이 조회 전용인 이유**: 지운 파일은 되돌릴 수 없습니다. 무엇이 지워질지
먼저 눈으로 확인하게 합니다.

**--min-age-hours (기본 24)**: 업로드는 "파일을 먼저 쓰고 → 레코드를 저장"
하는 순서라, 그 사이에 이 명령이 돌면 방금 올라온 파일이 고아로 보입니다.
갓 만들어진 파일은 손대지 않습니다.
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from maintenance.file_cleanup import project_file_fields


class Command(BaseCommand):
    help = "MEDIA_ROOT 에서 DB 가 참조하지 않는 업로드 파일을 찾습니다(기본: 조회만)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete", action="store_true",
            help="실제로 삭제합니다. 지정하지 않으면 목록만 보여줍니다.",
        )
        parser.add_argument(
            "--min-age-hours", type=int, default=24,
            help="이 시간보다 최근에 만들어진 파일은 건너뜁니다(기본 24). "
                 "업로드 도중인 파일을 지우지 않기 위한 장치입니다.",
        )

    def handle(self, *args, **options):
        media_root = Path(settings.MEDIA_ROOT)
        if not media_root.exists():
            self.stdout.write(f"MEDIA_ROOT 가 없습니다: {media_root}")
            return

        # ── 1. DB 가 참조하는 파일명 모으기 ──
        referenced: set[str] = set()
        for model, field_names in project_file_fields():
            for fname in field_names:
                values = (
                    model._default_manager
                    .exclude(**{f"{fname}": ""})
                    .exclude(**{f"{fname}__isnull": True})
                    .values_list(fname, flat=True)
                )
                referenced.update(v for v in values if v)

        self.stdout.write(f"DB 참조 파일: {len(referenced)}건")

        # ── 2. 디스크의 파일과 대조 ──
        cutoff = timezone.now() - timedelta(hours=options["min_age_hours"])
        orphans: list[tuple[Path, int]] = []
        skipped_recent = 0
        total = 0

        for path in media_root.rglob("*"):
            if not path.is_file():
                continue
            total += 1
            # storage 가 쓰는 이름은 MEDIA_ROOT 기준 상대 경로입니다.
            rel = path.relative_to(media_root).as_posix()
            if rel in referenced:
                continue
            mtime = timezone.datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.get_current_timezone()
            )
            if mtime > cutoff:
                skipped_recent += 1
                continue
            orphans.append((path, path.stat().st_size))

        self.stdout.write(f"디스크 파일  : {total}건")
        if skipped_recent:
            self.stdout.write(
                f"최근 파일 건너뜀: {skipped_recent}건 "
                f"(최근 {options['min_age_hours']}시간 이내)"
            )

        if not orphans:
            self.stdout.write(self.style.SUCCESS("고아 파일이 없습니다."))
            return

        freed = sum(size for _, size in orphans)
        self.stdout.write(
            self.style.WARNING(f"\n고아 파일 {len(orphans)}건 ({freed / 1024:.1f} KB)")
        )
        for path, size in orphans:
            self.stdout.write(f"  {path.relative_to(media_root)}  ({size / 1024:.1f} KB)")

        if not options["delete"]:
            self.stdout.write(
                "\n조회만 했습니다. 실제로 지우려면 --delete 를 붙이십시오."
            )
            return

        # ── 3. 삭제 ──
        deleted = failed = 0
        for path, _ in orphans:
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                self.stderr.write(f"  삭제 실패 {path}: {exc}")
                failed += 1

        # 빈 디렉터리 정리 (upload_to 의 %Y/%m 때문에 껍데기가 남습니다).
        # 깊은 곳부터 올라오며 지웁니다.
        removed_dirs = 0
        for d in sorted(media_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                try:
                    d.rmdir()
                    removed_dirs += 1
                except OSError:
                    pass

        msg = f"삭제 {deleted}건 ({freed / 1024:.1f} KB 회수)"
        if removed_dirs:
            msg += f", 빈 디렉터리 {removed_dirs}개 정리"
        if failed:
            self.stdout.write(self.style.WARNING(msg + f", 실패 {failed}건"))
        else:
            self.stdout.write(self.style.SUCCESS(msg))
