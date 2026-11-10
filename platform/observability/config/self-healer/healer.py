"""Self-healer do ToggleMaster (Runbook Automation).

Recebe o webhook do Grafana Alerting e, para cada alerta FIRING com o label
remediation=rollout-restart, executa o equivalente a

    kubectl rollout restart deployment/<service_name> -n togglemaster-homolog

falando direto com a API do Kubernetes (ServiceAccount + Role com permissao
apenas de get/patch em Deployments). So usa a biblioteca padrao do Python, entao
roda na imagem oficial python:alpine sem build de imagem propria.

Protecoes:
  * token Bearer compartilhado com o Grafana (SELF_HEALER_TOKEN);
  * lista de Deployments permitidos (ALLOWED_DEPLOYMENTS);
  * cooldown por Deployment (COOLDOWN_SECONDS) para nao entrar em loop de
    restart; se o problema persistir, o incidente segue aberto no PagerDuty e
    um humano assume.
"""

import hmac
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
K8S_API = "https://{}:{}".format(
    os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc"),
    os.environ.get("KUBERNETES_SERVICE_PORT", "443"),
)
TARGET_NAMESPACE = os.environ.get("TARGET_NAMESPACE", "togglemaster-homolog")
ALLOWED = {d.strip() for d in os.environ.get("ALLOWED_DEPLOYMENTS", "").split(",") if d.strip()}
COOLDOWN_SECONDS = int(os.environ.get("COOLDOWN_SECONDS", "300"))
TOKEN = os.environ.get("SELF_HEALER_TOKEN", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
PORT = int(os.environ.get("PORT", "8080"))

_last_restart = {}
_lock = threading.Lock()


def log(level, message, **fields):
    """Log estruturado em JSON (uma linha), facil de filtrar no Loki."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "level": level,
        "logger": "self-healer",
        "msg": message,
    }
    record.update(fields)
    print(json.dumps(record, ensure_ascii=False), flush=True)


def _k8s_request(method, path, body=None, content_type="application/json"):
    with open(f"{SA_DIR}/token", encoding="utf-8") as token_file:
        sa_token = token_file.read().strip()
    context = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(K8S_API + path, data=data, method=method)
    request.add_header("Authorization", f"Bearer {sa_token}")
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", content_type)
    with urllib.request.urlopen(request, context=context, timeout=10) as response:
        return json.loads(response.read() or b"{}")


def rollout_restart(deployment, reason):
    """Mesmo patch que o 'kubectl rollout restart' aplica no pod template."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": now,
                        "togglemaster.io/restarted-by": "self-healer",
                        "togglemaster.io/restart-reason": reason[:250],
                    }
                }
            }
        }
    }
    path = f"/apis/apps/v1/namespaces/{TARGET_NAMESPACE}/deployments/{deployment}"
    if DRY_RUN:
        log("INFO", "dry-run: rollout restart nao executado", deployment=deployment)
        return
    _k8s_request("PATCH", path, patch, "application/strategic-merge-patch+json")


def notify_discord(content):
    if not DISCORD_WEBHOOK_URL:
        return
    request = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=json.dumps({"username": "ToggleMaster Self-Healer", "content": content}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "togglemaster-self-healer/1.0"},
    )
    try:
        urllib.request.urlopen(request, timeout=10).close()
    except (urllib.error.URLError, TimeoutError) as error:
        log("WARN", "falha ao notificar o Discord", error=str(error))


def handle_alert(alert):
    labels = alert.get("labels", {})
    status = alert.get("status", "")
    deployment = labels.get("service_name", "")
    alertname = labels.get("alertname", "")

    if status == "resolved":
        log("INFO", "alerta resolvido", alertname=alertname, deployment=deployment)
        return "resolved"

    if labels.get("remediation") != "rollout-restart":
        log("INFO", "alerta sem remediacao automatica; ignorado", alertname=alertname)
        return "ignored"

    if deployment not in ALLOWED:
        log("WARN", "deployment fora da allowlist; nenhuma acao", deployment=deployment)
        return "not-allowed"

    with _lock:
        elapsed = time.monotonic() - _last_restart.get(deployment, -COOLDOWN_SECONDS)
        if elapsed < COOLDOWN_SECONDS:
            log("WARN", "cooldown ativo; restart nao repetido (escalar para humano)",
                deployment=deployment, seconds_remaining=int(COOLDOWN_SECONDS - elapsed))
            return "cooldown"
        _last_restart[deployment] = time.monotonic()

    reason = alert.get("annotations", {}).get("summary", alertname)
    log("WARN", "SELF-HEALING: executando rollout restart", deployment=deployment,
        namespace=TARGET_NAMESPACE, alertname=alertname, reason=reason)
    try:
        rollout_restart(deployment, reason)
    except urllib.error.HTTPError as error:
        log("ERROR", "falha no rollout restart", deployment=deployment,
            status=error.code, body=error.read().decode(errors="replace")[:500])
        with _lock:
            _last_restart.pop(deployment, None)
        notify_discord(f"❌ **Self-healing falhou** para `deployment/{deployment}` (HTTP {error.code}). Intervenção humana necessária.")
        return "error"
    except (urllib.error.URLError, OSError) as error:
        log("ERROR", "falha ao falar com a API do Kubernetes", deployment=deployment, error=str(error))
        with _lock:
            _last_restart.pop(deployment, None)
        return "error"

    log("INFO", "rollout restart aplicado com sucesso", deployment=deployment, namespace=TARGET_NAMESPACE)
    notify_discord(
        f"🤖 **Self-healing executado**\n"
        f"Ação: `kubectl rollout restart deployment/{deployment} -n {TARGET_NAMESPACE}`\n"
        f"Motivo: {reason}\n"
        f"Próximo restart automático permitido em {COOLDOWN_SECONDS // 60} min."
    )
    return "restarted"


class Handler(BaseHTTPRequestHandler):
    server_version = "togglemaster-self-healer/1.0"

    def _reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._reply(200, {"status": "ok"})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/hooks/grafana":
            self._reply(404, {"error": "not found"})
            return
        expected = f"Bearer {TOKEN}"
        received = self.headers.get("Authorization", "")
        if not TOKEN or not hmac.compare_digest(received.encode(), expected.encode()):
            log("WARN", "webhook recusado: token invalido", client=self.client_address[0])
            self._reply(401, {"error": "unauthorized"})
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._reply(400, {"error": "invalid json"})
            return

        log("INFO", "webhook recebido do Grafana", status=payload.get("status"),
            alerts=len(payload.get("alerts", [])), group_key=payload.get("groupKey", ""))
        results = [handle_alert(alert) for alert in payload.get("alerts", [])]
        self._reply(200, {"results": results})

    def log_message(self, fmt, *args):  # silencia o access log padrao
        pass


def main():
    if not TOKEN:
        log("ERROR", "SELF_HEALER_TOKEN nao definido")
        sys.exit(1)
    log("INFO", "self-healer iniciado", port=PORT, namespace=TARGET_NAMESPACE,
        allowed=sorted(ALLOWED), cooldown_seconds=COOLDOWN_SECONDS, dry_run=DRY_RUN)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
