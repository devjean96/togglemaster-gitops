# Runbook — Taxa de erros HTTP 5xx acima de 5%

**Alerta:** `Taxa de erros HTTP 5xx acima de 5%` (Grafana, pasta ToggleMaster)
**Severidade:** critical, abre incidente no PagerDuty e notifica o Discord.

## O que significa
Mais de 5% das requisições de um microsserviço terminaram com erro (span de
servidor com status `ERROR`, ou seja HTTP 5xx) nos últimos 2 minutos, com tráfego
acima de 0,1 req/s. Métrica: `traces_span_metrics_calls_total` (spanmetrics do
OTel Collector), armazenada no Prometheus.

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
