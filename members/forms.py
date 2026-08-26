"""회원 폼. 강사 자료 members/forms.py 의 골격에 region 을 추가했습니다."""
from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from apartments.models import Apartment, Membership

from .models import Member


class SignUpForm(UserCreationForm):
    """회원가입. UserCreationForm 이 비밀번호 일치·강도 검사와 해싱을 맡습니다.

    design 변경(2R-2): 입주민/관리사무소 관리자 역할과 단지를 가입 화면에서
    같이 받아 가입과 동시에 Membership 신청까지 접수한다.
    """

    member_type = forms.ChoiceField(
        label="가입 유형", choices=Membership.Role.choices,
        initial=Membership.Role.RESIDENT, widget=forms.RadioSelect,
    )
    apartment = forms.ModelChoiceField(
        label="단지", queryset=Apartment.objects.all(), required=False,
        help_text="지역에 등록된 단지가 없으면 비워두고 가입한 뒤 나중에 신청할 수 있습니다.",
    )
    decision_note = forms.CharField(
        label="확인 메모(선택)", widget=forms.Textarea, required=False,
    )

    class Meta:
        model = Member
        fields = ["username", "display_name", "nickname", "email", "region"]
        labels = {
            "username": "아이디",
            "display_name": "이름",
            "nickname": "닉네임",
            "email": "이메일",
            "region": "거주 지역",
        }

    def clean(self):
        cleaned = super().clean()
        region = cleaned.get("region")
        apartment = cleaned.get("apartment")
        if apartment and region and apartment.region != region:
            self.add_error("apartment", "선택한 지역과 단지의 지역이 다릅니다.")
        elif region and not apartment and Apartment.objects.filter(region=region).exists():
            self.add_error("apartment", "이 지역에 등록된 단지를 선택해 주세요.")
        return cleaned


class LoginForm(AuthenticationForm):
    """로그인. AuthenticationForm 이 인증·비활성 계정 검사를 맡습니다."""

    username = forms.CharField(label="아이디")
    password = forms.CharField(label="비밀번호", widget=forms.PasswordInput)


class ProfileForm(forms.ModelForm):
    """프로필 수정. 거주 지역(region)을 바꾸면 챗봇 기본 지역이 바뀝니다."""

    class Meta:
        model = Member
        fields = ["display_name", "nickname", "email", "region", "phone"]
        labels = {
            "display_name": "이름",
            "nickname": "닉네임",
            "email": "이메일",
            "region": "거주 지역",
            "phone": "전화번호",
        }
