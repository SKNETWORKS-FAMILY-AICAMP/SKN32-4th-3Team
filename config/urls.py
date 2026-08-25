"""프로젝트 최상위 URL 라우팅."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from members.views import GuideView, HomeView

urlpatterns = [
    path("admin/", admin.site.urls),
    # 랜딩 · 가이드 · 서비스 소개 (비로그인 공개)
    path("", HomeView.as_view(), name="home"),
    path("guide/<slug:key>/", GuideView.as_view(), name="guide"),
    path("service/", TemplateView.as_view(template_name="service.html"), name="service"),
    # 회원가입 · 로그인 · 프로필(거주 지역 설정)
    path("members/", include("members.urls")),
    # 챗봇 화면 + 대화방 AJAX 엔드포인트
    path("chat/", include("chat.urls")),
    # 커뮤니티
    path("boards/", include("boards.urls")),
    # 문서 목록 · 근거 원문 보기 · 인덱스 상태
    path("rag/", include("rag.urls")),
    # 관리자 통계
    path("dashboard/", include("dashboard.urls")),
    # 아파트 단지 검색 · 가입 · 관리자 신청/승인 · 규정 제안/검토
    path("apartments/", include("apartments.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
