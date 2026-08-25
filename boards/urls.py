from django.urls import path

from . import views

app_name = "boards"

urlpatterns = [
    path("", views.BoardListView.as_view(), name="list"),
    path("create/", views.BoardCreateView.as_view(), name="create"),
    path("<int:pk>/", views.BoardDetailView.as_view(), name="detail"),
    path("<int:pk>/update/", views.BoardUpdateView.as_view(), name="update"),
    path("<int:pk>/delete/", views.BoardDeleteView.as_view(), name="delete"),
    path("<int:pk>/like/", views.BoardLikeView.as_view(), name="like"),
    path("<int:pk>/comment/", views.CommentCreateView.as_view(), name="comment_create"),
    path("<int:pk>/comment/<int:comment_pk>/delete/", views.CommentDeleteView.as_view(), name="comment_delete"),
]
