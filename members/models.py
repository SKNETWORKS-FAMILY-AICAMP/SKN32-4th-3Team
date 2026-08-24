"""회원 모델.

강사 자료 members/models.py 의 Member(AbstractUser) 를 그대로 쓰고,
Ecobot 전용으로 region(거주 지역) 한 필드만 추가했습니다.

3차 app/models.py 의 User 와의 대응:
    User.email            → Member.email          (unique 유지)
    User.hashed_password  → Member.password       (AbstractUser 제공)
    User.display_name     → Member.display_name
    User.is_active        → Member.is_active      (AbstractUser 제공)
    User.created_at       → Member.date_joined    (AbstractUser 제공)
    User.updated_at       → Member.updated_at

region 을 추가하는 이유:
    3차에서는 지역 선택이 화면 드롭다운 값이라 매 요청마다 실려 왔고,
    새로고침하면 기본값(서울)으로 리셋되는 문제가 있었습니다.
    회원 프로필에 저장하면 로그인만으로 거주 지역이 적용되고,
    ChatSession.region 의 기본값으로도 쓸 수 있습니다.
"""
from django.contrib.auth.models import AbstractUser
from django.db import models


# 지역 코드는 3차 rag_service._extract_region() 의 REGION_MAP 값과
# 반드시 일치해야 합니다. 여기서 코드를 바꾸면 지역 필터가 조용히
# 아무것도 못 찾습니다.
REGION_CHOICES = [
    ("common", "전국 공통"),
    ("seoul", "서울"),
    ("cheonan", "천안"),
    ("busan_namgu", "부산 남구"),
    ("incheon_michuhol", "인천 미추홀구"),
    ("sejong", "세종"),
    ("jeju", "제주"),
]


class Member(AbstractUser):
    """서비스 회원. AUTH_USER_MODEL 로 지정됩니다."""

    GENDER_CHOICES = [("M", "남성"), ("F", "여성"), ("N", "선택하지 않음")]
    SIGN_TYPE_CHOICES = [("direct", "직접 가입"), ("social", "소셜 가입")]

    username = models.CharField("회원 아이디", max_length=50, unique=True)
    display_name = models.CharField("회원 이름", max_length=30)
    email = models.EmailField("이메일", unique=True)
    gender = models.CharField("성별", max_length=1, choices=GENDER_CHOICES, default="N")
    age = models.PositiveIntegerField("나이", null=True, blank=True)
    phone = models.CharField("전화번호", max_length=20, blank=True)
    sign_type = models.CharField("가입 방식", max_length=10, choices=SIGN_TYPE_CHOICES, default="direct")
    photo = models.ImageField("프로필 사진", upload_to="member_photos/%Y/%m/", blank=True, null=True)

    # ── Ecobot 추가 필드 ──
    region = models.CharField(
        "거주 지역",
        max_length=50,
        choices=REGION_CHOICES,
        default="seoul",
        help_text="챗봇이 기본으로 적용할 지역입니다.",
    )

    updated_at = models.DateTimeField("마지막 수정일", auto_now=True)

    # createsuperuser 가 추가로 물어볼(또는 --noinput 시
    # DJANGO_SUPERUSER_* 환경변수로 받을) 필수 필드.
    # display_name 이 빠지면 관리자 계정이 빈 이름으로 만들어집니다.
    REQUIRED_FIELDS = ["email", "display_name"]

    class Meta:
        db_table = "members"
        verbose_name = "회원"
        verbose_name_plural = "회원"

    def __str__(self):
        return f"{self.display_name}({self.username})"

    @property
    def is_service_admin(self) -> bool:
        """관리자 대시보드 접근 권한.

        3차에서는 `"admin" in user.email.lower()` 로 판정했습니다.
        이메일 문자열에 의존하는 방식은 admin@ 이 아닌 관리자를 못 잡고
        반대로 badmin@example.com 같은 주소를 관리자로 오인합니다.
        Django 가 이미 제공하는 is_staff 를 쓰면 관리자 페이지에서
        체크박스로 관리할 수 있습니다.
        """
        return self.is_staff or self.is_superuser
