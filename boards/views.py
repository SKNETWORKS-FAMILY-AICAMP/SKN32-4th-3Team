"""커뮤니티 게시판 View.

강사 자료 boards/views.py 의 제네릭 CBV 5종
(ListView/DetailView/CreateView/UpdateView/DeleteView) 구성을 따르고,
Ecobot 관례를 얹었습니다.

- 읽기(목록·상세)는 공개, 쓰기(작성·수정·삭제)는 로그인 필요.
- 수정·삭제는 작성자 본인만 — 아니면 403.
- 조회수는 F 표현식으로 원자적 증가.
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import F, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from members.models import REGION_CHOICES

from .forms import BoardForm, CommentForm
from .models import CATEGORY_CHOICES, Board, BoardLike, Comment


class BoardListView(ListView):
    """게시글 목록 — 카테고리·지역·정렬 필터 + 키워드 검색 + 페이지네이션."""

    model = Board
    template_name = "boards/board_list.html"
    context_object_name = "boards"
    paginate_by = 10

    def get_queryset(self):
        qs = Board.objects.select_related("author")
        region = self.request.GET.get("region", "").strip()
        if region:
            qs = qs.filter(region=region)
        category = self.request.GET.get("category", "").strip()
        if category:
            qs = qs.filter(category=category)
        kw = self.request.GET.get("kw", "").strip()
        if kw:
            qs = qs.filter(Q(title__icontains=kw) | Q(content__icontains=kw))
        sort = self.request.GET.get("sort", "latest").strip()
        if sort == "popular":
            qs = qs.order_by("-like_count", "-created_at")
        elif sort == "views":
            qs = qs.order_by("-read_count", "-created_at")
        else:
            qs = qs.order_by("-created_at")
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["region"] = self.request.GET.get("region", "")
        ctx["kw"] = self.request.GET.get("kw", "")
        ctx["category"] = self.request.GET.get("category", "")
        ctx["sort"] = self.request.GET.get("sort", "latest")
        ctx["region_choices"] = REGION_CHOICES
        ctx["category_choices"] = CATEGORY_CHOICES
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["qs_keep"] = params.urlencode()
        # 유저의 아파트명 (임시: region 기반)
        if self.request.user.is_authenticated:
            ctx["apartment_name"] = f"{self.request.user.get_region_display()} 에코빌"
        return ctx


class BoardDetailView(DetailView):
    """게시글 상세 — 조회수 +1, 댓글 목록, 좋아요 상태."""

    model = Board
    template_name = "boards/board_detail.html"
    context_object_name = "board"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        Board.objects.filter(pk=obj.pk).update(read_count=F("read_count") + 1)
        obj.read_count += 1
        return obj

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["comments"] = self.object.comments.select_related("author").all()
        ctx["comment_form"] = CommentForm()
        if self.request.user.is_authenticated:
            ctx["user_liked"] = BoardLike.objects.filter(
                board=self.object, user=self.request.user
            ).exists()
        else:
            ctx["user_liked"] = False
        return ctx


class BoardAuthorRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """작성자 본인만 통과. 비로그인 → 로그인 리다이렉트, 타인 → 403."""

    def test_func(self):
        return self.get_object().author_id == self.request.user.id


class BoardCreateView(LoginRequiredMixin, CreateView):
    model = Board
    form_class = BoardForm
    template_name = "boards/board_form.html"

    def form_valid(self, form):
        form.instance.author = self.request.user
        form.instance.region = self.request.user.region
        messages.success(self.request, "게시글이 등록되었습니다.")
        return super().form_valid(form)


class BoardUpdateView(BoardAuthorRequiredMixin, UpdateView):
    model = Board
    form_class = BoardForm
    template_name = "boards/board_form.html"

    def form_valid(self, form):
        messages.success(self.request, "게시글이 수정되었습니다.")
        return super().form_valid(form)


class BoardDeleteView(BoardAuthorRequiredMixin, DeleteView):
    """POST 전용 삭제."""

    model = Board
    http_method_names = ["post"]
    success_url = reverse_lazy("boards:list")

    def form_valid(self, form):
        messages.success(self.request, "게시글이 삭제되었습니다.")
        return super().form_valid(form)


class BoardLikeView(LoginRequiredMixin, View):
    """좋아요 토글 — POST 전용. JSON 응답."""

    def post(self, request, pk):
        board = get_object_or_404(Board, pk=pk)
        like, created = BoardLike.objects.get_or_create(board=board, user=request.user)
        if not created:
            like.delete()
            Board.objects.filter(pk=pk).update(like_count=F("like_count") - 1)
            liked = False
        else:
            Board.objects.filter(pk=pk).update(like_count=F("like_count") + 1)
            liked = True
        board.refresh_from_db()
        return JsonResponse({"liked": liked, "like_count": board.like_count})


class CommentCreateView(LoginRequiredMixin, View):
    """댓글 작성 — POST 전용."""

    def post(self, request, pk):
        board = get_object_or_404(Board, pk=pk)
        form = CommentForm(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.board = board
            comment.author = request.user
            comment.save()
            Board.objects.filter(pk=pk).update(comment_count=F("comment_count") + 1)
            messages.success(request, "댓글이 등록되었습니다.")
        return redirect("boards:detail", pk=pk)


class CommentDeleteView(LoginRequiredMixin, View):
    """댓글 삭제 — POST 전용. 작성자 본인만."""

    def post(self, request, pk, comment_pk):
        comment = get_object_or_404(Comment, pk=comment_pk, board_id=pk)
        if comment.author_id != request.user.id:
            messages.error(request, "본인 댓글만 삭제할 수 있습니다.")
        else:
            comment.delete()
            Board.objects.filter(pk=pk).update(comment_count=F("comment_count") - 1)
            messages.success(request, "댓글이 삭제되었습니다.")
        return redirect("boards:detail", pk=pk)
