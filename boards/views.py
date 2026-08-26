"""커뮤니티 게시판 View.

강사 자료 boards/views.py 의 제네릭 CBV 5종
(ListView/DetailView/CreateView/UpdateView/DeleteView) 구성을 따르고,
Ecobot 관례를 얹었습니다.

- 읽기(목록·상세)는 공개, 쓰기(작성·수정·삭제)는 로그인 필요.
- 수정·삭제는 작성자 본인만 — 아니면 403.
- 조회수는 F 표현식으로 원자적 증가.
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
from django.utils import timezone
from django.http import Http404, JsonResponse
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

from apartments import permissions, scope

from .forms import BoardForm, CommentForm
from .models import CATEGORY_CHOICES, Board, BoardLike, Comment


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
    막아야 실질적인 차단이 된다.

    예외: 아파트에 신청(REQUESTED) 이상인 유저는 공지글(category=notice)만
    읽을 수 있다. 이 경우 request._notice_only = True 가 설정된다."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)  # LoginRequiredMixin 이 로그인으로 보낸다
        request._notice_only = False
        if not permissions.has_community_access(request):
            # 미승인 유저(신청 전 포함) → 공지글만 열람 허용
            request._notice_only = True
        return super().dispatch(request, *args, **kwargs)


class BoardObjectAccessMixin(CommunityAccessRequiredMixin):
    """design 변경(2R-3): 상세·수정·삭제는 목록 게이트(승인된 소속이
    하나라도 있는가)만으로는 부족하다 — 그 글이 "내가 승인된 그 단지"
    글인지 객체 단위로 한 번 더 확인해야, 다른 단지 글 URL을 직접
    쳐서 들어오는 걸 막을 수 있다.

    예외: 미승인(notice_only) 유저는 공지글만 열람 가능."""

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        notice_only = getattr(self.request, "_notice_only", False)
        if notice_only:
            # 미승인 유저는 공지글만 열람 가능
            if obj.category != "notice":
                raise Http404("접근 권한이 없습니다.")
            return obj
        if not permissions.can_access_apartment_community(self.request.user, obj.apartment_id):
            raise Http404("접근 권한이 없습니다.")
        return obj


class BoardListView(CommunityAccessRequiredMixin, ListView):
    """게시글 목록 — 지금 활성 단지 글만(무조건 단지별) + 카테고리·정렬 필터
    + 키워드 검색(?kw=) + 페이지네이션.

    서비스 운영자가 활성 단지를 하나도 안 골랐으면(자기 소속이 없어도
    is_service_admin 이라 커뮤니티 자체엔 들어올 수 있다) 전체 단지를
    운영 목적으로 통으로 보여준다."""

    model = Board
    template_name = "boards/board_list.html"
    context_object_name = "boards"
    paginate_by = 10

    def get_queryset(self):
        self.notice_only = getattr(self.request, "_notice_only", False)
        if self.notice_only:
            # 미승인 유저: 소속 단지가 있으면 그 단지 공지, 없으면 전체 공지
            from apartments.scope import current_membership_for_chat
            membership = current_membership_for_chat(self.request)
            self.apartment = membership.apartment if membership else None
        else:
            self.apartment = scope.current_apartment(self.request)
        qs = Board.objects.select_related("author", "apartment")
        if self.notice_only:
            # 공지글만 필터 (소속 있으면 해당 단지, 없으면 전체)
            qs = qs.filter(category="notice", is_hidden=False)
            if self.apartment:
                qs = qs.filter(apartment=self.apartment)
        elif self.apartment:
            qs = qs.filter(apartment=self.apartment)
        elif not self.request.user.is_service_admin:
            qs = qs.none()
        # 비공개 글: 관리자에게는 보이고, 일반 사용자에게는 숨김
        self.is_manager = permissions.can_manage_apartment(
            self.request.user, self.apartment.id if self.apartment else None
        )
        if not self.is_manager:
            qs = qs.filter(is_hidden=False)
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
        # 공지는 항상 상단 고정 (category='notice' → is_notice=True → 내림차순 1위)
        from django.db.models import Case, When, BooleanField
        notice_order = Case(
            When(category="notice", then=True),
            default=False,
            output_field=BooleanField(),
        )
        if sort == "popular":
            qs = qs.annotate(is_notice=notice_order).order_by("-is_notice", "-like_count", "-created_at")
        elif sort == "views":
            qs = qs.annotate(is_notice=notice_order).order_by("-is_notice", "-read_count", "-created_at")
        else:
            qs = qs.annotate(is_notice=notice_order).order_by("-is_notice", "-created_at")
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        now = timezone.now()
        for board in ctx["boards"]:
            board.display_author = _display_author(board)
            board.is_new = (now - board.created_at).days < 7
        ctx["apartment"] = self.apartment
        ctx["region"] = self.request.GET.get("region", "")
        ctx["kw"] = self.request.GET.get("kw", "")
        ctx["category"] = self.request.GET.get("category", "")
        ctx["sort"] = self.request.GET.get("sort", "latest")
        ctx["region_choices"] = REGION_CHOICES
        ctx["category_choices"] = CATEGORY_CHOICES
        # 페이지 이동 시 검색어·필터가 유지되도록 page 를 뺀 쿼리스트링을 넘긴다.
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["qs_keep"] = params.urlencode()
        ctx["is_manager"] = self.is_manager
        ctx["notice_only"] = self.notice_only
        # 유저의 아파트명 (임시: region 기반)
        if self.request.user.is_authenticated:
            ctx["apartment_name"] = f"{self.request.user.get_region_display()} 에코빌"
        return ctx


class BoardDetailView(BoardObjectAccessMixin, DetailView):
    """게시글 상세 — 조회수 +1, 댓글 목록, 좋아요 상태."""

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
        ctx["comments"] = self.object.comments.select_related("author").all()
        ctx["comment_form"] = CommentForm()
        if self.request.user.is_authenticated:
            ctx["user_liked"] = BoardLike.objects.filter(
                board=self.object, user=self.request.user
            ).exists()
        else:
            ctx["user_liked"] = False
        is_manager = permissions.can_manage_apartment(self.request.user, board.apartment_id)
        ctx["can_delete"] = (
            board.author_id == self.request.user.id or is_manager
        )
        ctx["is_manager"] = is_manager
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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        apartment = scope.current_apartment(self.request)
        kwargs["is_manager"] = permissions.can_manage_apartment(
            self.request.user, apartment.id if apartment else None
        )
        return kwargs

    def form_valid(self, form):
        apartment = scope.current_apartment(self.request)
        if not apartment:
            messages.info(self.request, "글을 쓰려면 소속된 단지가 있어야 합니다.")
            return redirect("apartments:mine")
        # 일반 사용자가 공지 카테고리를 강제로 넘긴 경우 차단
        if form.cleaned_data.get("category") == "notice":
            if not permissions.can_manage_apartment(self.request.user, apartment.id):
                form.instance.category = "free"
        form.instance.author = self.request.user
        form.instance.apartment = apartment
        form.instance.region = apartment.region
        messages.success(self.request, "게시글이 등록되었습니다.")
        return super().form_valid(form)


class BoardUpdateView(BoardAuthorRequiredMixin, UpdateView):
    model = Board
    form_class = BoardForm
    template_name = "boards/board_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["is_manager"] = permissions.can_manage_apartment(
            self.request.user, self.get_object().apartment_id
        )
        return kwargs

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


class BoardHideView(BoardDeletePermissionMixin, View):
    """게시글 비공개/공개 토글 — POST 전용. 관리자(관리사무소/서비스운영자)만."""

    model = Board

    def post(self, request, pk):
        board = self.get_object()
        if board.is_hidden:
            # 공개로 전환
            board.is_hidden = False
            board.hidden_by = None
            board.hidden_at = None
            board.save(update_fields=["is_hidden", "hidden_by", "hidden_at"])
            messages.success(request, "게시글이 공개 처리되었습니다.")
        else:
            # 비공개로 전환
            board.is_hidden = True
            board.hidden_by = request.user
            board.hidden_at = timezone.now()
            board.save(update_fields=["is_hidden", "hidden_by", "hidden_at"])
            messages.success(request, "게시글이 비공개 처리되었습니다.")
        return redirect("boards:detail", pk=pk)


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
    """댓글 삭제 — POST 전용. 작성자 본인 또는 관리자."""

    def post(self, request, pk, comment_pk):
        comment = get_object_or_404(Comment, pk=comment_pk, board_id=pk)
        board = get_object_or_404(Board, pk=pk)
        is_manager = permissions.can_manage_apartment(request.user, board.apartment_id)
        if comment.author_id != request.user.id and not is_manager:
            messages.error(request, "본인 댓글만 삭제할 수 있습니다.")
        else:
            comment.delete()
            Board.objects.filter(pk=pk).update(comment_count=F("comment_count") - 1)
            messages.success(request, "댓글이 삭제되었습니다.")
        return redirect("boards:detail", pk=pk)
