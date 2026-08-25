"""커뮤니티 게시글 폼.

rag/forms.py 의 DocumentUploadForm 검증 관례(확장자 화이트리스트 +
용량 제한)를 따릅니다. 차이점:

- 첨부는 **선택**이고 색인되지 않습니다 (다운로드용). 그래서 이미지·
  압축 등 폭넓게 허용하되, 실행 파일 계열은 막습니다.
- RAG 색인 대상이 아니므로 평문 추출도 하지 않습니다. 게시글을 RAG
  근거로 편입하는 결정(models.py 상단 주석)은 아직 보류 상태이며,
  편입하더라도 색인 대상은 attachment 가 아니라 content 입니다.
"""
from pathlib import Path

from django import forms

from .models import Board

ALLOWED_ATTACHMENT_EXTENSIONS = {
    ".txt", ".md", ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".zip", ".docx", ".xlsx", ".pptx", ".hwp", ".hwpx",
}
MAX_ATTACHMENT_MB = 10


class BoardForm(forms.ModelForm):
    """게시글 작성/수정 공용 폼."""

    class Meta:
        model = Board
        fields = ["title", "region", "content", "attachment"]
        labels = {
            "title": "제목",
            "region": "지역",
            "content": "내용",
            "attachment": "첨부파일 (선택)",
        }
        help_texts = {
            "region": "어느 지역 기준의 경험·질문인지 골라 주세요.",
        }

    def clean_attachment(self):
        uploaded = self.cleaned_data.get("attachment")
        # 첨부는 선택 사항 — 수정 화면에서 기존 파일 유지(FieldFile)면 통과.
        if not uploaded or not hasattr(uploaded, "content_type"):
            return uploaded

        suffix = Path(uploaded.name).suffix.lower()
        if suffix not in ALLOWED_ATTACHMENT_EXTENSIONS:
            raise forms.ValidationError(
                f"허용되지 않는 형식입니다. ({', '.join(sorted(ALLOWED_ATTACHMENT_EXTENSIONS))} 만 가능)"
            )
        if uploaded.size > MAX_ATTACHMENT_MB * 1024 * 1024:
            raise forms.ValidationError(
                f"첨부파일은 {MAX_ATTACHMENT_MB}MB 이하만 올릴 수 있습니다."
            )
        return uploaded
