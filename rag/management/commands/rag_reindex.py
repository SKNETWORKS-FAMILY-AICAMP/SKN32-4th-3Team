"""문서 전체를 FAISS 인덱스로 재구축하는 관리 명령.

강사 자료 rag/management/commands/rag_reindex.py 와 같은 자리·같은 이름
입니다. 3차의 `python -m scripts.seed_docs` 안에 섞여 있던 "인덱싱" 부분을
분리한 것입니다.

    python manage.py rag_reindex
"""
from django.core.management.base import BaseCommand, CommandError

from rag import service


class Command(BaseCommand):
    help = "documents 테이블(또는 data/ 폴더)의 문서를 임베딩하여 FAISS 인덱스를 재구축합니다."

    def handle(self, *args, **options):
        try:
            result = service.rebuild_index()
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"인덱싱 완료: {result}"))
