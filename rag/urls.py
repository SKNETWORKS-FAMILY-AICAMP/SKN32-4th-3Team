"""RAG 앱 URL. 강사 자료 rag/urls.py 의 구성을 따릅니다."""
from django.urls import path

from . import views

app_name = "rag"

urlpatterns = [
    # 내 문서 + 공용 문서 목록
    path("documents/", views.DocumentListView.as_view(), name="documents"),
    # 사용자 문서 업로드 (3차에는 없던 화면)
    path("documents/upload/", views.DocumentUploadView.as_view(), name="upload"),
    # 근거 원문 보기. 답변의 "출처 보기" 링크가 여기로 옵니다.
    path("documents/<int:pk>/", views.DocumentDetailView.as_view(), name="document"),
    path("documents/<int:pk>/delete/", views.DocumentDeleteView.as_view(), name="document_delete"),
    # 인덱스 상태 조회. 3차 GET /api/rag/status 대응
    path("status/", views.IndexStatusView.as_view(), name="status"),
    # 인덱스 재구축. 3차 POST /api/rag/rebuild 대응
    path("rebuild/", views.IndexRebuildView.as_view(), name="rebuild"),
    # 검색만 확인(디버그). 3차 POST /api/rag/search 대응
    path("search/", views.RagSearchView.as_view(), name="search"),
]
