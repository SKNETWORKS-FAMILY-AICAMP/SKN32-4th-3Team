"""gunicorn 설정 — ecobotapt.com 운영용.

    gunicorn -c deploy/gunicorn.conf.py config.wsgi:application
"""

# Caddy 가 이 주소로 reverse_proxy 합니다. 0.0.0.0 으로 열지 마십시오 —
# 그러면 프록시를 우회한 평문 접속이 가능해지고, 그 요청은
# X-Forwarded-Proto 가 없어 CSRF·쿠키 동작이 달라집니다.
bind = "127.0.0.1:8000"

# ── 워커 ──
# 이 앱의 요청은 대부분 CPU 가 아니라 **대기**입니다(OpenAI 임베딩·응답 호출).
# 대기 중인 워커가 프로세스 하나를 통째로 붙잡지 않도록 스레드형을 씁니다.
# 이 서버는 8코어이지만 Next.js·Ollama·MySQL 이 함께 돌고 있어 3개로 잡습니다.
worker_class = "gthread"
workers = 3
threads = 4

# ── 타임아웃 ──
# 기본값 30초로는 부족합니다. 문서를 업로드하면 그 **요청 안에서**
# rebuild_index() 가 전체 문서를 다시 임베딩합니다(rag/views.py:167).
# 30초를 넘기면 gunicorn 이 워커를 죽여서, 업로드가 500 으로 끝나고
# 인덱스는 중간 상태로 남습니다.
#
# Caddy 쪽에도 같은 값이 있어야 합니다. 한쪽만 늘리면 짧은 쪽에서 끊깁니다.
timeout = 180
graceful_timeout = 30

# 유휴 연결을 재사용해 TLS·TCP 핸드셰이크를 아낍니다(앞단이 Caddy 하나뿐이라 안전).
keepalive = 5

# ── 워커 재시작 ──
# faiss 인덱스를 요청마다 역직렬화하므로 파편화가 쌓입니다. 주기적으로
# 갈아치워 메모리 사용량이 우상향하는 것을 막습니다. jitter 는 모든 워커가
# 동시에 재시작해 순간적으로 응답이 끊기는 것을 방지합니다.
max_requests = 1000
max_requests_jitter = 100

# ── 로그 ──
# 파일이 아니라 stdout/stderr 로 내보내 systemd → journald 가 받게 합니다.
# 로테이션을 journald 가 해주므로 로그 파일 권한 문제가 생기지 않습니다.
#   확인:  journalctl -u ecobot -f
accesslog = "-"
errorlog = "-"
loglevel = "info"

# 응답 시간(%(D)s, 마이크로초)을 포함시킵니다 — 어느 요청이 느린지 봐야 합니다.
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sus "%(f)s"'
