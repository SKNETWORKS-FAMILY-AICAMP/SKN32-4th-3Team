"""커뮤니티 게시판 View.

강사 자료 boards/views.py 의 제네릭 CBV 5종
(ListView/DetailView/CreateView/UpdateView/DeleteView) 구성을 따르고,
Ecobot 관례를 얹었습니다.

- design 변경(2R-2): 승인된 단지 소속(또는 서비스 운영자)만 접근 가능.
- design 변경(2R-3): 커뮤니티는 무조건 단지(apartment) 단위다. 목록은
  "지금 활성 단지" 글만 보여주고(scope.current_apartment), 상세·수정·
  삭제는 그 글이 속한 단지에 대한 접근 권한을 객체 단위로 한 번 더
  확인한다(BoardObjectAccessMixin) — 목록 게이트만으로는 다른 단지 글의
  URL을 직접 쳐서 들어오는 걸 못 막는다.
- 삭제는 작성자 본인 + 그 단지를 관리할 수 있는 사람(관리사무소 관리자/
  서비스 운영자)도 가능하다 — 수정은 요청받은 범위가 아니라 작성자만.
- 삭제는 POST 전용 + 템플릿 confirm — document_list 의 삭제 관례와 동일.
- 조회수는 UPDATE ... SET read_count = read_count + 1 (F 표현식).
  read-modify-write 로 하면 동시 조회에서 증가분이 유실됩니다.
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from apartments import permissions, scope

from .forms import BoardForm
from .models import Board


def _display_author(board):
    """작성자 표시 라벨. design 변경(2R-3): 그 글이 속한 단지의 승인된
    관리사무소 관리자가 쓴 글이면 닉네임 대신 "관리자"로 보인다 —
    관리사무소 명의의 공지·답변이라는 걸 나타내기 위함이다. 그 외에는
    지금까지처럼 닉네임(없으면 이름)."""
    if permissions.is_apartment_manager(board.author, board.apartment_id):
        return "관리자"
    return board.author.nickname or board.author.display_name


class CommunityAccessRequiredMixin(LoginRequiredMixin):
    """design 변경(2R-2): 커뮤니티는 승인된 단지 소속(또는 서비스 운영자)만
    들어갈 수 있다. 목록·상세까지 전면 잠근다 — 지금까지는 비로그인도
    글을 볼 수 있었지만, 사용자가 "커뮤니티 버튼 자체를 숨겨 달라"고
    명시했으므로 진입 버튼(뒤쪽 chat/room.html)뿐 아니라 URL 직접 접근도
    막아야 실질적인 차단이 된다."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)  # LoginRequiredMixin 이 로그인으로 보낸다
        if not permissions.has_community_access(request):
            messages.info(request, "아파트 소속이 승인된 회원만 커뮤니티를 이용할 수 있습니다.")
            return redirect("apartments:mine")
        return super().dispatch(request, *args, **kwargs)


class BoardObjectAccessMixin(CommunityAccessRequiredMixin):
    """design 변경(2R-3): 상세·수정·삭제는 목록 게이트(승인된 소속이
    하나라도 있는가)만으로는 부족하다 — 그 글이 "내가 승인된 그 단지"
    글인지 객체 단위로 한 번 더 확인해야, 다른 단지 글 URL을 직접
    쳐서 들어오는 걸 막을 수 있다."""

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if not permissions.can_access_apartment_community(self.request.user, obj.apartment_id):
            raise Http404("접근 권한이 없습니다.")
        return obj


class BoardListView(CommunityAccessRequiredMixin, ListView):
    """게시글 목록 — 지금 활성 단지 글만(무조건 단지별) + 키워드 검색(?kw=) + 페이지네이션.

    서비스 운영자가 활성 단지를 하나도 안 골랐으면(자기 소속이 없어도
    is_service_admin 이라 커뮤니티 자체엔 들어올 수 있다) 전체 단지를
    운영 목적으로 통으로 보여준다."""

    model = Board
    template_name = "boards/board_list.html"
    context_object_name = "boards"
    paginate_by = 10

    def get_queryset(self):
        self.apartment = scope.current_apartment(self.request)
        qs = Board.objects.select_related("author", "apartment")
        if self.apartment:
            qs = qs.filter(apartment=self.apartment)
        elif not self.request.user.is_service_admin:
            qs = qs.none()  # CommunityAccessRequiredMixin 이 이미 막아 이론상 도달 불가
        kw = self.request.GET.get("kw", "").strip()
        if kw:
            qs = qs.filter(Q(title__icontains=kw) | Q(content__icontains=kw))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        for board in ctx["boards"]:
            board.display_author = _display_author(board)
        ctx["apartment"] = self.apartment
        ctx["kw"] = self.request.GET.get("kw", "")
        # 페이지 이동 시 검색어가 유지되도록 page 를 뺀 쿼리스트링을 넘긴다.
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["qs_keep"] = params.urlencode()
        return ctx


class BoardDetailView(BoardObjectAccessMixin, DetailView):
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

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        board = ctx["board"]
        ctx["display_author"] = _display_author(board)
        ctx["can_delete"] = (
            board.author_id == self.request.user.id
            or permissions.can_manage_apartment(self.request.user, board.apartment_id)
        )
        return ctx


class BoardAuthorRequiredMixin(BoardObjectAccessMixin, UserPassesTestMixin):
    """작성자 본인만 통과(수정 전용). 비로그인/미승인/다른 단지 →
    커뮤니티 게이트, 본인이 아니면 403."""

    def test_func(self):
        return self.get_object().author_id == self.request.user.id


class BoardDeletePermissionMixin(BoardObjectAccessMixin, UserPassesTestMixin):
    """작성자 본인 또는 그 단지를 관리할 수 있는 사람(관리사무소 관리자/
    서비스 운영자)만 통과. design 변경(2R-3): 관리사무소 관리자는 자기
    관리 단지 글을 작성자가 아니어도 지울 수 있어야 한다는 요구사항."""

    def test_func(self):
        board = self.get_object()
        return (
            board.author_id == self.request.user.id
            or permissions.can_manage_apartment(self.request.user, board.apartment_id)
        )


class BoardCreateView(CommunityAccessRequiredMixin, CreateView):
    model = Board
    form_class = BoardForm
    template_name = "boards/board_form.html"

    def form_valid(self, form):
        apartment = scope.current_apartment(self.request)
        if not apartment:
            # 서비스 운영자가 활성 단지를 하나도 안 고른 경우 등 — 글은
            # 반드시 어느 단지 커뮤니티에 속해야 하므로 여기서 막는다.
            messages.info(self.request, "글을 쓰려면 소속된 단지가 있어야 합니다.")
            return redirect("apartments:mine")
        form.instance.author = self.request.user
        form.instance.apartment = apartment
        form.instance.region = apartment.region
        messages.success(self.request, "게시글이 등록되었습니다.")
        return super().form_valid(form)


class BoardUpdateView(BoardAuthorRequiredMixin, UpdateView):
    model = Board
    form_class = BoardForm
    template_name = "boards/board_form.html"

    def form_valid(self, form):
        messages.success(self.request, "게시글이 수정되었습니다.")
        return super().form_valid(form)


class BoardDeleteView(BoardDeletePermissionMixin, DeleteView):
    """POST 전용 삭제 — 확인은 목록·상세 화면의 confirm() 이 담당."""

    model = Board
    http_method_names = ["post"]
    success_url = reverse_lazy("boards:list")

    def form_valid(self, form):
        messages.success(self.request, "게시글이 삭제되었습니다.")
        return super().form_valid(form)
