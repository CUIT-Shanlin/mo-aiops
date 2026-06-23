# mo-chat-aiops Helm Chart

该 chart 用于把 `mo-chat-aiops` 部署到 Kubernetes。

## 前置条件

- 已构建并推送 AIOps 应用镜像。
- 已准备 PostgreSQL 和 Redis，并能被 AIOps Pod 访问。
- Prometheus、Loki、Tempo 已部署，或 `projectsConfig.content` 中关闭/调整对应数据源。
- 当前 MVP 使用进程内指标窗口和 Trace cache，默认 `replicaCount=1` 且 uvicorn `--workers 1`。

## 安装

```bash
helm upgrade --install mo-chat-aiops ./deploy/mo-chat-aiops \
  --namespace aiops \
  --create-namespace \
  --set image.repository=registry.example.com/mo-chat-aiops \
  --set image.tag=0.1.0 \
  --set secret.data.DATABASE_URL='postgresql+asyncpg://aiops:password@postgres.aiops.svc.cluster.local:5432/mo_chat_aiops' \
  --set secret.data.REDIS_URL='redis://redis.aiops.svc.cluster.local:6379' \
  --set secret.data.JWT_SECRET='replace-with-strong-secret-at-least-32-bytes' \
  --set secret.data.ADMIN_PASSWORD_HASH='plain:replace-me' \
  --set secret.data.LLM_API_KEY='replace-me'
```

生产环境建议使用单独的 values 文件，不要把敏感值写在命令历史中：

```bash
helm upgrade --install mo-chat-aiops ./deploy/mo-chat-aiops \
  --namespace aiops \
  --create-namespace \
  -f values-prod.yaml
```

## 使用已有 Secret

```yaml
secret:
  create: false
  name: mo-chat-aiops-secret
```

已有 Secret 至少需要包含：

- `DATABASE_URL`
- `REDIS_URL`
- `JWT_SECRET`
- `ADMIN_PASSWORD_HASH`
- `LLM_API_KEY`

## 配置被监控项目

通过 `projectsConfig.content` 写入 `projects.yaml`：

```yaml
projectsConfig:
  content: |
    default_project: mochat-prod

    projects:
      mochat-prod:
        name: "mo-chat 生产"
        metric_profile: java
        enabled: true
        datasources:
          prometheus:
            base_url: "http://prometheus.monitoring.svc.cluster.local:9090"
            verify_ssl: false
          loki:
            base_url: "http://loki.monitoring.svc.cluster.local:3100"
            auth_type: NoAuth
            verify_ssl: false
          tempo:
            base_url: "http://tempo.monitoring.svc.cluster.local:3200"
            verify_ssl: false
          kubernetes:
            mode: in_cluster
            namespaces: ["production"]
            verify_ssl: true
```

## RBAC

默认创建只读 ClusterRole/ClusterRoleBinding，允许查询 pods、pods/log、nodes、namespaces、deployments。

如果只允许访问当前 namespace：

```yaml
rbac:
  create: true
  clusterWide: false
```

如果使用外部 token 访问业务集群：

```yaml
rbac:
  create: false

projectsConfig:
  content: |
    default_project: mochat-prod
    projects:
      mochat-prod:
        name: "mo-chat 生产"
        metric_profile: java
        enabled: true
        datasources:
          kubernetes:
            mode: token
            api_server: "https://k8s.example.com:6443"
            token: "${K8S_TOKEN}"
            namespaces: ["production"]
            verify_ssl: true
```

## 验证

```bash
helm lint ./deploy/mo-chat-aiops
helm template mo-chat-aiops ./deploy/mo-chat-aiops --namespace aiops
kubectl -n aiops get pods -l app.kubernetes.io/name=mo-chat-aiops
kubectl -n aiops port-forward svc/mo-chat-aiops 8000:8000
curl -s http://localhost:8000/health
```
