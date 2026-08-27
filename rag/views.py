"""RAG 문서 조회 · 업로드 · 인덱스 관리 View.

실행 로직(업로드 · 재빌드 · 진단 검색 · 삭제)은 구현 완료 상태이고,
화면 렌더링(목록 · 상세)은 프론트 이식 단계에서 채웁니다.
"""
import json
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apartments import permissions as apt_permissions
from apartments import scope as apt_scope
from dashboard.views import AdminRequiredMixin

from django.core.exceptions import ValidationError

from . import service
from .forms import DocumentUploadForm, validate_uploaded_file
from .models import Document, SourceType


def _json(payload, status=200):
    return JsonResponse(
        payload, status=status, safe=False, json_dumps_params={"ensure_ascii": False}
    )


class DocumentListView(LoginRequiredMixin, View):
    """문서 목록 화면 — "내가 올린 문서 + 공용 문서 + (관리자면) 내 단지 규정".

    3차 GET /api/admin/documents 는 관리자 전용이었지만, 업로드 기능이
    생기면 일반 사용자도 자기 문서를 봐야 합니다. 관리자용 청크 통계는
    dashboard:documents 로 분리했습니다.

    4차 추가분: 관리사무소 관리자가 rag:upload 로 올린 단지 규정
    문서(owner=None, source_type=apartment)는 대시보드(서비스 관리자
    전용, AdminRequiredMixin)에서도 안 보이고 여기서도 원래 안 보여서,
    올린 사람 본인이 확인·삭제할 방법이 아예 없었다. 관리하는 단지의
    문서만 노출한다(다른 단지 규정까지 보이면 안 되므로 managed_
    apartment_ids 로 좁힌다 — 서비스 관리자는 이 함수가 전체 단지를
    돌려주므로 자연히 전체를 본다).
    """

    def get(self, request):
        from django.db.models import Q

        managed_ids = set(apt_permissions.managed_apartment_ids(request.user))
        docs = Document.objects.filter(
            Q(owner=request.user)
            | Q(source_type__in=[SourceType.LAW, SourceType.GUIDE])
            | Q(source_type=SourceType.APARTMENT, apartment_id__in=managed_ids)
        ).order_by("source_type", "title")

        is_service_admin = request.user.is_service_admin
        deletable_ids = set()
        for doc in docs:
            if doc.source_type == SourceType.MANUAL:
                if doc.owner_id == request.user.pk or is_service_admin:
                    deletable_ids.add(doc.pk)
            elif doc.source_type == SourceType.APARTMENT:
                # 위 쿼리에서 이미 managed_ids 로 걸러졌으므로 보이는
                # 단지 규정은 항상 삭제 가능하다.
                deletable_ids.add(doc.pk)
            elif is_service_admin:
                deletable_ids.add(doc.pk)

        return render(
            request, "rag/document_list.html",
            {"documents": docs, "deletable_ids": deletable_ids},
        )


class DocumentUploadView(LoginRequiredMixin, View):
    """사용자 문서 업로드 + 색인.

    ⚠️ 3차 POST /api/admin/upload 의 버그를 고친 자리입니다.

    3차 동작:  파일을 data/guide/ 폴더에 저장 → rebuild_index()
    문제:      rebuild_index() 는 RAG_SOURCE=db 일 때 documents 테이블을
               읽습니다. 방금 올린 파일은 폴더에만 있으므로 **색인되지
               않습니다.** 응답의 indexed_chunks 는 기존 문서의 청크
               수라서 성공처럼 보이고, seed_docs 를 다시 돌릴 때까지
               조용히 무시됩니다.
    4차 동작:  ① source_file 저장 → ② 평문 추출해 content_text 에 저장
               (Document 레코드 생성) → ③ rebuild_index() — 테이블을
               읽으니 반드시 포함됩니다.

    평문 추출은 service._read_file() 을 재사용합니다 (pdf/txt/md +
    cp949 폴백). 추출 결과가 비어 있으면(스캔 PDF 등) 색인할 수 없으므로
    레코드를 만들지 않고 에러를 돌려줍니다.
    """

    def _extra_context(self, request, apartment, is_manager):
        """관리사무소 관리자는 소속 단지가 컨텍스트로 이미 잡혀 있지만,
        최종 서버 관리자(is_service_admin)는 애초에 단지 소속을 가질 수
        없다(apartments/services.py:ServiceAdminCannotApplyError) — 그래서
        업로드마다 국가/지역별/특정 아파트 중 범위를 직접 고르게 하고,
        아파트를 고를 수 있도록 전체 목록을 같이 내려준다."""
        is_service_admin = request.user.is_service_admin
        context = {"is_manager": is_manager, "apartment": apartment, "is_service_admin": is_service_admin}
        if is_service_admin:
            from apartments.models import Apartment

            context["apartments_list"] = Apartment.objects.order_by("region", "name")
        return context

    def get(self, request):
        # design 변경: 관리사무소 관리자는 지역을 직접 고르지 않는다 —
        # 업로드한 문서가 자동으로 "그 단지" 문서가 되어 같은 단지
        # 입주민의 챗봇 답변에 바로 반영된다(아래 post() 참고).
        apartment = apt_scope.current_apartment(request)
        is_manager = bool(apartment) and apt_permissions.can_manage_apartment(request.user, apartment.pk)
        return render(
            request, "rag/document_upload.html",
            {"form": DocumentUploadForm(), **self._extra_context(request, apartment, is_manager)},
        )

    def post(self, request):
        # 4차 추가분: 여러 파일을 한 번에 올릴 수 있다 — source_file 은
        # 더 이상 ModelForm 필드가 아니라 request.FILES.getlist() 로 직접
        # 받는다(rag/forms.py::DocumentUploadForm 주석 참고). 범위(scope)
        # 는 파일마다 다시 고르게 하지 않고 배치 전체에 한 번만 적용한다
        # — 여러 개를 올릴 때마다 같은 값을 반복 입력하게 하면 오히려
        # 실수를 유발한다(서비스 관리자가 매번 "국가 전체" 기본값을 놓쳐
        # 결국 다 가이드/전국공통으로 들어가던 문제가 그 예다 — 그 기본
        # 선택 자체는 템플릿에서 없앴다).
        apartment = apt_scope.current_apartment(request)
        is_manager = bool(apartment) and apt_permissions.can_manage_apartment(request.user, apartment.pk)
        extra_context = self._extra_context(request, apartment, is_manager)

        form = DocumentUploadForm(request.POST)
        uploaded_files = request.FILES.getlist("source_file")
        if not uploaded_files:
            form.add_error(None, "파일을 선택해 주세요.")

        valid_files = []
        skipped = []
        for uploaded in uploaded_files:
            try:
                validate_uploaded_file(uploaded)
            except ValidationError as exc:
                skipped.append(f"{uploaded.name}: {'; '.join(exc.messages)}")
            else:
                valid_files.append(uploaded)

        if not form.is_valid() or (uploaded_files and not valid_files):
            for msg in skipped:
                form.add_error(None, msg)
            return render(
                request, "rag/document_upload.html", {"form": form, **extra_context}, status=400,
            )

        title_prefix = (form.cleaned_data.get("title") or "").strip()
        region_value = form.cleaned_data.get("region")

        # 범위 계산 — scope_kwargs 는 이 배치의 모든 Document 에 그대로
        # 적용된다.
        if extra_context["is_service_admin"]:
            # design 변경(4차 추가): 관리사무소 관리자는 업로드 = 자기
            # 단지 규정으로 고정할 수 있지만, 서비스 총괄 관리자는 국가
            # 전체(법령류)일 수도, 특정 지역 가이드일 수도, 특정 단지
            # 규정일 수도 있다 — 매번 명시적으로 범위를 고르게 한다.
            # (템플릿에서 "국가 전체" 기본 선택을 없애고 required 를
            # 걸었으므로, 직접 고르지 않으면 여기 도달하지 않는다.)
            upload_scope = request.POST.get("upload_scope", "")
            if upload_scope == "apartment":
                from apartments.models import Apartment

                target_apartment = Apartment.objects.filter(
                    pk=request.POST.get("target_apartment")
                ).first()
                if not target_apartment:
                    form.add_error(None, "문서를 등록할 아파트를 선택해 주세요.")
                    return render(
                        request, "rag/document_upload.html", {"form": form, **extra_context}, status=400,
                    )
                scope_kwargs = dict(
                    owner=None, source_type=SourceType.APARTMENT, apartment=target_apartment,
                    region=target_apartment.region, status=Document.Status.APPROVED,
                )
            elif upload_scope == "region":
                if not region_value:
                    form.add_error("region", "지역을 선택해 주세요.")
                    return render(
                        request, "rag/document_upload.html", {"form": form, **extra_context}, status=400,
                    )
                scope_kwargs = dict(owner=None, source_type=SourceType.GUIDE, region=region_value)
            elif upload_scope == "national":
                # 4차 추가분: "국가 전체"도 법령/가이드 둘 다 있을 수
                # 있다 — 예전엔 항상 GUIDE로만 저장돼서, 실제 법령
                # 원문을 올려도 시행일 안내(law_notice) 기능이 절대
                # 동작하지 않았다. national_doc_type 으로 고른다.
                if request.POST.get("national_doc_type") == "law":
                    scope_kwargs = dict(owner=None, source_type=SourceType.LAW, region=None)
                else:
                    scope_kwargs = dict(owner=None, source_type=SourceType.GUIDE, region=None)
            else:
                form.add_error(None, "문서 범위를 선택해 주세요.")
                return render(
                    request, "rag/document_upload.html", {"form": form, **extra_context}, status=400,
                )
        elif is_manager:
            # 관리사무소 관리자 업로드는 지역+아파트 메타데이터가 자동으로
            # 붙는다. rag.service.search() 의 기존 apartment_id
            # fail-closed 필터가 격리를 그대로 처리하므로 태깅만 하면 된다.
            scope_kwargs = dict(
                owner=None, source_type=SourceType.APARTMENT, apartment=apartment,
                region=apartment.region, status=Document.Status.APPROVED,
            )
        else:
            scope_kwargs = dict(owner=request.user, source_type=SourceType.MANUAL, region=region_value)

        created_docs = []
        for uploaded in valid_files:
            stem = Path(uploaded.name).stem
            if title_prefix:
                doc_title = title_prefix if len(valid_files) == 1 else f"{title_prefix} - {stem}"
            else:
                doc_title = stem

            doc = Document(title=doc_title, source_file=uploaded, **scope_kwargs)
            doc.save()  # 파일이 MEDIA_ROOT 에 저장되고 path 가 생긴다

            source_path = Path(doc.source_file.path)
            is_law_upload = (
                doc.source_type == SourceType.LAW and source_path.suffix.lower() != ".csv"
            )
            if is_law_upload:
                # 4차 추가분: seed_docs 가 폴더 법령을 적재할 때 쓰는
                # law_text.read_law_file() 을 그대로 재사용한다. 일반
                # service._read_file() 은 PDF 를 페이지별로 그냥
                # 이어붙이기만 해서, 페이지마다 반복되는 "법제처/국가
                # 법령정보센터" 머리말·꼬리말이나 "- 1 -" 페이지 번호가
                # [시행 ...] [법률 제N호, ...] 헤더 중간에 끼어들 수 있다
                # — 그러면 parse_law_header() 의 정규식이 못 찾는다.
                # read_law_file() 은 이 노이즈를 미리 제거하므로 헤더
                # 인식률도 올라가고 색인 품질도 seed_docs 와 동일해진다.
                from .law_text import read_law_file

                try:
                    extracted = read_law_file(source_path).strip()
                except Exception as exc:
                    doc.delete()
                    skipped.append(f"{uploaded.name}: 텍스트를 추출하지 못했습니다 ({exc}).")
                    continue
            else:
                extracted = service._read_file(source_path).strip()

            if not extracted:
                doc.delete()  # 레코드와 파일을 함께 정리
                skipped.append(f"{uploaded.name}: 텍스트를 추출하지 못했습니다. 스캔 PDF 라면 OCR 처리 후 다시 올려 주세요.")
                continue

            doc.content_text = extracted
            doc.source_key = f"upload:{doc.pk}"
            update_fields = ["content_text", "source_key"]

            if doc.source_type == SourceType.LAW:
                # 원문에 "[시행 YYYY. M. D.] [법률 제N호, ..., 개정유형]"
                # 헤더가 있으면 시행일 안내(law_notice)에 쓸 수 있게 뽑아
                # 둔다. 없으면 조용히 빈 값 — count_articles() 등 기존
                # law_text.py 관례와 같다(예외로 업로드를 막지 않는다).
                # 이 형식 자체가 없는 법령 문서(예: 다른 사이트에서 그대로
                # 복사한 텍스트)라면 애초에 자동으로 못 뽑고, 그 경우
                # law_notice 경고도 뜰 수 없다 — 시행일 정보가 없으니
                # "시행 전"인지 자체를 판단할 근거가 없기 때문이다.
                from .law_text import parse_law_header

                header = parse_law_header(extracted)
                doc.law_effective_date = header["effective_date"]
                doc.law_doc_number = header["doc_number"]
                doc.law_amendment_type = header["amendment_type"]
                update_fields += ["law_effective_date", "law_doc_number", "law_amendment_type"]

            doc.save(update_fields=update_fields)
            created_docs.append(doc)

        if not created_docs:
            form.add_error(None, "업로드에 실패했습니다. " + " / ".join(skipped))
            return render(
                request, "rag/document_upload.html", {"form": form, **extra_context}, status=400,
            )

        # 업로드 즉시 검색에 잡히도록 색인을 재구축한다.
        try:
            result = service.rebuild_index()
            names = ", ".join(d.title for d in created_docs)
            messages.success(
                request,
                f"{len(created_docs)}개 문서({names}) 업로드 완료 — "
                f"문서 {result['documents']}개가 색인되었습니다.",
            )
        except Exception as exc:
            # 문서는 저장됐지만 색인 실패. 3차처럼 부분 성공을 알린다.
            messages.warning(
                request,
                f"{len(created_docs)}개 문서는 저장됐지만 색인에 실패했습니다: {exc} — "
                "관리자 대시보드에서 재색인을 실행해 주세요.",
            )
        if skipped:
            messages.warning(request, "건너뛴 파일이 있습니다 — " + " / ".join(skipped))
        return redirect("rag:documents")


class DocumentDetailView(LoginRequiredMixin, View):
    """근거 원문 보기. 답변의 출처 링크가 여기로 옵니다.

    권한: 공용 문서(law·guide)는 누구나, 사용자 업로드(manual)는 owner
    본인만. 검사를 빠뜨리면 pk 를 바꿔가며 남의 문서를 읽을 수 있습니다.

    ?file=1 이면 업로드 원본 파일을 그대로 내려줍니다 (PDF 대조용).
    """

    def get(self, request, pk):
        doc = get_object_or_404(Document, pk=pk)
        if not doc.is_public and doc.owner_id != request.user.pk:
            # 존재 여부도 숨기기 위해 403 이 아니라 404 를 씁니다.
            raise Http404("문서를 찾을 수 없습니다.")

        if request.GET.get("file") and doc.source_file:
            return FileResponse(doc.source_file.open("rb"))

        return render(request, "rag/document_detail.html", {"document": doc})


class DocumentDeleteView(LoginRequiredMixin, View):
    """문서 삭제 + 색인 재구축.

    삭제 후 rebuild_index() 를 부르지 않으면 지운 문서가 계속 검색됩니다
    (3차 트러블슈팅 4번 "삭제한 파일의 옛 레코드가 잘못 인용됨"과 같은
    계열의 문제).

    권한 (4차 추가분: DocumentUploadView 가 화면에서 law/guide/apartment
    문서를 직접 만들 수 있게 되면서 규칙을 다시 정리했다 — "공용 문서는
    seed_docs 로만 관리"는 더 이상 사실이 아니다. seed_docs 로 심은
    폴더 문서는 애초에 DB 행이 없어(rag/service.py::_load_from_files())
    여기 pk 로 들어올 일이 없다):
        manual     — owner 본인 또는 서비스 관리자
        apartment  — 그 단지를 관리할 수 있는 사람(관리사무소 관리자) 또는
                     서비스 관리자. rag:upload 가 apartment 문서를
                     owner=None 으로 저장하므로 owner 비교로는 판별할 수
                     없다 — apartments.permissions.can_manage_apartment
                     를 그대로 재사용한다.
        law/guide  — 서비스 관리자만. 전국/지역 공용이라 개인·관리사무소
                     권한으로는 지울 수 없다.
    """

    def post(self, request, pk):
        doc = get_object_or_404(Document, pk=pk)
        is_service_admin = request.user.is_service_admin

        if doc.source_type == SourceType.MANUAL:
            allowed = doc.owner_id == request.user.pk or is_service_admin
        elif doc.source_type == SourceType.APARTMENT:
            allowed = is_service_admin or (
                doc.apartment_id is not None
                and apt_permissions.can_manage_apartment(request.user, doc.apartment_id)
            )
        else:
            allowed = is_service_admin

        if not allowed:
            raise Http404("문서를 찾을 수 없습니다.")

        title = doc.title
        doc.delete()
        try:
            service.rebuild_index()
            messages.success(request, f"'{title}' 을(를) 삭제하고 색인을 갱신했습니다.")
        except Exception as exc:
            messages.warning(request, f"'{title}' 은 삭제됐지만 색인 갱신에 실패했습니다: {exc}")
        return redirect("rag:documents")


class IndexStatusView(LoginRequiredMixin, View):
    """인덱스 존재 여부. 3차 GET /api/rag/status 와 응답 형식이 같습니다."""

    def get(self, request):
        from . import vector_store

        return _json({"index_exists": vector_store.index_exists()})


class IndexRebuildView(AdminRequiredMixin, View):
    """인덱스 전체 재구축. 3차 POST /api/rag/rebuild 대응.

    임베딩 API 를 문서 전체에 대해 호출하므로 비용이 발생합니다.
    embeddings.py 의 디스크 캐시가 같은 텍스트 재호출을 막아주지만,
    관리자 전용으로 둡니다. 오류 응답 형식({"detail": ...})은 3차
    HTTPException 과 맞춰서 프론트 에러 처리를 재사용합니다.
    """

    def post(self, request):
        try:
            return _json(service.rebuild_index())
        except Exception as exc:
            return _json({"detail": f"인덱싱에 실패했습니다: {exc}"}, status=500)


class RagSearchView(AdminRequiredMixin, View):
    """검색 품질 진단용. 답변 생성 없이 유사 청크만 반환합니다.

    3차 POST /api/rag/search 대응 — balanced=False 로 순수 유사도 순
    결과를 봅니다. _apply_quota() 를 조정할 때 이 화면으로 확인합니다.
    응답 형식 {"count", "results"} 는 3차와 동일합니다.

    본문은 JSON({"query": ..., "top_k": ...}) 과 폼 인코딩을 모두
    받습니다 — 3차 프론트는 JSON 을 보냈고, Django 템플릿 폼은 폼
    인코딩을 보내기 때문입니다.
    """

    def post(self, request):
        if request.content_type == "application/json":
            try:
                body = json.loads(request.body or b"{}")
            except ValueError:
                return _json({"detail": "잘못된 JSON 본문입니다."}, status=400)
        else:
            body = request.POST

        query = (body.get("query") or "").strip()
        if not query:
            return _json({"detail": "query 가 필요합니다."}, status=400)
        try:
            top_k = max(1, min(int(body.get("top_k", 4)), 20))
        except (TypeError, ValueError):
            top_k = 4

        try:
            results = service.search(
                query, top_k, owner_id=request.user.pk, balanced=False
            )
            return _json({"count": len(results), "results": results})
        except Exception as exc:
            return _json({"detail": f"검색에 실패했습니다: {exc}"}, status=500)
