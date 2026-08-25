"""아파트 단지 · 3단계 회원 계층 모델.

design: 사용자가 올린 APARTMENT_EXTENSION.md 를 근거로 확장안(Membership
M:N + 검토 게이트) 전체를 구현합니다. 3차/4차가 지켜온 원칙 — "근거로
삼을 자격을 무엇이 결정하는가" — 을 사람이 만든 데이터(단지 규정)에도
그대로 적용합니다: 검증되지 않은 규정은 색인되지 않습니다.

■ Member 모델은 건드리지 않습니다.
    관리사무소 관리자를 is_staff 로 표현하면 전체 서비스 대시보드가
    열립니다(dashboard/views.py:AdminRequiredMixin 참고). 세 계층은
    Member 필드가 아니라 Membership 행으로만 구분됩니다.

■ Member.apartment 단일 FK 가 아니라 Membership M:N 을 처음부터 씁니다.
    관리사무소 직원이 여러 단지를 위탁관리하는 경우가 실제로 흔하다는
    설계 문서의 지적을 반영해, 나중에 전환할 필요 없이 바로 M:N 으로
    시작합니다.
"""
from django.conf import settings
from django.db import models

from members.models import REGION_CHOICES


class Apartment(models.Model):
    """아파트 단지 1건.

    region 을 REGION_CHOICES 로 두는 이유: 단지 검색·신청 화면이 항상
    "지역 먼저 선택 → 그 지역 단지만 나열" 계단식으로 동작해야 하기
    때문입니다(원래 확장 후보 "지역별 > 아파트별 선택"의 완성).
    """

    name = models.CharField("단지명", max_length=100)
    region = models.CharField("지역", max_length=50, choices=REGION_CHOICES, db_index=True)
    address = models.CharField("주소", max_length=255, blank=True, default="")
    # K-apt 단지코드 대조 여부. 이번 라운드는 수동 시드만 하므로 항상 True.
    is_registered = models.BooleanField("단지 존재 확인", default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+", verbose_name="등록자",
    )
    created_at = models.DateTimeField("등록일", auto_now_add=True)
    updated_at = models.DateTimeField("수정일", auto_now=True)

    class Meta:
        db_table = "apartments"
        ordering = ["region", "name"]
        verbose_name = "아파트 단지"
        verbose_name_plural = "아파트 단지"
        constraints = [
            models.UniqueConstraint(fields=["name", "region"], name="uq_apartment_name_region"),
        ]

    def __str__(self):
        return f"[{self.get_region_display()}] {self.name}"


class Membership(models.Model):
    """Member ↔ Apartment 소속·권한.

    role 이 계층을 가른다: RESIDENT(입주민)는 코드 인증으로 즉시 승인되고
    쓰기 권한이 없다. MANAGER(관리사무소 관리자)는 항상 REQUESTED 로
    생성되고 서비스 운영자(is_staff)가 대시보드에서 승인해야 한다 —
    이미 승인자가 서비스 운영자 하나뿐이므로 "첫 관리자 부트스트랩"을
    위한 별도 분기가 필요 없다(APARTMENT_EXTENSION.md 5절).
    """

    class Role(models.TextChoices):
        RESIDENT = "resident", "입주민"
        MANAGER = "manager", "관리사무소 관리자"

    class Status(models.TextChoices):
        REQUESTED = "requested", "신청"
        APPROVED = "approved", "승인"
        REJECTED = "rejected", "거절"
        TERMINATED = "terminated", "해지"

    # ── design 변경(2R-1): 입주민도 코드 자기인증이 아니라 신청→관리사무소
    # 관리자 승인으로 바꿨다. Role/Status 는 이미 그대로 재사용 가능하다 —
    # role=RESIDENT 인 신청을 role=MANAGER 신청과 같은 큐 UI 패턴으로
    # (다만 승인자가 그 단지 관리자라는 점만 다르게) 처리한다.

    member = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="apartment_memberships", verbose_name="회원",
    )
    apartment = models.ForeignKey(
        Apartment, on_delete=models.CASCADE,
        related_name="memberships", verbose_name="단지",
    )
    role = models.CharField("역할", max_length=20, choices=Role.choices)
    status = models.CharField(
        "상태", max_length=20, choices=Status.choices, default=Status.REQUESTED, db_index=True,
    )
    # 여러 단지에 소속된 경우(위탁관리 등) 기본으로 쓸 단지 표시.
    is_primary = models.BooleanField("기본 단지", default=False)
    decision_note = models.TextField("승인/거절 사유", blank=True, default="")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+", verbose_name="승인자",
    )
    applied_at = models.DateTimeField("신청일", auto_now_add=True)
    decided_at = models.DateTimeField("처리일", null=True, blank=True)

    class Meta:
        db_table = "apartment_memberships"
        ordering = ["-applied_at"]
        verbose_name = "단지 소속"
        verbose_name_plural = "단지 소속"
        constraints = [
            models.UniqueConstraint(
                fields=["member", "apartment", "role"], name="uq_membership_member_apartment_role",
            ),
        ]

    def __str__(self):
        return f"{self.member} @ {self.apartment} ({self.get_role_display()}/{self.get_status_display()})"


class ApartmentRule(models.Model):
    """단지 배출 규정 1건. Board(자유 게시판)와 의도적으로 분리합니다 —
    게시판 원문을 그대로 색인하면 추측성 문장이 근거로 승격됩니다
    (rag/models.py 가 LLM 출력 summary 를 색인에서 뺀 것과 같은 원칙).
    """

    class Category(models.TextChoices):
        FOOD = "food", "음식물"
        RECYCLE = "recycle", "재활용"
        BULKY = "bulky", "대형폐기물"
        TIME = "time", "배출시간"
        PLACE = "place", "배출장소"
        ETC = "etc", "기타"

    class SourceLevel(models.TextChoices):
        OFFICIAL = "official", "관리사무소·규약"
        RESIDENT = "resident", "입주민 제보"

    class Status(models.TextChoices):
        DRAFT = "draft", "작성중"
        REVIEW = "review", "검토대기"
        APPROVED = "approved", "승인"
        REJECTED = "rejected", "반려"

    apartment = models.ForeignKey(Apartment, on_delete=models.CASCADE, related_name="rules", verbose_name="단지")
    category = models.CharField("분류", max_length=20, choices=Category.choices)
    content = models.TextField("규정 내용")
    photo = models.ImageField("배출장소 사진", upload_to="apartment_rules/%Y/%m/", blank=True, null=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+", verbose_name="제안자",
    )
    source_level = models.CharField("출처 등급", max_length=20, choices=SourceLevel.choices)
    # 색인 게이트. search()/ask() 가 아니라 색인 적재 단계(_load_from_db)에서만
    # 걸러야 _apply_quota() 자리배분과 안 얽힙니다 — rag/models.py:Document.status
    # 와 같은 패턴입니다.
    status = models.CharField("상태", max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    effective_from = models.DateField("적용 시작일", null=True, blank=True)
    effective_until = models.DateField("적용 종료일", null=True, blank=True)
    superseded_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="supersedes", verbose_name="대체 규정",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+", verbose_name="검토자",
    )
    # 승인 시 apartments/services.py:sync_rule_to_document() 가 채웁니다.
    document = models.ForeignKey(
        "rag.Document", on_delete=models.SET_NULL, null=True, blank=True,
        # related_name 을 "+"로 두지 않는다 — rag/service.py 가 답변 출처에
        # source_level/등록시점을 표시하려면 Document 에서 역참조로 이
        # 규정을 찾아야 한다.
        related_name="synced_by_rules", verbose_name="연동 문서",
    )
    created_at = models.DateTimeField("작성일", auto_now_add=True)
    updated_at = models.DateTimeField("수정일", auto_now=True)

    class Meta:
        db_table = "apartment_rules"
        ordering = ["-created_at"]
        verbose_name = "단지 규정"
        verbose_name_plural = "단지 규정"

    def __str__(self):
        return f"{self.apartment} - {self.get_category_display()} ({self.get_status_display()})"

    def is_within_window(self) -> bool:
        """오늘 날짜가 적용 기간 안인지. 시작/종료일이 없으면 무제한으로 본다."""
        from django.utils import timezone

        today = timezone.localdate()
        if self.effective_from and today < self.effective_from:
            return False
        if self.effective_until and today > self.effective_until:
            return False
        return True

