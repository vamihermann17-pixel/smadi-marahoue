#!/usr/bin/env bash
# Connexion sans clé de GitHub Actions à Google Earth Engine (Workload Identity Federation).
# À lancer une fois dans Cloud Shell (console Google Cloud), projet Earth Engine actif :
#   bash <(curl -sL https://raw.githubusercontent.com/vamihermann17-pixel/smadi-marahoue/main/scripts/config_gcp_sans_cle.sh)
# Variables modifiables : P (projet), R (dépôt GitHub propriétaire/nom).
# Le script peut être relancé sans risque : ce qui existe déjà est conservé.

P="${P:-smadi-2026}"
R="${R:-vamihermann17-pixel/smadi-marahoue}"
SA_NOM=smadi-github
SA="$SA_NOM@$P.iam.gserviceaccount.com"
POOL=github
PROV=smadi-marahoue

set -u
gcloud config set project "$P" >/dev/null
N=$(gcloud projects describe "$P" --format='value(projectNumber)')
echo "== Projet $P ($N), dépôt $R"

echo "== Services"
gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  earthengine.googleapis.com serviceusage.googleapis.com

echo "== Compte de service $SA"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NOM" --display-name="SMADI - GitHub Actions"

for role in roles/earthengine.viewer roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding "$P" --member="serviceAccount:$SA" --role="$role" \
    --condition=None --quiet >/dev/null && echo "   rôle $role"
done

echo "== Pool d'identités $POOL"
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "$POOL" --location=global --display-name="GitHub Actions"

echo "== Fournisseur $PROV (seul le dépôt $R est accepté)"
gcloud iam workload-identity-pools providers describe "$PROV" --location=global \
  --workload-identity-pool="$POOL" >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc "$PROV" --location=global \
    --workload-identity-pool="$POOL" --display-name="smadi-marahoue" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition="assertion.repository=='$R'"

echo "== Autorisation du dépôt à utiliser $SA"
gcloud iam service-accounts add-iam-policy-binding "$SA" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$N/locations/global/workloadIdentityPools/$POOL/attribute.repository/$R" \
  --quiet >/dev/null && echo "   ok"

echo
echo "== À mettre dans GitHub (Settings > Secrets and variables > Actions > Variables) :"
echo "GCP_WIF_PROVIDER=projects/$N/locations/global/workloadIdentityPools/$POOL/providers/$PROV"
echo "GCP_SERVICE_ACCOUNT=$SA"
