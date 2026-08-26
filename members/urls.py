"""회원 URL. 강사 자료 members/urls.py 구성을 따릅니다."""
from django.urls import path

from . import views

app_name = "members"

urlpatterns = [
    path("signup/", views.SignUpView.as_view(), name="signup"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("mypage/", views.MyPageView.as_view(), name="mypage"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("profile/edit/", views.ProfileUpdateView.as_view(), name="profile_edit"),
    path("withdraw/", views.WithdrawView.as_view(), name="withdraw"),
    path("guide/<str:key>/", views.GuideView.as_view(), name="guide"),
]

# 추가: 가이드 화면
