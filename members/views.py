"""회원 View. 강사 자료 8/14 CBV 버전의 관례를 따릅니다.

3차 app/routers/api.py 인증 엔드포인트 대응:
    POST /api/auth/register → SignUpView
    POST /api/auth/login    → LoginView (django.contrib.auth.views 상속)
    POST /api/auth/logout   → LogoutView
    GET  /api/me            → 불필요 (템플릿에서 request.user)

■ 3차 대비 사라진 것
    app/core/security.py 전체 (JWT 생성·검증, bcrypt 해싱)
    _set_session_cookie() / get_current_user() / COOKIE_NAME
    → django.contrib.auth 의 login()/logout()/LoginRequiredMixin 이
      같은 일을 합니다. requirements 에서 python-jose, passlib 도 제거.

Home/SignUp/Login 은 동작 상태이고, Profile/Withdraw 는 강사 자료
CBV 를 붙여넣는 자리로 남겨뒀습니다.
"""
from django.contrib.auth import login, logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import redirect, render
from django.views import View

from .forms import LoginForm, ProfileForm, SignUpForm


class HomeView(View):
    """랜딩 화면. 로그인 여부와 무관하게 접근 가능합니다.

    로그인 상태에서는 커뮤니티 최근 게시글을 미리보기로 보여줍니다.
    """

    def get(self, request):
        from boards.models import Board

        recent_boards = Board.objects.select_related("author").order_by("-created_at")[:5]
        return render(request, "home.html", {"recent_boards": recent_boards})


class GuideView(View):
    """분리배출·음식물·에너지·지역별 가이드. 3차 openGuide() 의 서버 렌더링판.

    3차: JS 가 GUIDE_DATA[key] 를 innerHTML 로 조립 → 4차: 같은 데이터를
    guide.html 템플릿이 그립니다. 마지막 섹션 홀수 처리(full-width)도
    템플릿에서 동일하게 재현합니다.
    """

    def get(self, request, key):
        from django.http import Http404

        from .guides import GUIDE_DATA

        data = GUIDE_DATA.get(key)
        if data is None:
            raise Http404("가이드를 찾을 수 없습니다.")
        return render(
            request, "guide.html",
            {"guide": data, "odd": len(data["sections"]) % 2 == 1},
        )


class SignUpView(View):
    def _apartments_context(self):
        from apartments.models import Apartment

        return {"apartments": Apartment.objects.all()}

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("chat:room")
        return render(
            request, "members/signup.html",
            {"form": SignUpForm(), **self._apartments_context()},
        )

    def post(self, request):
        if request.user.is_authenticated:
            return redirect("chat:room")
        form = SignUpForm(request.POST)
        if not form.is_valid():
            return render(
                request, "members/signup.html",
                {"form": form, **self._apartments_context()}, status=400,
            )

        from django.contrib import messages

        from apartments.models import Membership
        from apartments.services import apply_for_membership

        with transaction.atomic():
            member = form.save()
            apartment = form.cleaned_data.get("apartment")
            if apartment:
                apply_for_membership(
                    member, apartment, form.cleaned_data["member_type"],
                    form.cleaned_data.get("decision_note", ""),
                )

        login(request, member)
        if form.cleaned_data.get("apartment"):
            role_label = dict(Membership.Role.choices)[form.cleaned_data["member_type"]]
            messages.success(
                request,
                f"가입이 완료됐습니다. '{apartment.name}' {role_label} 신청이 접수되어 승인을 기다리는 중이에요 "
                "— 승인 전에도 챗봇에서 단지 규정을 바로 물어볼 수 있어요.",
            )
        return redirect("chat:room")


class LoginView(auth_views.LoginView):
    """Django 기본 LoginView 상속 — next 파라미터·에러 처리를 공짜로 얻습니다."""

    template_name = "members/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class LogoutView(LoginRequiredMixin, View):
    def post(self, request):
        logout(request)
        return redirect("home")


class ProfileView(LoginRequiredMixin, View):
    """프로필 조회."""

    def get(self, request):
        return render(request, "members/profile.html")


class ProfileUpdateView(LoginRequiredMixin, View):
    """프로필 수정. 거주 지역(region)을 바꾸면 챗봇 기본 지역이 바뀝니다."""

    def get(self, request):
        return render(
            request, "members/profile_form.html",
            {"form": ProfileForm(instance=request.user)},
        )

    def post(self, request):
        form = ProfileForm(request.POST, instance=request.user)
        if not form.is_valid():
            return render(
                request, "members/profile_form.html", {"form": form}, status=400
            )
        form.save()
        return redirect("members:profile")


class WithdrawView(LoginRequiredMixin, View):
    """회원 탈퇴.

    행을 지우지 않고 is_active=False 로 비활성화합니다. FK CASCADE 로
    대화·문서가 함께 사라지는 것을 막고, ChatLog 통계도 보존됩니다.
    비활성 계정은 Django 인증이 로그인 자체를 거부합니다.
    """

    def get(self, request):
        return render(request, "members/withdraw.html")

    def post(self, request):
        user = request.user
        logout(request)
        user.is_active = False
        user.save(update_fields=["is_active"])
        return redirect("home")
