# 0001. ecobotapt.com 배포 구성

- 상태: 채택
- 날짜: 2026-08-26
- 맥락: 기존 Caddy가 `기존-사이트.example.com`(Next.js)을 서빙 중인 자택 서버에
  EcoBot을 두 번째 사이트로 얹는다.

## 결정 1 — 정적 파일은 Caddy가 아니라 WhiteNoise가 서빙한다

**왜 자명하지 않은가:** 앞단에 웹서버가 있으면 정적 파일은 그쪽이 직접
내보내는 것이 정석이다. 여기서는 그렇게 하지 않았다.

Caddy는 `caddy` 사용자(uid 997)로 돌고, 프로젝트는 `<앱계정 홈>`(**0750**,
소유 `앱계정:앱계정`) 아래에 있다. `caddy`는 이 디렉터리를 **통과조차 하지
못한다.** `root * $PROJECT_DIR/staticfiles`를 써도 403이 난다.

**검토했다가 안 쓴 대안:**

| 대안 | 왜 안 썼나 |
|---|---|
| `chmod 755 <앱계정 홈>` | 홈 전체를 다른 로컬 사용자에게 여는 대가가 정적 파일 몇 개보다 크다 |
| `setfacl -m u:caddy:x <앱계정 홈>` | 동작은 한다. 다만 ACL은 `ls -l`에 드러나지 않아, 나중에 홈을 옮기거나 권한을 손보는 사람이 이유를 모른 채 깨뜨린다 |
| `staticfiles/`를 `/var/www`로 복사 | `collectstatic` 뒤에 복사 단계가 하나 더 붙고, 두 위치가 어긋나면 낡은 CSS가 서빙된다 |

**대가:** 정적 파일이 파이썬 프로세스를 거친다. WhiteNoise는 파일을 메모리에
올려두고 gzip/brotli 사본을 미리 만들어 두므로 실측 부담은 작고, 해시된
파일명에 `immutable` 캐시 헤더가 붙어 재요청 자체가 드물다. 이 규모에서는
gunicorn 워커를 붙잡는 비용보다 경로 결합이 없는 이점이 크다.

**같은 제약이 배포 절차에도 걸린다.** sudo 를 가진 계정은 `관리 계정`(uid
1000) 하나뿐인데, 이 계정 역시 `<앱계정 홈>` 를 통과하지 못한다. 그래서

```bash
sudo tee -a /etc/caddy/Caddyfile < <앱계정 홈>/.../Caddyfile.ecobotapt
```

가 실패한다 — **리다이렉트는 sudo 가 아니라 셸이 수행**하므로 파일을 여는
주체가 여전히 `관리 계정` 다. `sudo` 를 붙였는데 Permission denied 가 나서
원인을 엉뚱한 데서 찾기 쉬운 형태라, root 로 도는 `deploy/install-system.sh`
로 감싸고 런북 맨 앞에 계정 구조 절을 두었다.

## 결정 2 — 업로드 파일(media)은 Django가 서빙한다

같은 권한 문제다. `django.views.static.serve`는 경로 탈출을 막아 주지만 파일을
파이썬으로 읽어 내보내므로 느리다. 이 프로젝트의 업로드는 프로필 사진·게시글
첨부·단지 규정 PDF 수준이라 실사용에 문제가 없다고 판단했다.

**함정:** `django.conf.urls.static.static()`은 `DEBUG=False`이면 **빈 리스트를
반환한다.** 기존 `config/urls.py`는 이 함수만 쓰고 있어서, 그대로 배포하면
업로드 파일이 전부 404가 되는데 **에러 로그에는 아무것도 안 남는다.**
`re_path` + `static_serve`로 명시적으로 갈아끼웠다.

**빠져나갈 길:** `DJANGO_MEDIA_ROOT`를 홈 밖(`/var/www/ecobot/media`)으로
돌리고 `DJANGO_SERVE_MEDIA=False`로 끄면 Caddy `file_server`로 넘어간다.
설정 두 줄이라 나중에 트래픽이 늘면 바꿀 수 있다.

## 결정 3 — 운영 의존성에서 torch 계열을 뺀다

`requirements-prod.txt`는 `sentence-transformers`와 `langchain-*`을 뺐다.
전자가 끌고 오는 torch만 2~3GB다. **실측: venv 238MB(uv) / 307MB(pip)** — 포함 시 3GB 이상.

안전한 이유는 두 묶음 모두 **함수 안에서 지연 임포트**되기 때문이다
(`rag/embeddings.py:49`, `rag/service.py:153`, `rag/vector_store.py:161`).
모듈 최상단 임포트는 `TYPE_CHECKING` 가드 안에 있어 런타임에 실행되지 않는다.

**대가 — 이게 진짜 비용이다:** `.env`와 requirements 파일이 **암묵적으로
결합**된다. 누가 `EMBEDDING_BACKEND=local`이나 `RAG_PIPELINE=langchain`으로
바꾸면 기동은 정상인데 **그 요청에서만** `ModuleNotFoundError`가 난다.
기동 시점 검증에 걸리지 않는 종류의 실패라, 두 파일 양쪽에 경고를 남겼다.

## 결정 4 — gunicorn `timeout = 180`, `worker_class = gthread`

기본값 30초로는 부족하다. 문서 업로드가 **그 요청 안에서**
`rebuild_index()`로 전체 문서를 재임베딩한다(`rag/views.py:167`). 30초를
넘기면 워커가 죽어 업로드는 500으로 끝나고 인덱스는 중간 상태로 남는다.

워커형을 스레드로 잡은 것은 이 앱의 요청이 CPU가 아니라 **대기**(OpenAI
호출)가 대부분이기 때문이다. 동기 워커면 대기 중인 요청이 프로세스를 통째로
붙잡는다.

**Caddy 쪽에도 같은 180초를 넣었다.** 한쪽만 늘리면 짧은 쪽에서 끊긴다 —
두 곳에 흩어진 값이라 다음 사람이 한쪽만 보고 고칠 위험이 있어 양쪽 주석에
서로를 가리키게 적었다.

**제대로 된 해법은 재색인을 백그라운드 작업으로 빼는 것**이다. 타임아웃 상향은
그 전까지의 임시방편이다.

## 결정 5 — `CSRF_TRUSTED_ORIGINS`를 `ALLOWED_HOSTS`에서 유도한다

명시하지 않으면 `ALLOWED_HOSTS`의 실제 도메인에서 `https://` 형태로 만든다.
도메인을 추가할 때 두 곳을 고치다 한 곳을 빠뜨리는 사고가 잦고, 그 증상이
"로그인 버튼을 누르면 403"이라 원인에서 먼 곳에 나타나기 때문이다.
`DJANGO_CSRF_TRUSTED_ORIGINS`로 언제든 덮어쓸 수 있다.

## 결정 6 — `mysqlclient` 를 유지하고 빌드 의존성을 감수한다

배포 준비 중 `python3.12-venv` 가 없어 venv 가 pip 없이 만들어지는 문제를
겪었다. uv 로 갈아타면 이 apt 의존성이 사라진다 — **그리고 실제로 사라졌다.**
uv 는 ensurepip 없이 venv 를 만들고 없는 파이썬 버전은 직접 내려받는다.

**그런데 `mysqlclient` 는 uv 로도 해결되지 않았다.** PyPI 에 올라온 휠이
**`win_amd64` 하나뿐**이다(2.2.8 기준). Linux·macOS 는 소스 빌드가 강제되고,
MySQL 클라이언트 헤더가 없으면 이렇게 죽는다:

```
Exception: Can not find valid pkg-config name.
```

즉 uv 는 **두 apt 패키지 중 하나만** 없애 준다. 이걸 몰라서 "uv 쓰면 apt 안
건드려도 되겠지"로 넘어가면 서버에서 같은 자리에 다시 걸린다.

**검토했다가 안 쓴 대안:**

| 대안 | 왜 안 썼나 |
|---|---|
| `PyMySQL` (순수 파이썬) | 빌드 의존성이 사라지지만 Django 가 공식 지원하는 드라이버가 아니다. `install_as_MySQLdb()` 몽키패치가 필요하고, 드라이버를 바꾸는 위험이 apt 한 줄보다 크다 |
| `mysql-connector-python` | 같은 이유. 게다가 Django 백엔드가 Oracle 배포판에 묶인다 |
| 개발도 전부 SQLite | 운영이 MySQL 인데 개발이 SQLite 면 그 차이가 배포 시점에 드러난다 |

**대가:** 새 팀원이 Linux·macOS 에서 합류할 때마다 빌드 의존성 한 줄을 깔아야
한다. Windows 는 휠이 있어 영향이 없고, 개발 중 MySQL 이 필요 없으면
`DB_ENGINE=sqlite3` 으로 우회된다. README 에 환경별 표를 넣어 두었다.

## 결정 7 — 파이썬은 3.10~3.13 을 모두 지원한다

3.12 로 못 박지 않았다. 팀원 환경이 3.11 인 경우가 있어서다.
**3.11.14 와 3.12.3 에서 색인·기동까지 실제로 검증**했다(문서 25개 → 청크 880개).
3.12 전용 문법(PEP 695 타입 파라미터, `itertools.batched`, `@override`)은
쓰지 않는다. 3.9 이하는 이식 코드의 `str | None` 때문에 불가하다.

`.python-version` 은 3.12 로 남겨 두었다. uv·pyenv 가 이 파일을 보고 없는
버전을 자동으로 맞춰 주므로, 지우는 것보다 두는 편이 버전이 갈리지 않는다.

## 남겨둔 위험

- **동적 IP.** 공인 IP가 바뀌면 두 도메인이 함께 죽는다. Cloudflare DDNS를
  아직 걸지 않았다.
- **rate limit 없음.** 회원가입이 열려 있고 챗봇 호출에 제한이 없어, OpenAI
  대시보드의 월 한도가 유일한 방어선이다.
- **재색인이 동기.** 결정 4 참고.
