"""챗봇 URL.

3차 엔드포인트와의 대응:
    POST /api/chat               → POST chat:ask
    GET  /api/chat/sessions      → GET  chat:sessions
    GET  /api/popular-questions  → GET  chat:popular
    (신규) 대화방 삭제            → POST chat:session_delete
        3차의 deleteSession() 은 화면에서만 지우고 서버는 안 지워서
        새로고침하면 지운 대화가 되살아났습니다. FK 모델이 생겼으니
        서버 삭제를 추가합니다.

"새 대화" 전용 엔드포인트는 두지 않습니다 — 3차처럼 클라이언트가 빈
대화 상태를 들고 있다가, 첫 질문(ask, session_id=null)에서 서버가
대화방을 만들어 pk 를 돌려줍니다. 빈 대화방 행이 쌓이지 않습니다.
"""
from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("", views.ChatRoomView.as_view(), name="room"),
    path("ask/", views.ChatAskView.as_view(), name="ask"),
    path("sessions/", views.ChatSessionListView.as_view(), name="sessions"),
    path("sessions/<int:pk>/delete/", views.ChatSessionDeleteView.as_view(), name="session_delete"),
    path("popular/", views.PopularQuestionView.as_view(), name="popular"),
    path("feedback/<int:pk>/", views.ChatFeedbackView.as_view(), name="feedback"),
]
