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

from . import service, tasks
from .forms import DocumentUploadForm
from .models import Document, SourceType


def _json(payload, status=200):
    return JsonResponse(
        payload, status=status, safe=False, json_dumps_params={"ensure_ascii": False}
    )


class DocumentListView(LoginRequiredMixin, View):
    """문서 목록 화면 — "내가 올린 문서 + 공용 문서".

    3차 GET /api/admin/documents 는 관리자 전용이었지만, 업로드 기능이
    생기면 일반 사용자도 자기 문서를 봐야 합니다. 관리자용 청크 통계는
    dashboard:documents 로 분리했습니다.
    """

    def get(self, request):
        from django.db.models import Q

        docs = Document.objects.filter(
            Q(owner=request.user) | Q(source_type__in=[SourceType.LAW, SourceType.GUIDE])
        ).order_by("source_type", "title")
        return render(request, "rag/document_list.html", {"documents": docs})


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
        apartment = apt_scope.current_apartment(request)
        is_manager = bool(apartment) and apt_permissions.can_manage_apartment(request.user, apartment.pk)
        extra_context = self._extra_context(request, apartment, is_manager)

        form = DocumentUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(
                request, "rag/document_upload.html", {"form": form, **extra_context}, status=400,
            )

        doc: Document = form.save(commit=False)
        if extra_context["is_service_admin"]:
            # design 변경(4차 추가): 관리사무소 관리자는 업로드 = 자기
            # 단지 규정으로 고정할 수 있지만, 서비스 총괄 관리자는 국가
            # 전체(법령류)일 수도, 특정 지역 가이드일 수도, 특정 단지
            # 규정일 수도 있다 — 매번 명시적으로 범위를 고르게 한다.
            doc.owner = None
            upload_scope = request.POST.get("upload_scope", "national")
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
                doc.source_type = SourceType.APARTMENT
                doc.apartment = target_apartment
                doc.region = target_apartment.region
                doc.status = Document.Status.APPROVED
            elif upload_scope == "region":
                if not doc.region:
                    form.add_error("region", "지역을 선택해 주세요.")
                    return render(
                        request, "rag/document_upload.html", {"form": form, **extra_context}, status=400,
                    )
                doc.source_type = SourceType.GUIDE
            else:
                doc.source_type = SourceType.GUIDE
                doc.region = None
        elif is_manager:
            # 관리사무소 관리자 업로드는 지역+아파트 메타데이터가 자동으로
            # 붙는다. rag.service.search() 의 기존 apartment_id
            # fail-closed 필터가 격리를 그대로 처리하므로 태깅만 하면 된다.
            doc.owner = None
            doc.source_type = SourceType.APARTMENT
            doc.apartment = apartment
            doc.region = apartment.region
            doc.status = Document.Status.APPROVED
        else:
            doc.owner = request.user
            doc.source_type = SourceType.MANUAL
        doc.save()  # 파일이 MEDIA_ROOT 에 저장되고 path 가 생긴다

        text = service._read_file(Path(doc.source_file.path)).strip()
        if not text:
            doc.delete()  # 레코드와 파일을 함께 정리
            form.add_error(
                "source_file",
                "텍스트를 추출하지 못했습니다. 스캔 PDF 라면 OCR 처리 후 다시 올려 주세요.",
            )
            return render(
                request, "rag/document_upload.html", {"form": form, **extra_context}, status=400,
            )

        doc.content_text = text
        doc.source_key = f"upload:{doc.pk}"
        doc.save(update_fields=["content_text", "source_key"])

        # 색인은 예약만 하고 즉시 반환한다.
        #
        # 예전에는 여기서 rebuild_index() 를 직접 불렀다. 그러면 전체 문서를
        # 다시 임베딩하는 동안 이 요청이 붙잡혀 있고, gunicorn 타임아웃을
        # 넘기면 워커가 죽어서 업로드가 500 으로 끝난다 — 문서는 이미
        # 저장됐는데도. 실제 작업은 ecobot-reindex 가 맡는다(rag/tasks.py).
        tasks.request_reindex(f"업로드: {doc.title[:60]}")
        messages.success(
            request,
            f"'{doc.title}' 업로드 완료 — 색인은 백그라운드에서 갱신됩니다. "
            "검색에 반영되기까지 잠시 걸릴 수 있습니다.",
        )
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
    """문서 삭제 + 색인 갱신 예약.

    삭제해도 재색인 전까지는 청크가 인덱스에 남습니다. 예전에는 이 요청
    안에서 rebuild_index() 까지 끝내 그 창을 없앴지만, 문서가 늘면
    타임아웃에 걸려 삭제 자체가 실패했습니다. 지금은 예약만 하고,
    그 사이의 오인용은 search() 의 _drop_missing_documents() 가 막습니다
    (3차 트러블슈팅 4번 "삭제한 파일의 옛 레코드가 잘못 인용됨"과 같은
    계열의 문제).

    권한: owner 본인 또는 서비스 관리자. 공용 문서(law·guide)는 화면이
    아니라 seed_docs 가 폴더 기준으로 관리하므로 여기서 못 지웁니다.
    """

    def post(self, request, pk):
        doc = get_object_or_404(Document, pk=pk)
        if doc.is_public:
            messages.error(request, "공용 문서는 관리 명령(seed_docs)으로만 삭제할 수 있습니다.")
            return redirect("rag:documents")
        if doc.owner_id != request.user.pk and not request.user.is_service_admin:
            raise Http404("문서를 찾을 수 없습니다.")

        title = doc.title
        doc.delete()
        # 색인에서 빠지는 것은 재색인이 끝나야 반영되지만, 그 사이에도 지운
        # 문서가 인용되지는 않는다 — search() 가 DB 에 없는 document_id 를
        # 결과에서 걸러낸다(rag/service.py:_drop_missing_documents).
        tasks.request_reindex(f"삭제: {title[:60]}")
        messages.success(
            request,
            f"'{title}' 을(를) 삭제했습니다. 색인은 백그라운드에서 갱신됩니다.",
        )
        return redirect("rag:documents")


class IndexStatusView(LoginRequiredMixin, View):
    """인덱스 존재 여부 + 재색인 상태.

    3차 GET /api/rag/status 의 index_exists 는 그대로 두고(프런트 호환),
    재색인이 백그라운드로 바뀌면서 필요해진 진행 상태를 덧붙입니다.
    """

    def get(self, request):
        from . import vector_store
        from .models import ReindexState

        state = ReindexState.get()
        return _json(
            {
                "index_exists": vector_store.index_exists(),
                "reindex": {
                    "status": state.status,
                    "status_display": state.get_status_display(),
                    "pending": state.dirty,
                    "reason": state.reason,
                    "requested_at": state.requested_at,
                    "finished_at": state.finished_at,
                    "last_error": state.last_error,
                },
            }
        )


class IndexRebuildView(AdminRequiredMixin, View):
    """인덱스 전체 재구축. 3차 POST /api/rag/rebuild 대응.

    임베딩 API 를 문서 전체에 대해 호출하므로 비용이 발생합니다.
    embeddings.py 의 디스크 캐시가 같은 텍스트 재호출을 막아주지만,
    관리자 전용으로 둡니다. 오류 응답 형식({"detail": ...})은 3차
    HTTPException 과 맞춰서 프론트 에러 처리를 재사용합니다.
    """

    def post(self, request):
        # 관리자가 수동으로 누르는 버튼이지만 여기서도 기다리지 않는다.
        # 문서가 많을수록 오래 걸리는 건 업로드와 똑같고, 관리자 화면이라고
        # 타임아웃이 비켜 가지는 않는다.
        tasks.request_reindex(f"수동 요청: {request.user.username}")
        return _json(
            {
                "detail": "재색인을 예약했습니다. 진행 상태는 인덱스 상태에서 확인하세요.",
                "queued": True,
            },
            status=202,
        )


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
