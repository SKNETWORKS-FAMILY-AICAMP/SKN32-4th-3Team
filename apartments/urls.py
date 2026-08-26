"""단지 신청·가입·검토 URL."""
from django.urls import path

from . import views

app_name = "apartments"

urlpatterns = [
    path("search/", views.ApartmentSearchView.as_view(), name="search"),
    path("join/", views.ApartmentJoinView.as_view(), name="join"),
    path("manager/apply/", views.ManagerApplyView.as_view(), name="manager_apply"),
    path("mine/", views.MyApartmentView.as_view(), name="mine"),
    path("manager-dashboard/", views.ManagerDashboardView.as_view(), name="manager_dashboard"),
    path("switch/<int:pk>/", views.ApartmentSwitchView.as_view(), name="switch"),
    path("membership/queue/", views.MembershipQueueView.as_view(), name="membership_queue"),
    path("membership/residents/", views.ResidentApprovalQueueView.as_view(), name="resident_queue"),
    path("membership/<int:pk>/decide/", views.MembershipDecisionView.as_view(), name="membership_decide"),
    path("membership/<int:pk>/leave/", views.MembershipLeaveView.as_view(), name="membership_leave"),
    path("membership/residents/roster/", views.ResidentRosterView.as_view(), name="resident_roster"),
    path("membership/managers/roster/", views.ManagerRosterView.as_view(), name="manager_roster"),
    path("office/", views.ApartmentOfficeInfoView.as_view(), name="office"),
    path("rules/", views.ApartmentRuleListView.as_view(), name="rule_list"),
    # design 변경(2R-3): 등록 즉시 반영되므로(관리자 전용) 검토 큐/결정
    # 라우트는 더 이상 필요 없다.
    path("rules/new/", views.ApartmentRuleCreateView.as_view(), name="rule_create"),
    path("rules/<int:pk>/delete/", views.ApartmentRuleDeleteView.as_view(), name="rule_delete"),
]
