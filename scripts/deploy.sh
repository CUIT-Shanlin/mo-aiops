#!/usr/bin/env bash
set -euo pipefail

# mo-chat-aiops 快速构建 & 部署脚本
# 用法:
#   ./scripts/deploy.sh              # 构建镜像 + Helm upgrade
#   ./scripts/deploy.sh --build-only # 仅构建镜像
#   ./scripts/deploy.sh --deploy-only # 仅部署（跳过构建）
#   ./scripts/deploy.sh --logs       # 查看 pod 日志
#   ./scripts/deploy.sh --status     # 查看部署状态
#   ./scripts/deploy.sh --delete     # 删除整个部署
#
# 敏感配置从 .env.deploy 读取（不提交 git）
# 首次使用: cp .env.deploy.example .env.deploy && 编辑填入真实值

NAMESPACE="mochat-aiops"
RELEASE="aiops"
CHART="deploy/mo-chat-aiops"
IMAGE="mo-chat-aiops"
TAG="0.1.0"
HEALTH_URL="http://aiops-mo-chat-aiops.${NAMESPACE}.svc.cluster.local:8000/health"

cd "$(dirname "$0")/.."

info()  { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()    { printf "\033[1;32m✔ %s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33m⚠ %s\033[0m\n" "$*"; }
fail()  { printf "\033[1;31m✘ %s\033[0m\n" "$*"; exit 1; }

load_env_deploy() {
    local env_file=".env.deploy"
    if [[ ! -f "$env_file" ]]; then
        fail ".env.deploy not found. Copy from example: cp .env.deploy.example .env.deploy"
    fi

    # 解析 .env.deploy，忽略注释和空行，支持 export 前缀和引号
    while IFS= read -r line || [[ -n "$line" ]]; do
        # 跳过注释和空行
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${line// /}" ]] && continue
        # 去掉 export 前缀
        line="${line#export }"
        # 解析 KEY=VALUE
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            local key="${BASH_REMATCH[1]}"
            local val="${BASH_REMATCH[2]}"
            # 去掉外层引号
            val="${val#\"}"
            val="${val%\"}"
            val="${val#\'}"
            val="${val%\'}"
            export "DEPLOY_${key}=${val}"
        fi
    done < "$env_file"

    # 校验必填项
    [[ -z "${DEPLOY_JWT_SECRET:-}" ]]   && fail "JWT_SECRET is empty in .env.deploy"
    [[ -z "${DEPLOY_LLM_API_KEY:-}" ]] && fail "LLM_API_KEY is empty in .env.deploy"

    ok "Loaded .env.deploy"
}

check_prereqs() {
    command -v docker >/dev/null 2>&1 || fail "docker not found"
    command -v helm  >/dev/null 2>&1 || fail "helm not found"
    command -v kubectl >/dev/null 2>&1 || fail "kubectl not found"
    kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || fail "namespace $NAMESPACE not found"
}

build_image() {
    info "Building Docker image ${IMAGE}:${TAG} ..."
    docker build -t "${IMAGE}:${TAG}" . 2>&1 | tail -5
    ok "Image built: ${IMAGE}:${TAG}"
}

deploy_helm() {
    info "Deploying via Helm (release: ${RELEASE}) ..."

    # 从 .env.deploy 构建 --set-string 参数
    local helm_sets=(
        --set-string "secret.data.DATABASE_URL=${DEPLOY_DATABASE_URL}"
        --set-string "secret.data.REDIS_URL=${DEPLOY_REDIS_URL}"
        --set-string "secret.data.JWT_SECRET=${DEPLOY_JWT_SECRET}"
        --set-string "secret.data.ADMIN_PASSWORD_HASH=${DEPLOY_ADMIN_PASSWORD_HASH}"
        --set-string "secret.data.LLM_API_KEY=${DEPLOY_LLM_API_KEY}"
    )

    local helm_cmd=(helm upgrade --install "$RELEASE" "$CHART" -n "$NAMESPACE" "${helm_sets[@]}")

    "${helm_cmd[@]}" 2>&1 | tail -5

    info "Waiting for rollout ..."
    kubectl rollout status "deployment/${RELEASE}-mo-chat-aiops" -n "$NAMESPACE" --timeout=120s
    ok "Rollout complete"
}

verify() {
    info "Verifying deployment ..."
    local pod
    pod=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    [[ -z "$pod" ]] && fail "No pod found"

    kubectl run "aiops-verify-$RANDOM" --rm -i --restart=Never --image=curlimages/curl \
        -n "$NAMESPACE" -- sh -c "
        echo '--- health ---'
        curl -sf $HEALTH_URL && echo
        echo '--- Prometheus ---'
        curl -sf --connect-timeout 3 http://prometheus.mochat-observability.svc.cluster.local:9090/-/healthy && echo
        echo '--- Loki ---'
        curl -sf --connect-timeout 3 http://loki.mochat-observability.svc.cluster.local:3100/ready && echo
        echo '--- Tempo ---'
        curl -sf --connect-timeout 3 http://tempo.mochat-observability.svc.cluster.local:3200/ready && echo
        echo '--- OK ---'
    " 2>&1 | grep -E '^---|healthy|ready|OK|error|fail' || true

    ok "Pod: $pod"
}

show_status() {
    echo ""
    info "=== Pods ==="
    kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE}" -o wide
    echo ""
    info "=== Services ==="
    kubectl get svc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE}"
    echo ""
    info "=== Helm Release ==="
    helm status "$RELEASE" -n "$NAMESPACE" 2>/dev/null || warn "Release not found"
    echo ""
    info "=== Recent Logs ==="
    local pod
    pod=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [[ -n "$pod" ]]; then
        kubectl logs "$pod" -n "$NAMESPACE" --tail=15 2>/dev/null
    fi
}

show_logs() {
    local pod
    pod=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    [[ -z "$pod" ]] && fail "No pod found"
    kubectl logs "$pod" -n "$NAMESPACE" -f --tail=100
}

delete_release() {
    warn "Deleting Helm release '${RELEASE}' from namespace '${NAMESPACE}' ..."
    helm uninstall "$RELEASE" -n "$NAMESPACE" 2>/dev/null || true
    ok "Release deleted (image and namespace preserved)"
}

# --- main ---
BUILD=true DEPLOY=true
for arg in "$@"; do
    case "$arg" in
        --build-only)  DEPLOY=false ;;
        --deploy-only) BUILD=false ;;
        --status)  check_prereqs; show_status; exit 0 ;;
        --logs)    check_prereqs; show_logs; exit 0 ;;
        --delete)  check_prereqs; delete_release; exit 0 ;;
        -h|--help)
            echo "Usage: $0 [--build-only|--deploy-only|--status|--logs|--delete]"
            exit 0
            ;;
        *) fail "Unknown flag: $arg" ;;
    esac
done

check_prereqs
$BUILD  && build_image
$DEPLOY && { load_env_deploy; deploy_helm; }
verify
show_status
