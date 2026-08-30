# ToggleMaster GitOps

Fonte unica de verdade dos workloads do ToggleMaster no Kubernetes. O ArgoCD reconcilia continuamente este repositorio, corrige drift (`selfHeal`) e remove recursos que deixarem de existir no Git (`prune`). Alteracoes operacionais devem ser feitas por Pull Request; comandos imperativos como `kubectl set image` e `kubectl scale` nao fazem parte do fluxo normal.

## Estrutura

```text
togglemaster-gitops/
├── apps/
│   ├── auth/
│   ├── flag/
│   ├── targeting/
│   ├── evaluation/
│   └── analytics/
│       ├── base/
│       └── overlays/homolog/
├── platform/
│   └── ingress/
│       ├── base/
│       └── overlays/homolog/
├── argocd/
│   ├── projects/
│   ├── applications/
│   ├── kustomization.yaml
│   └── root-application.yaml
└── README.md
```

As bases foram migradas dos manifests existentes em `/Users/jeanmoreiraact/Documents/togglemaster/k8s` e conferidas contra as variaveis e endpoints implementados nos cinco servicos.

## O que cada aplicacao possui

- Deployment com estrategia `RollingUpdate`, `maxUnavailable: 0` e historico de revisoes.
- Service interno `ClusterIP`.
- ConfigMap para configuracao nao sensivel.
- Referencias `secretKeyRef`; nenhum valor secreto fica neste repositorio.
- `readinessProbe` e `livenessProbe` no endpoint `/health`.
- CPU e memoria em `requests` e `limits`.
- Imagem ECR com tag imutavel no formato `sha-<commit>`; `latest` nao e usado.
- Um overlay Kustomize `homolog`, usado como ambiente unico para reduzir consumo no AWS Academy.
- HPA para `evaluation` e `analytics`, preservado dos manifests originais.

Todos os workloads sao reconciliados no namespace `togglemaster-homolog`. Os Services usam nomes estaveis, portanto a descoberta interna continua usando enderecos como `http://auth-service:8001`.

## Valores do ambiente

Os overlays de homologacao usam os outputs provisionados pelo Terraform:

- conta AWS Academy `935091649608` nos cinco repositorios ECR;
- tabela DynamoDB `ToggleMasterAnalytics-homolog`;
- Redis `master.togglemaster-homolog-redis.e30slj.use1.cache.amazonaws.com:6379`;
- SQS `togglemaster-homolog-evaluation-events` em `us-east-1`.

Somente `sha-0000000` permanece como valor inicial. A pipeline de cada servico
o substitui pela tag imutavel da imagem publicada no ECR.

Os nomes ECR ja correspondem ao repositorio de infraestrutura:

```yaml
images:
  - name: togglemaster-auth-service
    newName: 935091649608.dkr.ecr.us-east-1.amazonaws.com/togglemaster-homolog/auth-service
    newTag: sha-a1b2c3d
```

O pipeline de cada servico publica a imagem com uma tag `sha-<commit>` e atualiza o overlay `homolog`. Este e o unico destino de deploy do desafio; nao existe promocao para outro ambiente.

## Contrato de Secrets

Os Secrets devem existir no namespace do ambiente antes de os Deployments ficarem saudaveis:

| Aplicacao | Secret | Chaves obrigatorias |
| --- | --- | --- |
| auth | `auth-service-secret` | `DATABASE_URL`, `MASTER_KEY` |
| flag | `flag-service-secret` | `DATABASE_URL` |
| targeting | `targeting-service-secret` | `DATABASE_URL` |
| evaluation | `evaluation-service-secret` | `SERVICE_API_KEY` |

`AWS_SQS_URL` pertence ao ConfigMap do analytics porque identifica uma fila e
nao concede acesso a ela. As credenciais continuam vindo da cadeia padrao do SDK
AWS e da LabRole dos nodes.

Use External Secrets Operator, Sealed Secrets ou outro mecanismo declarativo aprovado para materializar esses objetos. Nao adicione um `Secret` com valores em texto, Base64 ou credenciais temporarias do AWS Academy ao Git. Os valores encontrados nos manifests legados nao foram copiados e devem ser rotacionados se ja tiverem sido expostos ou commitados.

Os SDKs AWS de `evaluation` e `analytics` usam a cadeia padrao de credenciais e, no AWS Academy, herdam as permissoes disponiveis aos nodes pela LabRole. Credenciais AWS temporarias nao sao armazenadas nos Pods.

## Validacao local

Renderize todos os overlays antes do commit:

```bash
for app in auth flag targeting evaluation analytics; do
  kubectl kustomize "apps/${app}/overlays/homolog" >/dev/null
done

kubectl kustomize platform/ingress/overlays/homolog >/dev/null
kubectl kustomize argocd >/dev/null
```

Para uma validacao contra a API do cluster sem persistir alteracoes:

```bash
kubectl kustomize apps/auth/overlays/homolog | \
  kubectl apply --dry-run=server -f -
```

## Bootstrap do ArgoCD

O ArgoCD e instalado pelo repositorio `togglemaster-infrastructure` usando o
provider Helm do Terraform. O mesmo `terraform apply` cria a root Application
`togglemaster-root`; nao execute `kubectl apply` no fluxo normal. O arquivo
`argocd/root-application.yaml` permanece como referencia declarativa do contrato
entre os repositorios.

A root Application sincroniza o AppProject e o ApplicationSet. O ApplicationSet
gera seis Applications no ambiente unico: cinco servicos e o Ingress
compartilhado.

Se este repositorio for privado, cadastre previamente a credencial de repositorio no ArgoCD. A credencial tambem deve ser gerenciada declarativamente e nao deve ser commitada em texto puro.

## Dependencias do cluster

- O HPA de `evaluation` e `analytics` requer Metrics Server.
- O Ingress compartilhado requer um controller `ingress-nginx` e a classe `nginx`.
- Os endpoints Redis, RDS, SQS e DynamoDB devem vir dos outputs do Terraform do mesmo ambiente.
- A policy imutavel dos repositorios ECR impede sobrescrever uma tag `sha-*`; publique uma nova tag para cada build.

Enquanto Metrics Server ou ingress-nginx nao estiverem instalados, remova declarativamente os recursos dependentes da kustomization ou adicione os componentes de plataforma ao GitOps. Nao corrija o cluster manualmente sem registrar a mudanca no Git.

## Recuperacao e rollback

Para rollback, reverta o commit que alterou a tag ou configuracao e faça merge. O ArgoCD reconciliara o estado anterior. Evite usar rollback imperativo no cluster, pois o `selfHeal` restaurara o estado registrado no Git.
