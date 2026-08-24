"""커뮤니티 게시판 View.

강사 자료 boards/views.py 의 제네릭 CBV 5종
(ListView/DetailView/CreateView/UpdateView/DeleteView) 구성을 따르고,
Ecobot 관례를 얹었습니다.

- 읽기(목록·상세)는 공개, 쓰기(작성·수정·삭제)는 로그인 필요.
  랜딩·가이드가 공개인 것과 같은 결: 눈팅으로 유입 → 가입 유도.
- 수정·삭제는 작성자 본인만 — 아니면 403.
  rag 의 "남의 manual 문서 404" 와 다른 이유: 게시글은 공개라서
  존재를 숨길 게 없고, 숨기면 오히려 목록과 상세가 모순됩니다.
- 삭제는 POST 전용 + 템플릿 confirm — document_list 의 삭제 관례와 동일.
- 조회수는 UPDATE ... SET read_count = read_count + 1 (F 표현식).
  read-modify-write 로 하면 동시 조회에서 증가분이 유실됩니다.
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import F, Q
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from members.models import REGION_CHOICES

from .forms import BoardForm
from .models import Board


class BoardListView(ListView):
    """게시글 목록 — 지역 필터(?region=) + 키워드 검색(?kw=) + 페이지네이션."""

    model = Board
    template_name = "boards/board_list.html"
    context_object_name = "boards"
    paginate_by = 10

    def get_queryset(self):
        qs = Board.objects.select_related("author")
        region = self.request.GET.get("region", "").strip()
        if region:
            qs = qs.filter(region=region)
        kw = self.request.GET.get("kw", "").strip()
        if kw:
            qs = qs.filter(Q(title__icontains=kw) | Q(content__icontains=kw))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["region"] = self.request.GET.get("region", "")
        ctx["kw"] = self.request.GET.get("kw", "")
        ctx["region_choices"] = REGION_CHOICES
        # 페이지 이동 시 필터·검색어가 유지되도록 page 를 뺀 쿼리스트링을 넘긴다.
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["qs_keep"] = params.urlencode()
        return ctx


class BoardDetailView(DetailView):
    """게시글 상세 — 조회할 때마다 조회수 +1."""

    model = Board
    template_name = "boards/board_detail.html"
    context_object_name = "board"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        # F 표현식: DB 에서 원자적으로 증가 (동시 조회 유실 방지)
        Board.objects.filter(pk=obj.pk).update(read_count=F("read_count") + 1)
        obj.read_count += 1  # 화면 표시용 (재조회 없이 반영)
        return obj


class BoardAuthorRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """작성자 본인만 통과. 비로그인 → 로그인 리다이렉트, 타인 → 403."""

    def test_func(self):
        return self.get_object().author_id == self.request.user.id


class BoardCreateView(LoginRequiredMixin, CreateView):
    model = Board
    form_class = BoardForm
    template_name = "boards/board_form.html"

    def get_initial(self):
        # 회원 프로필의 거주 지역을 기본값으로 — 챗봇 기본 지역과 같은 발상.
        return {"region": self.request.user.region}

    def form_valid(self, form):
        form.instance.author = self.request.user
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
    """POST 전용 삭제 — 확인은 목록·상세 화면의 confirm() 이 담당."""

    model = Board
    http_method_names = ["post"]
    success_url = reverse_lazy("boards:list")

    def form_valid(self, form):
        messages.success(self.request, "게시글이 삭제되었습니다.")
        return super().form_valid(form)
