#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Uso:
  ./scripts/render-personal-demo.sh --terraform-dir CAMINHO

Renderiza o overlay personal-demo usando a identidade AWS ativa e os outputs
do state Terraform personal-demo. Execute depois do terraform apply e antes de
permitir a sincronizacao final do ArgoCD.
USAGE
}

TERRAFORM_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --terraform-dir)
      TERRAFORM_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Argumento desconhecido: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

[[ -n "$TERRAFORM_DIR" ]] || { usage >&2; exit 1; }
[[ -d "$TERRAFORM_DIR" ]] || { echo "Diretorio Terraform inexistente: $TERRAFORM_DIR" >&2; exit 1; }

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
AWS_REGION_VALUE="$(aws configure get region || true)"
AWS_REGION_VALUE="${AWS_REGION_VALUE:-${AWS_REGION:-us-east-1}}"
SQS_URL="$(terraform -chdir="$TERRAFORM_DIR" output -raw sqs_queue_url)"
REDIS_ENDPOINT="$(terraform -chdir="$TERRAFORM_DIR" output -raw redis_endpoint)"
DYNAMODB_TABLE="$(terraform -chdir="$TERRAFORM_DIR" output -raw dynamodb_table_name)"
DEPLOYMENT_PROFILE="$(terraform -chdir="$TERRAFORM_DIR" output -raw deployment_profile)"
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION_VALUE}.amazonaws.com"

[[ "$ACCOUNT_ID" =~ ^[0-9]{12}$ ]] || { echo "Account ID AWS invalido." >&2; exit 1; }
[[ "$DEPLOYMENT_PROFILE" == "personal" ]] || { echo "O state ativo nao usa deployment_profile=personal." >&2; exit 1; }
[[ "$SQS_URL" == https://sqs.* ]] || { echo "Output sqs_queue_url invalido." >&2; exit 1; }
[[ -n "$REDIS_ENDPOINT" ]] || { echo "Output redis_endpoint vazio." >&2; exit 1; }
[[ -n "$DYNAMODB_TABLE" ]] || { echo "Output dynamodb_table_name vazio." >&2; exit 1; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for service in auth flag targeting evaluation analytics; do
  file="$ROOT_DIR/apps/$service/overlays/personal-demo/kustomization.yaml"
  sed -E -i.bak "s#[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com#${ECR_REGISTRY}#g" "$file"
  rm -f "$file.bak"
done

evaluation_file="$ROOT_DIR/apps/evaluation/overlays/personal-demo/kustomization.yaml"
analytics_file="$ROOT_DIR/apps/analytics/overlays/personal-demo/kustomization.yaml"

sed -E -i.bak "s#value: rediss://[^:]+:6379#value: rediss://${REDIS_ENDPOINT}:6379#" "$evaluation_file"
rm -f "$evaluation_file.bak"

for file in "$evaluation_file" "$analytics_file"; do
  sed -E -i.bak "s#value: https://sqs\.[^/]+\.amazonaws\.com/[0-9]{12}/[^[:space:]]+#value: ${SQS_URL}#" "$file"
  rm -f "$file.bak"
done

sed -E -i.bak "s#value: ToggleMasterAnalytics-[^[:space:]]+#value: ${DYNAMODB_TABLE}#" "$analytics_file"
rm -f "$analytics_file.bak"

if rg -n "000000000000|REPLACE_WITH" "$ROOT_DIR/apps" --glob '*/overlays/personal-demo/kustomization.yaml'; then
  echo "O overlay personal-demo ainda contem placeholders." >&2
  exit 1
fi

echo "Overlay personal-demo renderizado para a conta ${ACCOUNT_ID} em ${AWS_REGION_VALUE}."
