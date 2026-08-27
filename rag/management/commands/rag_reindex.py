"""문서 전체를 FAISS 인덱스로 재구축하는 관리 명령.

강사 자료 rag/management/commands/rag_reindex.py 와 같은 자리·같은 이름
입니다. 3차의 `python -m scripts.seed_docs` 안에 섞여 있던 "인덱싱" 부분을
분리한 것입니다.

    python manage.py rag_reindex              # 무조건 재구축 (기존 동작)
    python manage.py rag_reindex --if-needed  # 갱신 대기 상태일 때만

`--if-needed` 는 백그라운드 워커(ecobot-reindex.service)가 씁니다.
웹 요청은 rag.tasks.request_reindex() 로 "갱신 필요" 표시만 남기고 즉시
반환하며, 실제 작업은 이 명령이 별도 프로세스에서 처리합니다.
"""
import contextlib
import os
from pathlib import Path

# 파일 락은 플랫폼마다 API 가 다릅니다. fcntl 은 POSIX 전용이라 Windows 에서
# import 자체가 ModuleNotFoundError 로 죽습니다 — 팀 개발 환경이 Windows 라
# 이 명령(퀵스타트에 있습니다)이 통째로 안 돌게 됩니다.
try:                    # POSIX
    import fcntl
    msvcrt = None
except ImportError:     # Windows
    import msvcrt
    fcntl = None

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from rag import service, tasks


class _AlreadyRunning(Exception):
    """다른 프로세스가 이미 락을 쥐고 있음."""


@contextlib.contextmanager
def _exclusive_lock(path: Path):
    """되도록이면 커널이 풀어 주는 배타 락. 못 잡으면 _AlreadyRunning.

    "w" 가 아니라 "a+b" 로 여는 이유가 둘 있습니다. 하나는 **락을 잡기 전에
    파일을 비우지 않기** 위해서입니다("w" 는 여는 순간 자르므로, 락을 못 잡고
    빠지는 프로세스가 남의 락 파일을 건드리게 됩니다). 다른 하나는 Windows 의
    msvcrt.locking 이 바이너리 모드 파일 기술자를 요구하기 때문입니다.

    두 API 모두 이미 잠겨 있으면 OSError 를 냅니다(POSIX 는 그 하위 클래스인
    BlockingIOError). 그래서 OSError 하나로 받습니다.
    """
    os.makedirs(path.parent, exist_ok=True)
    with open(path, "a+b") as fh:
        try:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise _AlreadyRunning from exc

        try:
            yield
        finally:
            # POSIX 는 fd 가 닫히면 커널이 풉니다. Windows 는 명시적으로
            # 풀어야 하고, 이미 풀렸어도 오류가 나므로 무시합니다.
            if fcntl is None:
                fh.seek(0)
                with contextlib.suppress(OSError):
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


class Command(BaseCommand):
    help = "documents 테이블(또는 data/ 폴더)의 문서를 임베딩하여 FAISS 인덱스를 재구축합니다."

    def add_arguments(self, parser):
        parser.add_argument(
            "--if-needed",
            action="store_true",
            help="ReindexState.dirty 가 True 일 때만 실행합니다(백그라운드 워커용).",
        )

    def handle(self, *args, **options):
        # ── 중복 실행 방지 ──
        # systemd path 유닛(파일 변경)과 timer(5분 주기)가 같은 서비스를
        # 겹쳐 깨울 수 있고, 관리자가 손으로 돌릴 수도 있습니다. 임베딩을
        # 두 프로세스가 동시에 돌리면 API 비용이 두 배로 나가고 인덱스
        # 파일을 서로 덮어씁니다.
        #
        # DB 플래그로 락을 흉내 내지 않는 이유: 프로세스가 죽으면
        # status=running 이 영구히 남아 이후 실행이 전부 막힙니다.
        # 파일 락은 프로세스가 사라지면 OS 가 알아서 풀어 줍니다.
        lock_path = Path(settings.INDEX_DIR) / "reindex.lock"
        try:
            with _exclusive_lock(lock_path):
                self._reindex(options)
        except _AlreadyRunning:
            # 이미 돌고 있으면 조용히 빠집니다. dirty 는 그대로라
            # 지금 돌고 있는 쪽이 끝난 뒤 타이머가 다시 잡습니다.
            self.stdout.write("이미 재색인이 진행 중입니다 — 건너뜁니다.")

    def _reindex(self, options):
        if options["if_needed"]:
            result = tasks.run_if_needed()
            if result.get("skipped"):
                self.stdout.write("갱신할 변경이 없습니다.")
                return
        else:
            # 명시적 실행. 상태 표시도 함께 갱신되도록 같은 경로를 씁니다.
            try:
                result = tasks.run_if_needed(force=True)
            except Exception as exc:
                raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"인덱싱 완료: {result}"))
