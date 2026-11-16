# Runbook — Taxa de erros HTTP 5xx acima de 5%

**Alerta:** `Taxa de erros HTTP 5xx acima de 5%` (Grafana, pasta ToggleMaster)
**Severidade:** critical, abre incidente no PagerDuty e notifica o Discord.

## O que significa
Mais de 5% das requisições de um microsserviço terminaram com erro (span de
servidor com código HTTP entre 500 e 599) nos últimos 2 minutos, com tráfego
acima de 0,1 req/s. A regra consulta `http_response_status_code` e usa
`http_status_code` como compatibilidade para instrumentações OpenTelemetry
legadas. Métrica: `traces_span_metrics_calls_total` (spanmetrics do OTel
Collector), armazenada no Prometheus.

## Validação no Prometheus

Depois de gerar uma resposta 5xx, abra o Prometheus com
`kubectl -n observability port-forward svc/prometheus-server 9090:80` e consulte:

```promql
count by (service_name, http_response_status_code, http_status_code) (
  traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}
)
```

O serviço deve apresentar `http_response_status_code` entre `500` e `599`, ou
`http_status_code` para uma instrumentação legada. Para validar exatamente o
numerador usado pelo alerta:

```promql
sum by (service_name) (
  rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER", http_response_status_code=~"5[0-9]{2}"}[2m])
  or
  rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER", http_response_status_code="", http_status_code=~"5[0-9]{2}"}[2m])
)
```

## Ação automática (self-healing)
O Grafana chama o `self-healer` (namespace `observability`), que executa
`kubectl rollout restart deployment/<service_name> -n togglemaster-homolog`.
Há um cooldown de 5 minutos por deployment para evitar loop de restart.

## Se o alerta continuar disparando após o restart
1. Ver os logs do self-healer:
   `kubectl -n observability logs deploy/self-healer`
2. Ver os logs do serviço no Grafana (Explore → Loki):
   `{k8s_namespace_name="togglemaster-homolog", k8s_container_name="<servico>"} |~ "(?i)error"`
3. Abrir o trace com erro no New Relic (APM → serviço → Errors) e seguir o span
   até a dependência que falhou (banco, Redis, outro serviço).
4. Dependência fora do ar (RDS, ElastiCache): verificar no console da AWS.
5. Regressão de código: reverter a tag da imagem no overlay `homolog`
   (Pull Request no togglemaster-gitops); o ArgoCD aplica.
