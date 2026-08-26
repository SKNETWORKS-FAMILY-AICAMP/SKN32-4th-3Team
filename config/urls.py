"""프로젝트 최상위 URL 라우팅."""
import os

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import TemplateView
from django.views.static import serve as static_serve

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
elif os.getenv("DJANGO_SERVE_MEDIA", "True").strip().lower() == "true":
    # 운영에서의 업로드 파일 서빙.
    #
    # django.conf.urls.static.static() 은 DEBUG=False 이면 **빈 리스트를
    # 반환**합니다. 그래서 위 블록만 두면 배포 직후 프로필 사진 · 게시글
    # 첨부 · 단지 규정 파일이 전부 404 가 됩니다.
    #
    # static_serve 는 경로 탈출을 막아 주지만 파일을 파이썬으로 읽어
    # 내보내므로 웹서버가 직접 주는 것보다 느립니다. 이 프로젝트의 업로드는
    # 사진 · 소용량 PDF 수준이라 실사용에 문제가 없는 선택입니다.
    #
    # 더 빠르게 하려면 MEDIA_ROOT 를 홈 디렉터리 밖으로 옮기고 Caddy 가
    # file_server 로 직접 서빙하게 한 뒤 DJANGO_SERVE_MEDIA=False 로
    # 끄십시오. 자세한 절차는 docs/deploy.md 를 보십시오.
    urlpatterns += [
        re_path(
            r"^media/(?P<path>.*)$",
            static_serve,
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
