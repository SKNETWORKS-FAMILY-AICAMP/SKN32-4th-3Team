"""boards 앱 end-to-end 검증. python verify_boards.py 로 실행.

주의(HANDOFF): 403 검증 시 traceback 로그가 출력되지만 실패가 아닙니다.
"""
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from boards.models import Board
from members.models import Member

PASS = []


def ok(name, cond, detail=""):
    assert cond, f"FAIL: {name} {detail}"
    PASS.append(name)
    print(f"  ✔ {name}")


# ── 준비: 회원 2명 (부산 A, 서울 B) ──
Board.objects.all().delete()
Member.objects.filter(username__in=["boardA", "boardB"]).delete()
a = Member.objects.create_user(
    username="boardA", password="pw-boardA-1", email="a@t.co",
    display_name="부산주민", region="busan_namgu",
)
b = Member.objects.create_user(
    username="boardB", password="pw-boardB-1", email="b@t.co",
    display_name="서울주민", region="seoul",
)

anon, ca, cb = Client(), Client(), Client()
ca.force_login(a)
cb.force_login(b)

print("[1] 익명 접근")
r = anon.get(reverse("boards:list"))
ok("목록은 비로그인 공개(200)", r.status_code == 200)
r = anon.get(reverse("boards:create"))
ok("글쓰기는 로그인 리다이렉트(302→login)", r.status_code == 302 and "login" in r["Location"])

print("[2] 작성 (A)")
r = ca.get(reverse("boards:create"))
ok("작성 폼에 프로필 지역이 기본 선택", r.status_code == 200
   and 'value="busan_namgu" selected' in r.content.decode())
r = ca.post(reverse("boards:create"), {
    "title": "남구 스티로폼 배출 요일", "region": "busan_namgu",
    "content": "스티로폼은 무슨 요일에 내놔야 하나요?",
})
board = Board.objects.get(title="남구 스티로폼 배출 요일")
ok("POST → 상세로 리다이렉트", r.status_code == 302
   and r["Location"] == reverse("boards:detail", args=[board.pk]))
ok("author 자동 지정", board.author_id == a.id)

print("[3] 상세 · 조회수")
r = anon.get(reverse("boards:detail", args=[board.pk]))
ok("상세 200 + 본문 렌더링", r.status_code == 200 and "스티로폼" in r.content.decode())
anon.get(reverse("boards:detail", args=[board.pk]))
board.refresh_from_db()
ok("조회수 2회 → read_count=2 (F 표현식)", board.read_count == 2, f"got {board.read_count}")
html = r.content.decode()
ok("타인 화면에는 수정·삭제 버튼 없음", "수정" not in html.split("docs-wrap")[0])

print("[4] 필터 · 검색")
cb.post(reverse("boards:create"), {
    "title": "서울 폐건전지 문의", "region": "seoul", "content": "폐건전지 수거함 위치"})
r = anon.get(reverse("boards:list"), {"region": "busan_namgu"})
html = r.content.decode()
ok("지역 필터: 부산 글만", "스티로폼" in html and "폐건전지" not in html)
r = anon.get(reverse("boards:list"), {"kw": "건전지"})
html = r.content.decode()
ok("키워드 검색: 매칭 글만", "폐건전지" in html and "스티로폼" not in html)

print("[5] 권한 (403 traceback 로그는 정상)")
r = cb.get(reverse("boards:update", args=[board.pk]))
ok("타인 수정 GET → 403", r.status_code == 403)
r = cb.post(reverse("boards:delete", args=[board.pk]))
ok("타인 삭제 POST → 403", r.status_code == 403)
ok("403 이후에도 글 존재", Board.objects.filter(pk=board.pk).exists())
r = anon.get(reverse("boards:delete", args=[board.pk]))
ok("삭제는 POST 전용 (GET 405/302)", r.status_code in (302, 405))

print("[6] 수정 (A 본인)")
r = ca.post(reverse("boards:update", args=[board.pk]), {
    "title": "남구 스티로폼 배출 요일", "region": "busan_namgu",
    "content": "해결: 남구는 목요일입니다.",
})
board.refresh_from_db()
ok("본인 수정 반영", r.status_code == 302 and "목요일" in board.content)

print("[7] 첨부 검증")
r = ca.post(reverse("boards:create"), {
    "title": "실행파일", "region": "common", "content": "x",
    "attachment": SimpleUploadedFile("virus.exe", b"MZ", content_type="application/x-msdownload"),
})
ok(".exe 첨부 거부 + 레코드 미생성", r.status_code == 200
   and "허용되지 않는 형식" in r.content.decode()
   and not Board.objects.filter(title="실행파일").exists())
r = ca.post(reverse("boards:create"), {
    "title": "공지문 첨부", "region": "busan_namgu", "content": "관리사무소 공지 첨부합니다",
    "attachment": SimpleUploadedFile("notice.txt", "분리배출 공지".encode(), content_type="text/plain"),
})
with_file = Board.objects.get(title="공지문 첨부")
ok("txt 첨부 등록 + 목록에 📎 표시", with_file.attachment
   and "📎" in anon.get(reverse("boards:list")).content.decode())

print("[8] 페이지네이션 (10개/쪽)")
for i in range(11):
    Board.objects.create(author=a, title=f"채움 {i}", content="p", region="jeju")
r = anon.get(reverse("boards:list"))
html = r.content.decode()
ok("1쪽 10건 + 다음 링크", html.count("<tr>") == 11 and "다음 ›" in html)  # thead 1 + 10
r = anon.get(reverse("boards:list"), {"region": "jeju", "page": 2})
html = r.content.decode()
ok("2쪽 200 + 이전 링크에 필터 유지", r.status_code == 200
   and '?region=jeju&amp;page=1' in html and "‹ 이전" in html)

print("[9] 삭제 (A 본인)")
r = ca.post(reverse("boards:delete", args=[board.pk]))
ok("본인 삭제 → 목록 리다이렉트 + 소멸", r.status_code == 302
   and not Board.objects.filter(pk=board.pk).exists())

print(f"\n전체 {len(PASS)}건 통과")
