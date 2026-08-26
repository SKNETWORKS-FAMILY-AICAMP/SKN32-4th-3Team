#!/usr/bin/env bash
#
# EcoBot 시스템 설치 — root 권한이 필요한 작업만 모았습니다.
#
# 이 저장소는 앱 계정 소유이고 <앱계정 홈> 는 0750 입니다. sudo 를 가진
# 계정(관리 계정)은 이 디렉터리를 **통과조차 못 합니다**. 그래서
#
#     sudo tee -a /etc/caddy/Caddyfile < deploy/Caddyfile.ecobotapt
#
# 같은 형태는 실패합니다 — 리다이렉트(<)는 sudo 가 아니라 **셸**이 수행하므로
# 관리 계정 권한으로 파일을 열려다 Permission denied 가 납니다.
#
# 이 스크립트는 전체가 root 로 실행되므로 그 문제가 없습니다.
# 실행 위치는 상관없습니다(모든 경로가 절대 경로입니다).
#
#   sudo bash $PROJECT_DIR/deploy/install-system.sh deps
#   sudo bash $PROJECT_DIR/deploy/install-system.sh service
#   sudo bash $PROJECT_DIR/deploy/install-system.sh caddy
#
# 런북(docs/deploy.md)의 순서대로 deps → (사람이 .env·DB 준비) →
# service → caddy 로 나눠 두었습니다. `all` 은 셋을 연달아 실행합니다.
#
# 몇 번을 실행해도 안전합니다(중복 추가하지 않습니다).

set -euo pipefail

PROJECT_DIR="$PROJECT_DIR"
APP_USER="앱 계정"
CADDYFILE="/etc/caddy/Caddyfile"
DOMAIN="ecobotapt.com"
SERVER_IP="<서버 공인 IP>"

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
    cat <<USAGE
사용법:  sudo bash $0 <단계>

  deps     시스템 패키지 (default-libmysqlclient-dev)
  db       MySQL 데이터베이스·계정 생성  ← .env 작성 후에
  service  systemd 유닛 설치 + 기동      ← migrate 후에
  caddy    Caddy 사이트 블록 추가        ← 마지막
  all      위 넷을 연달아

  ── 배포 후 보안·운영 (선택, 순서 무관) ──
  mysql-secure  MySQL 을 127.0.0.1 로 제한
  ddns          Cloudflare DDNS 설치 (IP 변경 추적)
  reindex       재색인 워커 설치 (백그라운드 색인)
  cleanup       고아 업로드 파일 주간 정리 (선택 — 자동 삭제)

런북: $PROJECT_DIR/docs/deploy.md
USAGE
}

# 사용법을 root 검사보다 먼저 봅니다 — 인자를 몰라서 실행해 본 사람에게
# "root 로 실행하십시오"만 띄우면 무엇을 하라는 건지 알 수 없습니다.
case "${1:-}" in
    deps|db|service|caddy|all|mysql-secure|ddns|reindex|cleanup) ;;
    *) usage; exit 1 ;;
esac

[[ $EUID -eq 0 ]] || die "root 로 실행해야 합니다:  sudo bash $0 $1"
[[ -d $PROJECT_DIR ]] || die "$PROJECT_DIR 가 없습니다"


install_deps() {
    say "1) 시스템 패키지"
    # mysqlclient 는 C 확장이고 PyPI 에 Windows 휠만 있습니다.
    # Linux 에서는 소스 빌드가 강제되므로 MySQL 클라이언트 헤더가 필요합니다.
    # (uv 를 써도 이건 우회되지 않습니다)
    apt-get update -qq
    apt-get install -y default-libmysqlclient-dev
    ok "default-libmysqlclient-dev 설치됨"

    if pkg-config --exists mysqlclient; then
        ok "pkg-config 가 mysqlclient 를 인식합니다 — pip 설치가 가능합니다"
    else
        warn "pkg-config 가 아직 mysqlclient 를 못 찾습니다. 아래를 확인하십시오:"
        warn "  pkg-config --list-all | grep -i mysql"
    fi

    cat <<MSG

  다음은 앱 계정 계정에서 (sudo 불필요):
      cd $PROJECT_DIR
      uv pip install --python .venv/bin/python -r requirements-prod.txt
MSG
}


install_db() {
    say "2) MySQL 데이터베이스 · 계정"
    local envfile="$PROJECT_DIR/.env"
    [[ -f $envfile ]] || die "$envfile 가 없습니다. 앱 계정 계정에서 먼저 작성하십시오"

    # 비밀번호를 여기에 적지 않고 .env 에서 읽습니다. 두 곳에 적으면 반드시
    # 어긋나고, 그때 증상이 "Django 만 접속 실패"라 원인에서 멉니다.
    # root 로 실행 중이므로 0600 인 .env 도 읽을 수 있습니다.
    local name user pass
    name=$(grep -E "^DB_NAME=" "$envfile" | cut -d= -f2- | tr -d "\r")
    user=$(grep -E "^DB_USER=" "$envfile" | cut -d= -f2- | tr -d "\r")
    pass=$(grep -E "^DB_PASSWORD=" "$envfile" | cut -d= -f2- | tr -d "\r")

    [[ -n $name && -n $user && -n $pass ]] \
        || die ".env 의 DB_NAME / DB_USER / DB_PASSWORD 를 확인하십시오"
    [[ $pass != *"넣으십시오"* ]] \
        || die ".env 의 DB_PASSWORD 가 아직 예시값입니다"
    [[ $pass != *"'"* ]] \
        || die "DB_PASSWORD 에 작은따옴표가 있으면 이 스크립트로 처리할 수 없습니다"

    # MySQL root 는 auth_socket 이라 OS root 로만 붙습니다(그래서 이 단계가
    # 앱 계정 계정에서 불가능합니다). 아래는 몇 번 돌려도 안전합니다.
    mysql <<SQL
CREATE DATABASE IF NOT EXISTS \`$name\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$user'@'localhost' IDENTIFIED BY '$pass';
ALTER USER '$user'@'localhost' IDENTIFIED BY '$pass';
GRANT ALL PRIVILEGES ON \`$name\`.* TO '$user'@'localhost';
FLUSH PRIVILEGES;
SQL
    ok "데이터베이스 '$name' · 계정 '$user'@'localhost' 준비됨"

    # 실제로 붙는지 확인합니다 — 여기서 확인해 두면 migrate 가 실패할 때
    # 원인이 DB 인지 Django 설정인지 나눠서 볼 수 있습니다.
    if mysql -u "$user" -p"$pass" -e "USE \`$name\`; SELECT 1;" >/dev/null 2>&1; then
        ok "'$user' 로 '$name' 접속 확인됨"
    else
        die "계정은 만들었으나 접속에 실패했습니다"
    fi

    cat <<MSG

  다음은 앱 계정 계정에서 (sudo 불필요):
      cd $PROJECT_DIR
      .venv/bin/python manage.py migrate
      .venv/bin/python manage.py createsuperuser
      .venv/bin/python manage.py seed_docs
      .venv/bin/python manage.py rag_reindex
      .venv/bin/python manage.py seed_apartments
MSG
}


install_service() {
    say "3) systemd 유닛"
    local src="$PROJECT_DIR/deploy/ecobot.service"
    [[ -f $src ]] || die "$src 가 없습니다"

    install -m 644 -o root -g root "$src" /etc/systemd/system/ecobot.service
    systemctl daemon-reload
    ok "/etc/systemd/system/ecobot.service 설치됨"

    # 유닛은 User=앱 계정 로 돌고 경로가 전부 절대 경로입니다. 누가 설치하든
    # 서비스는 앱 계정 권한으로 실행되므로 파일 소유권을 바꿀 필요가 없습니다.
    [[ -x "$PROJECT_DIR/.venv/bin/gunicorn" ]] \
        || die ".venv/bin/gunicorn 이 없습니다. 앱 계정 계정에서 의존성을 먼저 설치하십시오"
    [[ -f "$PROJECT_DIR/.env" ]] \
        || die ".env 가 없습니다. 런북 3단계를 먼저 끝내십시오"
    ok "gunicorn · .env 확인됨"

    # 서비스가 써야 하는 디렉터리(유닛의 ReadWritePaths 와 일치해야 합니다)
    for d in media vector_db; do
        install -d -o "$APP_USER" -g "$APP_USER" "$PROJECT_DIR/$d"
    done
    ok "media/ · vector_db/ 준비됨"

    systemctl enable --now ecobot
    sleep 2
    if systemctl is-active --quiet ecobot; then
        ok "ecobot 기동됨"
    else
        warn "기동 실패 — 로그:  journalctl -u ecobot -n 40 --no-pager"
        exit 1
    fi
}


install_caddy() {
    say "4) Caddy 사이트 블록"
    local snippet="$PROJECT_DIR/deploy/Caddyfile.ecobotapt"
    [[ -f $snippet ]] || die "$snippet 가 없습니다"

    # DNS 가 먼저여야 합니다. 없는 상태로 reload 하면 Let's Encrypt 검증이
    # 실패하고, 반복하면 발급 한도에 걸려 몇 시간 막힙니다.
    local resolved
    resolved=$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)
    if [[ $resolved != "$SERVER_IP" ]]; then
        die "DNS 미확인: $DOMAIN → '${resolved:-없음}' (기대: $SERVER_IP)"
    fi
    ok "DNS 확인됨 ($DOMAIN → $SERVER_IP)"

    if grep -q "$DOMAIN" "$CADDYFILE"; then
        warn "$CADDYFILE 에 $DOMAIN 블록이 이미 있습니다 — 추가하지 않습니다"
    else
        local backup="${CADDYFILE}.bak.$(date +%Y%m%d-%H%M%S)"
        cp -a "$CADDYFILE" "$backup"
        ok "백업: $backup"

        cat "$snippet" >> "$CADDYFILE"
        ok "블록 추가됨"

        if ! caddy validate --config "$CADDYFILE" --adapter caddyfile 2>&1 | grep -q "Valid configuration"; then
            cp -a "$backup" "$CADDYFILE"
            die "설정이 유효하지 않아 되돌렸습니다. 위 오류를 확인하십시오"
        fi
        ok "caddy validate 통과"
    fi

    systemctl reload caddy
    ok "caddy reload 됨 (무중단 — 기존-사이트.example.com 은 끊기지 않습니다)"

    cat <<MSG

  인증서 발급을 확인하십시오 (첫 요청 때 자동 발급):
      journalctl -u caddy -f
      → "certificate obtained successfully" 를 기다리십시오

  그 다음 휴대폰에서 Wi-Fi 를 끄고 https://$DOMAIN 을 열어 보십시오.
  (서버 안에서 자기 공인 IP 로 접속하면 NAT 헤어핀 때문에 타임아웃이 납니다)
MSG
}


secure_mysql() {
    say "MySQL 을 127.0.0.1 로 제한"
    local cnf="/etc/mysql/mysql.conf.d/mysqld.cnf"
    [[ -f $cnf ]] || die "$cnf 가 없습니다"

    if grep -qE "^bind-address[[:space:]]*=" "$cnf"; then
        ok "bind-address 가 이미 명시돼 있습니다: $(grep -E '^bind-address' "$cnf")"
    else
        # 기본 설정은 이 줄이 주석 처리돼 있어 MySQL 이 모든 인터페이스(*:3306)
        # 로 열립니다. 공유기가 3306 을 포워딩하지 않으면 인터넷에서는 안 닿지만
        # LAN 의 다른 기기에서는 접속할 수 있습니다.
        cp -a "$cnf" "${cnf}.bak.$(date +%Y%m%d-%H%M%S)"
        if grep -qE "^#bind-address[[:space:]]*=" "$cnf"; then
            sed -i "s/^#bind-address[[:space:]]*=.*/bind-address = 127.0.0.1/" "$cnf"
        else
            sed -i "/^\[mysqld\]/a bind-address = 127.0.0.1" "$cnf"
        fi
        ok "bind-address = 127.0.0.1 설정됨 (백업 남김)"
    fi

    # 재시작 전에 LAN 에서 붙어 있는 클라이언트가 있는지 봅니다. 있으면
    # 잠그는 순간 그쪽이 끊깁니다.
    local remote
    remote=$(mysql -N -B -e "SELECT COUNT(*) FROM information_schema.processlist WHERE host NOT LIKE 'localhost%' AND host NOT LIKE '127.0.0.1%';" 2>/dev/null || echo 0)
    if [[ ${remote:-0} -gt 0 ]]; then
        warn "로컬이 아닌 연결이 $remote 건 있습니다. 잠그면 끊깁니다."
        warn "확인:  sudo mysql -e \"SELECT user,host FROM information_schema.processlist\""
        die "중단했습니다. 확인 후 다시 실행하십시오"
    fi
    ok "로컬 외 연결 없음 — 재시작해도 안전합니다"

    systemctl restart mysql
    sleep 3
    if ss -tln | grep -qE "127\.0\.0\.1:3306"; then
        ok "MySQL 이 127.0.0.1:3306 으로만 열렸습니다"
    else
        warn "현재 바인딩: $(ss -tln | grep 3306 || echo '(3306 없음)')"
        die "예상과 다릅니다. $cnf 를 확인하십시오"
    fi

    # Django 는 CONN_MAX_AGE 기본값(0)이라 요청마다 연결을 새로 맺습니다.
    # 재시작 직후 요청도 새 연결을 만들므로 ecobot 을 건드릴 필요가 없습니다.
    systemctl is-active --quiet ecobot && ok "ecobot 은 재시작 불필요 (CONN_MAX_AGE=0)"
}


install_ddns() {
    say "Cloudflare DDNS"
    local dir="/etc/ddns-cloudflare" cfg="/etc/ddns-cloudflare/config.env"
    local src="$PROJECT_DIR/deploy"

    install -d -m 700 -o root -g root "$dir"

    if [[ -f $cfg ]]; then
        ok "설정이 이미 있습니다: $cfg (덮어쓰지 않습니다)"
    else
        cat > "$cfg" <<'CFG'
# Cloudflare DDNS 설정.  이 파일에 API 토큰이 들어갑니다 — 0600 유지.
#
# 토큰 만들기:
#   Cloudflare 대시보드 → 우측 상단 프로필 → API Tokens → Create Token
#   → "Edit zone DNS" 템플릿
#   → Permissions : Zone / DNS / Edit
#   → Zone Resources: Include / Specific zone → ecobotapt.com
#                     (+ Add more) Include / Specific zone → example.com
#   → 두 zone 을 모두 넣어야 위키까지 따라갑니다.

CF_API_TOKEN=

# 공백으로 구분. 여기 적힌 A 레코드만 갱신합니다(이미 존재해야 합니다).
DDNS_RECORDS="ecobotapt.com www.ecobotapt.com 기존-사이트.example.com"
CFG
        chmod 600 "$cfg"
        ok "설정 템플릿 생성: $cfg"
    fi

    install -m 644 -o root -g root "$src/ddns-cloudflare.service" /etc/systemd/system/
    install -m 644 -o root -g root "$src/ddns-cloudflare.timer"   /etc/systemd/system/
    systemctl daemon-reload
    ok "유닛 설치됨"

    if ! grep -qE "^CF_API_TOKEN=.+" "$cfg"; then
        warn "CF_API_TOKEN 이 비어 있어 타이머를 켜지 않았습니다."
        cat <<MSG

  토큰을 넣은 뒤 아래를 실행하십시오:
      sudo nano $cfg
      sudo $PROJECT_DIR/deploy/ddns-cloudflare.sh      # 한 번 수동 확인
      sudo systemctl enable --now ddns-cloudflare.timer
MSG
        return 0
    fi

    # 타이머를 켜기 전에 한 번 직접 돌려 봅니다 — 토큰 권한이 모자라면
    # 여기서 드러납니다. 타이머로만 돌리면 5분 뒤 저널을 봐야 압니다.
    if "$src/ddns-cloudflare.sh"; then
        systemctl enable --now ddns-cloudflare.timer
        ok "타이머 활성화됨 (5분 주기)"
        systemctl list-timers ddns-cloudflare.timer --no-pager | sed -n "2p"
    else
        die "수동 실행이 실패했습니다. 위 오류를 확인하십시오 (타이머는 켜지 않았습니다)"
    fi
}


install_reindex() {
    say "재색인 워커 (백그라운드 색인)"
    local src="$PROJECT_DIR/deploy"
    local vdir="$PROJECT_DIR/vector_db"

    [[ -x "$PROJECT_DIR/.venv/bin/python" ]] \
        || die ".venv 가 없습니다. 앱 계정 계정에서 의존성을 먼저 설치하십시오"

    # 트리거·락 파일이 여기 생깁니다. 웹 프로세스(앱 계정)가 써야 하므로
    # 소유자를 맞춰 둡니다.
    install -d -o "$APP_USER" -g "$APP_USER" "$vdir"
    ok "vector_db/ 준비됨"

    install -m 644 -o root -g root "$src/ecobot-reindex.service" /etc/systemd/system/
    install -m 644 -o root -g root "$src/ecobot-reindex.path"    /etc/systemd/system/
    install -m 644 -o root -g root "$src/ecobot-reindex.timer"   /etc/systemd/system/
    systemctl daemon-reload
    ok "유닛 3개 설치됨 (service · path · timer)"

    # path 유닛은 감시 대상 파일이 없어도 뜨지만, 미리 만들어 두면
    # 첫 트리거가 확실히 잡힙니다.
    sudo -u "$APP_USER" touch "$vdir/reindex.trigger" 2>/dev/null || true

    systemctl enable --now ecobot-reindex.path
    systemctl enable --now ecobot-reindex.timer
    ok "path · timer 활성화됨"

    # 한 번 돌려서 실제로 동작하는지 봅니다. --if-needed 라서 갱신할 것이
    # 없으면 즉시 끝납니다 — 여기서 실패하면 설정이 잘못된 것입니다.
    systemctl start ecobot-reindex.service
    sleep 2
    local res
    res=$(systemctl show ecobot-reindex.service -p Result --value)
    if [[ $res == "success" ]]; then
        ok "시험 실행 성공"
    else
        warn "시험 실행 결과: $res"
        warn "로그:  journalctl -u ecobot-reindex -n 30 --no-pager"
        die "재색인 워커가 정상 동작하지 않습니다"
    fi

    cat <<MSG

  이제 문서 업로드·삭제가 색인을 기다리지 않습니다.
      journalctl -u ecobot-reindex -f      # 색인 로그
      systemctl list-timers ecobot-reindex # 다음 안전망 실행

  ⚠️ 코드가 바뀌었으므로 앱 계정 계정에서 마이그레이션이 필요합니다:
      cd $PROJECT_DIR && .venv/bin/python manage.py migrate
MSG
}


install_cleanup() {
    say "고아 업로드 파일 주간 정리"
    local src="$PROJECT_DIR/deploy"

    [[ -x "$PROJECT_DIR/.venv/bin/python" ]] \
        || die ".venv 가 없습니다. 앱 계정 계정에서 의존성을 먼저 설치하십시오"

    # 켜기 전에 무엇이 지워질지 보여 줍니다. 자동 삭제를 붙이는 작업이라
    # "설치했더니 파일이 사라졌다"가 되지 않게 합니다.
    warn "이 타이머는 파일을 되돌릴 수 없게 지웁니다. 현재 대상은 다음과 같습니다:"
    echo
    sudo -u "$APP_USER" env -C "$PROJECT_DIR" \
        "$PROJECT_DIR/.venv/bin/python" manage.py cleanup_orphan_files \
        --min-age-hours 168 2>/dev/null | sed "s/^/    /"
    echo

    install -m 644 -o root -g root "$src/ecobot-cleanup.service" /etc/systemd/system/
    install -m 644 -o root -g root "$src/ecobot-cleanup.timer"   /etc/systemd/system/
    systemctl daemon-reload
    ok "유닛 2개 설치됨"

    systemctl enable --now ecobot-cleanup.timer
    ok "타이머 활성화됨 (매주 일요일 04:00)"
    systemctl list-timers ecobot-cleanup.timer --no-pager | sed -n "2p"

    cat <<MSG

  지금 한 번 돌려 보려면:
      sudo systemctl start ecobot-cleanup
      journalctl -u ecobot-cleanup -n 30 --no-pager

  삭제 없이 목록만 보려면 (앱 계정 계정):
      cd $PROJECT_DIR
      .venv/bin/python manage.py cleanup_orphan_files
MSG
}


case "$1" in
    deps)    install_deps ;;
    db)      install_db ;;
    service) install_service ;;
    caddy)   install_caddy ;;
    all)     install_deps; install_db; install_service; install_caddy ;;
    mysql-secure) secure_mysql ;;
    ddns)         install_ddns ;;
    reindex)      install_reindex ;;
    cleanup)      install_cleanup ;;
esac

say "완료"
