"""RAG 색인 대상 문서 모델.

3차 app/models.py 의 Document 를 Django ORM 으로 옮긴 것입니다.

■ "사용자 문서도 함께 검색" 시나리오는 유지합니다
    3차 초안의 이 시나리오는 살아 있는 설계이고 4차에서도 유지할 가치가
    있습니다. 필요한 부품이 모두 남아 있습니다.

        owner (FK, null 허용)  → 본인 문서만 검색되게 하는 필터 키
        content_text           → 실제로 색인되는 본문
        source_type="manual"   → 사용자가 올린 문서

    rag/service.py 의 search() 필터가 그대로 동작합니다.

        r["owner_id"] == owner_id or r["source_type"] in ("law", "guide")
        → 본인 문서 + 공용 법령·가이드

    Ecobot 에서의 쓸모: 아파트 관리사무소 공지문, 우리 동 배출 안내문을
    올리면 법령·지역 가이드와 함께 근거로 잡힙니다. 3차에는 이 문서를
    만들 UI 경로가 없어서(회원가입 화면조차 없었음) 기능이 잠들어 있었을
    뿐입니다. 4차에서 업로드 화면을 붙이면 살아납니다.

■ 제거한 필드와 근거 (시나리오와 무관한 에디터 부품들)

    content (에디터 JSON 원본)  → 제거
        실제 저장값을 확인해보니 두 상태가 섞여 있었습니다.

        (1) seed_docs.py 는 `content=content, content_text=content` 로
            **같은 평문을 두 컬럼에 똑같이** 넣습니다. 법령 원문 261KB 가
            두 번 저장됩니다. _load_from_db() 의 중복 제거
            (`if candidate not in parts`) 덕분에 색인이 두 배가 되는 것만
            우연히 막혀 있는 상태입니다.

        (2) 프론트 에디터가 저장하는 경우 content 는 TipTap JSON 문자열
            입니다. 이때 _load_from_db() 는 law 가 아닌 문서에 대해 parts 를
            "\\n\\n".join 하므로 **직렬화된 JSON 이 색인 본문에 섞여
            들어갑니다.** 실제로 재현해서 확인했습니다.

                '평문 본문\\n\\n{"type":"doc","content":[{"type":"paragraph"}]}'

            근거 스니펫에 JSON 조각이 노출되고 임베딩도 JSON 토큰까지 같이
            벡터화합니다. 기능이 아니라 결함입니다.

        → content_text 하나만 색인 대상으로 남깁니다. schemas.py 주석에
          적힌 프론트 계약("content_text 는 항상 같이 보낸다")과도 맞습니다.
          4차에 리치 에디터가 필요해지면 색인에서 **명시적으로 제외되는**
          별도 필드를 추가하는 편이 안전합니다.

    summary  → 제거
        _load_from_db() 가 summary 도 색인 본문에 이어 붙입니다.
        summary 는 LLM 이 생성한 문장입니다. 모델 출력을 다음 답변의
        "근거"로 색인하면 환각이 근거로 승격되는 경로가 생깁니다.
        대표 지표가 환각률 6.7% 인데 스스로 그 경로를 열어둘 이유가
        없습니다. (지금은 seed 문서의 summary 가 NULL 이라 잠들어 있을 뿐)

    parent_id / children (문서 트리)  → 제거
        문서 계층은 RAG 와 무관한 Notion 류 내비게이션입니다.
        법령·가이드에 상하위 관계가 없고 업로드 문서에도 필요 없습니다.
"""
from django.conf import settings
from django.db import models

from members.models import REGION_CHOICES


class SourceType(models.TextChoices):
    LAW = "law", "법령 원문"
    GUIDE = "guide", "배출 가이드"
    MANUAL = "manual", "사용자 업로드"


class Document(models.Model):
    """법령 · 가이드 · 사용자 업로드 문서 1건. 청킹 전 단위입니다."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="documents",
        verbose_name="소유자",
        help_text="사용자가 올린 문서만 채워집니다. 공용 문서는 비어 있습니다.",
    )
    title = models.CharField("제목", max_length=255)
    # Django 의 TextField 는 MySQL 백엔드에서 LONGTEXT 로 생성됩니다.
    # 3차 트러블슈팅 1번(법령 원문 261KB 가 TEXT 65,535바이트 한계를 넘어
    # "Incorrect string value" 로 잘리던 문제)이 여기서 자동 해결됩니다.
    #
    # ⚠️ 색인되는 유일한 본문 필드입니다. 평문이 아닌 것을 넣으면
    #    그대로 벡터화됩니다 (모델 상단 주석의 content 사례 참고).
    content_text = models.TextField("본문", blank=True, default="")
    source_type = models.CharField(
        "문서 종류",
        max_length=20,
        choices=SourceType.choices,
        default=SourceType.MANUAL,
        db_index=True,
    )
    source_url = models.URLField("원본 링크", max_length=500, blank=True, null=True)
    # 업로드된 원본 파일. 답변 근거를 원문과 직접 대조할 수 있게 보관합니다.
    # 3차 POST /api/admin/upload 는 data/guide/ 에 파일만 떨어뜨리고
    # documents 테이블과 연결이 끊겨 있었습니다 (rag/views.py 주석 참고).
    source_file = models.FileField(
        "원본 파일",
        upload_to="documents/%Y/%m/",
        blank=True,
        null=True,
    )
    region = models.CharField(
        "적용 지역",
        max_length=50,
        choices=REGION_CHOICES,
        null=True,
        blank=True,
        db_index=True,
        help_text="비어 있거나 common 이면 전국 공통으로 취급합니다.",
    )
    # 폴더 기준 자동 동기화(3차 트러블슈팅 4번)에 쓰는 키.
    # 파일이 삭제·이름 변경되면 이 키로 옛 레코드를 찾아 같이 지웁니다.
    source_key = models.CharField(
        "원본 파일 키",
        max_length=255,
        blank=True,
        default="",
        db_index=True,
    )
    created_at = models.DateTimeField("등록일", auto_now_add=True)
    updated_at = models.DateTimeField("수정일", auto_now=True)

    class Meta:
        db_table = "documents"
        ordering = ["source_type", "title"]
        verbose_name = "문서"
        verbose_name_plural = "문서"
        indexes = [
            # search() 가 owner_id + source_type 으로 거르므로 복합 인덱스.
            models.Index(fields=["owner", "source_type"], name="doc_owner_type_idx"),
        ]

    def __str__(self):
        return self.title

    @property
    def is_public(self) -> bool:
        """소유자와 무관하게 모든 사용자가 검색할 수 있는 문서인지.

        rag/service.py 의 PUBLIC_SOURCE_TYPES 와 같은 기준입니다.
        """
        return self.source_type in (SourceType.LAW, SourceType.GUIDE)


class QuestionCluster(models.Model):
    """의미가 같은 질문을 묶은 클러스터. 인기 질문 집계용.

    3차 트러블슈팅 8번의 해법입니다. GROUP BY question(문자열 완전 일치)
    으로 집계하면 "종이컵 버리는 법"과 "종이컵은 어떻게 버려요?"가 별개로
    카운트되어 실제 인기 주제가 분산됩니다. 질문 임베딩을 기존 클러스터
    벡터와 코사인 비교해 settings.QUESTION_CLUSTER_THRESHOLD 이상이면
    편입합니다.

    embedding 을 JSON 문자열로 저장하는 이유:
        MySQL 에 벡터 타입이 없고, 클러스터 수가 수백 단위라 전체를 읽어
        numpy 로 비교하는 편이 단순합니다. 수만 개로 늘어나면 벡터
        인덱스 도입을 검토할 시점입니다.
    """

    representative = models.TextField("대표 질문")
    embedding = models.TextField("대표 벡터(JSON)")
    count = models.PositiveIntegerField("질문 횟수", default=1)
    created_at = models.DateTimeField("생성일", auto_now_add=True)

    class Meta:
        db_table = "question_clusters"
        ordering = ["-count"]
        verbose_name = "질문 클러스터"
        verbose_name_plural = "질문 클러스터"

    def __str__(self):
        return f"{self.representative[:30]} ({self.count}회)"
