"""data/laws · data/guide 폴더의 공용 문서를 Document 테이블에 적재합니다.

3차 scripts/seed_docs.py 의 이식본입니다.

    python manage.py seed_docs             # 폴더 → 테이블 동기화
    python manage.py seed_docs --reindex   # 적재 후 FAISS 재구축까지

■ 3차 대비 달라진 점
    - SQLAlchemy 세션 → Django ORM
    - 시스템 계정이 사라졌습니다. 3차는 documents.owner_id 가 NOT NULL
      이라 "system@local" 가짜 계정을 만들어 공용 문서 소유자로 붙였는데,
      4차 Document.owner 는 null 허용이므로 공용 문서는 owner=None 입니다.
      데모 계정 생성도 뺐습니다 — 이제 회원가입 화면이 있습니다.
    - upsert 키가 title → source_key("law:파일명" 형태)로 바뀌었습니다.
      3차는 제목(파일 첫 줄)이 바뀌면 같은 파일이 새 문서로 중복
      적재됐습니다. 파일명 기준 키면 제목이 바뀌어도 갱신으로 잡힙니다.
    - 폴더에서 사라진 파일의 레코드 삭제(트러블슈팅 4번)는 3차
      _remove_stale() 그대로, 단 source_key 기준입니다.
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from rag.law_text import count_articles, read_law_file
from rag.models import Document, SourceType

# 폴더 안내문 등 자료가 아닌 파일은 제외한다 (3차 IGNORED_STEMS 그대로)
IGNORED_STEMS = {"readme", "read_me", "notes", "메모"}
SUPPORTED = (".txt", ".md", ".pdf")

# 제목의 "[지역명]" → region 코드 (3차 seed_docs.REGION_MAP 그대로).
# 여기 없는 지역이나 법령은 None(=전국 공통)으로 남는다.
REGION_MAP = {
    "서울": "seoul",
    "천안": "cheonan",
    "부산 남구": "busan_namgu",
    "부산남구": "busan_namgu",
    "세종": "sejong",
    "인천 미추홀구": "incheon_michuhol",
    "인천미추홀구": "incheon_michuhol",
    "미추홀": "incheon_michuhol",
    "제주": "jeju",
}


def _extract_title(text: str, fallback: str) -> str:
    """첫 줄이 "[서울시] …" 형태면 제목으로 쓰고, 아니면 파일명을 쓴다."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        return line if line.startswith("[") and len(line) <= 80 else fallback
    return fallback


def _extract_region(title: str) -> str | None:
    for keyword, code in REGION_MAP.items():
        if keyword in title:
            return code
    return None


class Command(BaseCommand):
    help = "data/laws · data/guide 의 법령·가이드를 Document 테이블에 적재합니다."

    def add_arguments(self, parser):
        parser.add_argument("--reindex", action="store_true",
                            help="적재 후 FAISS 인덱스까지 재구축합니다.")

    def handle(self, *args, **options):
        self.stdout.write("[1/2] 공용 문서 읽기 및 적재")
        current_keys: set[str] = set()
        added = updated = 0

        folders = [
            (settings.LAWS_DIR, SourceType.LAW),
            (settings.GUIDE_DIR, SourceType.GUIDE),
        ]
        for folder, source_type in folders:
            folder.mkdir(parents=True, exist_ok=True)
            for path in sorted(folder.iterdir()):
                if not (path.is_file() and path.suffix.lower() in SUPPORTED):
                    continue
                if path.stem.lower() in IGNORED_STEMS or path.stem.startswith("_"):
                    continue

                try:
                    text = read_law_file(path)
                except Exception as exc:
                    self.stderr.write(f"  [건너뜀] {path.name}: {exc}")
                    continue
                if not text.strip():
                    self.stderr.write(f"  [건너뜀] 내용이 비어 있습니다: {path.name}")
                    continue

                title = _extract_title(text, path.stem)
                region = _extract_region(title)
                source_key = f"{source_type}:{path.name}"
                current_keys.add(source_key)

                if source_type == SourceType.LAW:
                    articles = count_articles(text)
                    if articles == 0:
                        self.stderr.write(
                            f"  [경고] {path.name}: 조문(제N조)을 찾지 못했습니다. "
                            "일반 문자 단위로 분할되어 조문 인용이 어려울 수 있습니다."
                        )
                    else:
                        self.stdout.write(f"  법령  : {title} — 조문 {articles}개, {len(text):,}자")
                else:
                    self.stdout.write(f"  가이드: {title} — {len(text):,}자 (지역: {region or '전국 공통'})")

                _, created = Document.objects.update_or_create(
                    source_key=source_key,
                    defaults={
                        "owner": None,
                        "title": title,
                        "content_text": text,
                        "source_type": source_type,
                        "region": region,
                    },
                )
                added += created
                updated += not created

        # 폴더에서 사라진 공용 문서 레코드 정리 (3차 트러블슈팅 4번).
        # 시드가 넣은 문서(source_key 가 law:/guide: 로 시작)만 대상으로 하고
        # 사용자 업로드(manual)는 건드리지 않는다.
        stale = Document.objects.filter(
            source_type__in=[SourceType.LAW, SourceType.GUIDE]
        ).exclude(source_key__in=current_keys)
        for doc in stale:
            self.stdout.write(f"  삭제: [{doc.source_type}] {doc.title}")
        removed = stale.count()
        stale.delete()

        self.stdout.write(self.style.SUCCESS(
            f"적재 완료: 추가 {added} · 갱신 {updated} · 삭제 {removed}"
        ))

        if not options["reindex"]:
            self.stdout.write("[2/2] 건너뜀 — 색인하려면 --reindex 또는 rag_reindex 를 실행하십시오.")
            return

        self.stdout.write("[2/2] 인덱스 재구축")
        from rag import service

        result = service.rebuild_index()
        self.stdout.write(self.style.SUCCESS(f"인덱싱 완료: {result}"))
        if result.get("indexed_chunks") == 0:
            self.stderr.write("[경고] 청크가 0개입니다. .env 의 RAG_SOURCE 가 db 인지 확인하세요.")
