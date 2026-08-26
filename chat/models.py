"""챗봇 대화방 · 대화 기록 · 질문 로그 모델.

3차 app/models.py 의 ChatMessage / ChatLog 를 옮기면서 대화방 개념을
정식 모델로 승격했습니다.

■ ChatSession 을 새로 만든 이유
    3차 ChatMessage.session_id 는 `String(50)` 이고, 프론트의 "새 대화"
    버튼이 만드는 Date.now() 값을 문자열로 받아 저장했습니다.
    (JS 타임스탬프가 32비트 정수 범위를 넘어서 String 으로 받았다는
    주석이 모델에 그대로 남아 있습니다)

    이 방식의 문제:
      - session_id 가 NULL 인 옛 데이터를 "레거시 대화" 하나로 묶는
        예외 처리가 라우터·프론트 양쪽에 흩어집니다
      - 대화방의 제목·마지막 지역 같은 속성을 넣을 곳이 없어서
        "마지막 메시지의 region 을 읽는다"는 우회가 필요했습니다
      - 클라이언트가 만든 ID 라 충돌·위조를 막을 수 없습니다

    FK 로 바꾸면 대화방 목록 조회가 Session.objects.filter(owner=...)
    한 줄이 되고, region·title 이 대화방 속성으로 자연스럽게 들어갑니다.

■ ChatLog 를 남긴 이유
    ChatMessage(대화 원문)와 목적이 다릅니다. ChatLog 는 질문·지역·
    답변성공여부만 남기는 통계 전용이고 대시보드가 이것만 집계합니다.
    대화 원문 테이블을 통계용으로 쓰면 role="user" 필터 + 성공여부
    판정이 매 쿼리마다 붙습니다. 3차 판단대로 분리 유지합니다.
"""
from django.conf import settings
from django.db import models

from members.models import REGION_CHOICES
from rag.models import QuestionCluster


class ChatSession(models.Model):
    """대화방 하나. "새 대화" 버튼이 이 레코드를 만듭니다."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_sessions",
        verbose_name="사용자",
    )
    title = models.CharField(
        "대화방 제목",
        max_length=100,
        blank=True,
        default="",
        help_text="비어 있으면 첫 질문을 잘라서 표시합니다.",
    )
    region = models.CharField(
        "지역",
        max_length=50,
        choices=REGION_CHOICES,
        default="seoul",
        help_text="이 대화방에서 사용하는 지역. 기본값은 회원 프로필의 region 입니다.",
    )
    # 4차 2R 추가분: 대화방 생성 시점의 단지 컨텍스트를 고정한다.
    # apartments.scope.current_apartment_id() 로 구해 생성 시 한 번만 넣고,
    # 이후 회원이 단지를 바꿔도 이 대화방은 옛 기준으로 남는다 — 프로필을
    # 바꿨는데 옛 답이 나오는 걸 "왜 그런지 모르게" 만들지 않기 위해,
    # 대화방 목록 화면에 이 값을 배지로 보여준다(설계 문서 10절).
    apartment = models.ForeignKey(
        "apartments.Apartment", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="단지",
    )
    created_at = models.DateTimeField("생성일", auto_now_add=True)
    updated_at = models.DateTimeField("마지막 활동", auto_now=True)

    class Meta:
        db_table = "chat_sessions"
        # 최근 활동한 대화방이 목록 위로 오게 합니다.
        ordering = ["-updated_at"]
        verbose_name = "대화방"
        verbose_name_plural = "대화방"

    def __str__(self):
        return self.display_title

    @property
    def display_title(self) -> str:
        if self.title:
            return self.title
        first = self.messages.filter(role=ChatMessage.Role.USER).first()
        return (first.content[:30] if first else "새 대화")


class ChatMessage(models.Model):
    """대화 원문 1건. 화면 복원과 다음 질문의 맥락에 쓰입니다."""

    class Role(models.TextChoices):
        USER = "user", "사용자"
        ASSISTANT = "assistant", "챗봇"

    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name="대화방",
    )
    role = models.CharField("발화자", max_length=20, choices=Role.choices)
    content = models.TextField("본문")
    tip = models.TextField("실천 팁", blank=True, default="")
    # 근거 목록. [{"document_id":.., "title":.., "snippet":..}, ...]
    # JSONField 를 쓰면 3차의 json.dumps/json.loads 왕복이 사라집니다.
    sources = models.JSONField("근거 목록", default=list, blank=True)

    # ── 4차 추가분 ──
    class Feedback(models.TextChoices):
        POSITIVE = "positive", "좋아요"
        NEGATIVE = "negative", "싫어요"

    feedback = models.CharField(
        "피드백", max_length=10, choices=Feedback.choices,
        null=True, blank=True, db_index=True,
    )
    feedback_at = models.DateTimeField("피드백 시각", null=True, blank=True)

    # 검색 실패 시 추천한 과거 질문. [{"question":.., "count":..}, ...]
    suggested_questions = models.JSONField("추천 질문", default=list, blank=True)
    # 근거 법령 중 아직 시행 전인 것이 있을 때의 안내 문구.
    law_notice = models.TextField("법령 시행 안내", blank=True, default="")
    # 근거를 못 찾았을 때 보여줄 연락처 카드.
    # [{"type": "office"|"local_gov", "title":.., "phone":.., "address":.., ...}, ...]
    contact_cards = models.JSONField("연락처 카드", default=list, blank=True)

    created_at = models.DateTimeField("작성일", auto_now_add=True, db_index=True)

    class Meta:
        db_table = "chat_messages"
        ordering = ["created_at", "id"]
        verbose_name = "대화 메시지"
        verbose_name_plural = "대화 메시지"

    def __str__(self):
        return f"[{self.role}] {self.content[:30]}"


class ChatLog(models.Model):
    """질문 통계 로그. 관리자 대시보드 집계 전용입니다."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_logs",
        verbose_name="사용자",
    )
    question = models.TextField("질문")
    region = models.CharField("지역", max_length=50, choices=REGION_CHOICES, default="seoul")
    # 근거 기반 답변에 성공했는지. 자료없음 대응률 지표의 분모/분자가 됩니다.
    has_answer = models.BooleanField("답변 성공", default=True)
    cluster = models.ForeignKey(
        QuestionCluster,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logs",
        verbose_name="질문 클러스터",
    )
    # 4차 2R 추가분: 단지 축 통계용(관리자 대시보드에서 단지별 질문 추이를
    # 보고 싶을 때 대비). 단지 미가입 사용자의 질문이면 비어 있다.
    apartment = models.ForeignKey(
        "apartments.Apartment", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="단지",
    )
    created_at = models.DateTimeField("질문 시각", auto_now_add=True, db_index=True)

    class Meta:
        db_table = "chat_logs"
        ordering = ["-created_at"]
        verbose_name = "질문 로그"
        verbose_name_plural = "질문 로그"

    def __str__(self):
        return f"{self.question[:30]} ({self.region})"
