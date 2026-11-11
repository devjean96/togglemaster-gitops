#!/usr/bin/env bash
# Roteiro da "Prova Real" (video): provoca a falha no auth-service e acompanha
# o self-healing. Requer kubectl apontando para o EKS e o gerador de trafego rodando.
set -euo pipefail
NS=togglemaster-homolog

MASTER_KEY=$(kubectl -n "$NS" get secret auth-service-secret -o jsonpath='{.data.MASTER_KEY}' | base64 -d)

echo ">> Pods do auth-service ANTES:"
kubectl -n "$NS" get pods -l app.kubernetes.io/name=auth-service

echo ">> Ativando a falha simulada (o /validate passa a responder 500)..."
kubectl -n "$NS" port-forward svc/auth-service 18001:8001 >/dev/null 2>&1 &
PF=$!; trap 'kill $PF 2>/dev/null || true' EXIT; sleep 3
curl -s -X POST -H "Authorization: Bearer ${MASTER_KEY}" http://localhost:18001/admin/chaos; echo

echo ">> Aguarde ~2-3 min: alerta Pending -> Firing -> PagerDuty/Discord -> self-healer."
echo ">> Acompanhando o self-healer e os pods (Ctrl+C para sair):"
kubectl -n observability logs -f deploy/self-healer --since=1m &
kubectl -n "$NS" get pods -l app.kubernetes.io/name=auth-service -w
