"""분리배출 Q&A 커뮤니티 게시판.

4차 신규 기능입니다. 강사 자료 boards/models.py 의 Board 를 골격으로
쓰되, 이 게시글을 RAG 근거에 포함시키는 것이 Ecobot 에서의 의미입니다.

■ RAG 와 연결되는 지점
    강사 자료 rag/service.py 의 sync_boards() 가 게시글을 벡터DB 에
    색인합니다. Ecobot 에 붙이면 "우리 동네 사람이 실제로 이렇게 버렸다"
    가 법령·가이드와 함께 검색 근거로 올라옵니다.
    3차가 정적 문서 기반 RAG 였다면, 4차는 회원이 생성한 데이터가
    근거로 편입되는 구조가 됩니다. Django 를 도입하는 명분입니다.

    다만 _apply_quota() 의 자리 배분에 "커뮤니티" 그룹을 추가할지는
    결정이 필요합니다. 지역/공통/법령 3분할에 4번째를 넣으면 기존
    측정치(통과율 93.3%)가 무효가 되므로, 별도 실험 후 판단하십시오.
"""
from django.conf import settings
from django.db import models

from members.models import REGION_CHOICES


CATEGORY_CHOICES = [
    ("question", "질문"),
    ("info", "정보공유"),
    ("review", "후기"),
    ("free", "자유"),
]


class Board(models.Model):
    """커뮤니티 게시글 1건."""

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="boards",
        verbose_name="작성자",
    )
    title = models.CharField("제목", max_length=200)
    content = models.TextField("내용")
    category = models.CharField(
        "카테고리",
        max_length=20,
        choices=CATEGORY_CHOICES,
        default="free",
        db_index=True,
    )
    region = models.CharField(
        "지역",
        max_length=50,
        choices=REGION_CHOICES,
        default="common",
        db_index=True,
        help_text="어느 지역 기준의 글인지. 검색 시 지역 필터에 쓰입니다.",
    )
    attachment = models.FileField("첨부파일", upload_to="board_files/%Y/%m/", blank=True, null=True)
    read_count = models.PositiveIntegerField("조회수", default=0)
    like_count = models.PositiveIntegerField("좋아요 수", default=0)
    comment_count = models.PositiveIntegerField("댓글 수", default=0)
    created_at = models.DateTimeField("작성일", auto_now_add=True)
    updated_at = models.DateTimeField("수정일", auto_now=True)

    class Meta:
        db_table = "boards"
        ordering = ["-created_at"]
        verbose_name = "게시글"
        verbose_name_plural = "게시글"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        """CreateView/UpdateView 가 저장 후 이동할 기본 주소."""
        from django.urls import reverse

        return reverse("boards:detail", args=[self.pk])


class Comment(models.Model):
    """게시글 댓글."""

    board = models.ForeignKey(
        Board,
        on_delete=models.CASCADE,
        related_name="comments",
        verbose_name="게시글",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comments",
        verbose_name="작성자",
    )
    content = models.TextField("내용")
    created_at = models.DateTimeField("작성일", auto_now_add=True)
    updated_at = models.DateTimeField("수정일", auto_now=True)

    class Meta:
        db_table = "board_comments"
        ordering = ["created_at"]
        verbose_name = "댓글"
        verbose_name_plural = "댓글"

    def __str__(self):
        return f"{self.author} → {self.board.title[:20]}"


class BoardLike(models.Model):
    """게시글 좋아요. 유저당 1개 제한."""

    board = models.ForeignKey(
        Board,
        on_delete=models.CASCADE,
        related_name="likes",
        verbose_name="게시글",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="board_likes",
        verbose_name="사용자",
    )
    created_at = models.DateTimeField("생성일", auto_now_add=True)

    class Meta:
        db_table = "board_likes"
        unique_together = [("board", "user")]
        verbose_name = "좋아요"
        verbose_name_plural = "좋아요"
