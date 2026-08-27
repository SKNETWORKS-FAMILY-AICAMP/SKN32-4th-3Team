#!/usr/bin/env bash
#
# Cloudflare DDNS — 공인 IP 가 바뀌면 A 레코드를 따라가게 합니다.
#
# 왜 필요한가: 이 서버는 동적 IP 회선이라 공인 IP 가 고정이 아닙니다. IP 가
# 바뀌면 A 레코드가 옛 주소를 가리킨 채 남고 **사이트가 죽습니다.** 같은
# 회선에 도메인이 여럿이면 전부 함께 죽으므로 자동화해 둡니다.
#
# 여러 zone 을 갱신해도 같은 Cloudflare 계정이면 토큰 하나로 처리됩니다.
# zone 은 FQDN 에서 자동으로 찾습니다.
#
# 설정:  /etc/ddns-cloudflare/config.env  (0600, root)
#     CF_API_TOKEN=...
#     DDNS_RECORDS="ecobotapt.com www.ecobotapt.com"
#
# 수동 실행:  sudo <저장소>/deploy/ddns-cloudflare.sh
# 자동 실행:  systemd timer (ddns-cloudflare.timer)
# 로그:       journalctl -u ddns-cloudflare

set -uo pipefail

CONFIG="${DDNS_CONFIG:-/etc/ddns-cloudflare/config.env}"
API="https://api.cloudflare.com/client/v4"

log()  { printf '%s %s\n' "$(date '+%F %T')" "$*"; }
fail() { log "ERROR: $*"; exit 1; }

[[ -r $CONFIG ]] || fail "설정 파일을 읽을 수 없습니다: $CONFIG"
# shellcheck source=/dev/null
source "$CONFIG"

[[ -n ${CF_API_TOKEN:-} ]]  || fail "CF_API_TOKEN 이 비어 있습니다"
[[ -n ${DDNS_RECORDS:-} ]]  || fail "DDNS_RECORDS 가 비어 있습니다"

cf() {  # cf <METHOD> <PATH> [BODY]
    local method=$1 path=$2 body=${3:-}
    if [[ -n $body ]]; then
        curl -sS --max-time 20 -X "$method" "$API$path" \
            -H "Authorization: Bearer $CF_API_TOKEN" \
            -H "Content-Type: application/json" --data "$body"
    else
        curl -sS --max-time 20 -X "$method" "$API$path" \
            -H "Authorization: Bearer $CF_API_TOKEN"
    fi
}

# ── 현재 공인 IP ──
# 한 곳만 믿지 않습니다. 조회처가 죽었을 때 빈 값으로 레코드를 덮어쓰면
# 도메인 전체가 날아가므로, 형식 검증까지 통과한 값만 씁니다.
get_public_ip() {
    local ip
    for src in \
        "https://cloudflare.com/cdn-cgi/trace" \
        "https://api.ipify.org" \
        "https://icanhazip.com"
    do
        ip=$(curl -sS --max-time 10 "$src" 2>/dev/null \
             | grep -oE '(^|ip=)[0-9]{1,3}(\.[0-9]{1,3}){3}$' | sed 's/^ip=//')
        [[ $ip =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] && { echo "$ip"; return 0; }
    done
    return 1
}

IP=$(get_public_ip) || fail "공인 IP 를 확인하지 못했습니다 — 아무것도 바꾸지 않았습니다"
log "현재 공인 IP: $IP"

# ── 토큰 먼저 검증 ──
# 이걸 건너뛰면 토큰이 틀렸을 때 모든 조회가 빈 결과로 돌아와서
# "zone 을 찾지 못했습니다"로 보입니다. 원인에서 한참 먼 메시지라
# Cloudflare 에서 zone 설정을 뒤지게 됩니다. 먼저 확인해 구분합니다.
verify_token() {
    local resp ok_
    resp=$(cf GET "/user/tokens/verify")
    ok_=$(echo "$resp" | jq -r '.success // false')
    if [[ $ok_ != "true" ]]; then
        fail "API 토큰이 유효하지 않습니다: $(echo "$resp" | jq -c '.errors // .messages // .')"
    fi
    log "토큰 확인됨 (status: $(echo "$resp" | jq -r '.result.status // "?"'))"
}

# ── zone 찾기 ──
# FQDN 에서 뒤에서부터 좁혀 가며 등록된 zone 을 찾습니다.
#   www.ecobotapt.com → "www.ecobotapt.com"(X) → "ecobotapt.com"(O)
find_zone_id() {
    local fqdn=$1 candidate=$fqdn resp id
    while [[ $candidate == *.* ]]; do
        resp=$(cf GET "/zones?name=$candidate&status=active")
        # 권한 부족(토큰에 그 zone 이 안 들어간 경우)과 "없음" 을 구분합니다.
        if [[ $(echo "$resp" | jq -r '.success // false') != "true" ]]; then
            log "  ($candidate 조회 실패: $(echo "$resp" | jq -c '.errors'))"
        fi
        id=$(echo "$resp" | jq -r '.result[0].id // empty')
        [[ -n $id ]] && { echo "$id"; return 0; }
        candidate=${candidate#*.}
    done
    return 1
}

# 토큰을 먼저 검증합니다. 함수 정의가 모두 끝난 뒤여야 하므로 여기입니다.
verify_token

changed=0 checked=0 failed=0

for fqdn in $DDNS_RECORDS; do
    checked=$((checked + 1))

    zone_id=$(find_zone_id "$fqdn") || { log "SKIP $fqdn — zone 없음 (토큰의 Zone Resources 에 이 도메인이 포함됐는지 확인하십시오)"; failed=$((failed+1)); continue; }

    rec=$(cf GET "/zones/$zone_id/dns_records?type=A&name=$fqdn")
    rec_id=$(echo "$rec"  | jq -r '.result[0].id // empty')
    cur_ip=$(echo "$rec"  | jq -r '.result[0].content // empty')
    proxied=$(echo "$rec" | jq -r '.result[0].proxied // false')
    ttl=$(echo "$rec"     | jq -r '.result[0].ttl // 1')

    if [[ -z $rec_id ]]; then
        log "SKIP $fqdn — A 레코드가 없습니다(먼저 Cloudflare 에서 만드십시오)"
        failed=$((failed + 1)); continue
    fi

    if [[ $cur_ip == "$IP" ]]; then
        log "OK   $fqdn — 변경 없음 ($cur_ip)"
        continue
    fi

    # proxied 와 ttl 을 그대로 실어 보냅니다. 빠뜨리면 Cloudflare 가 기본값으로
    # 되돌려 버려서, 회색 구름이던 레코드가 주황 구름이 되고 Caddy 의
    # Let's Encrypt 갱신이 막힙니다.
    body=$(jq -nc --arg ip "$IP" --arg n "$fqdn" --argjson p "$proxied" --argjson t "$ttl" \
        '{type:"A", name:$n, content:$ip, proxied:$p, ttl:$t}')
    resp=$(cf PATCH "/zones/$zone_id/dns_records/$rec_id" "$body")

    if [[ $(echo "$resp" | jq -r '.success') == "true" ]]; then
        log "UPDATE $fqdn — $cur_ip → $IP (proxied=$proxied)"
        changed=$((changed + 1))
    else
        log "ERROR $fqdn — 갱신 실패: $(echo "$resp" | jq -c '.errors')"
        failed=$((failed + 1))
    fi
done

log "완료 — 확인 $checked건, 변경 $changed건, 실패 $failed건"
[[ $failed -eq 0 ]] || exit 1
