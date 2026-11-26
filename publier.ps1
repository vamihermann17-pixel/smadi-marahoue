# Envoie le dossier smadi_github vers le dépôt GitHub smadi-marahoue.
# Avant : créer le dépôt VIDE et public sur https://github.com/new (nom : smadi-marahoue).
# Lancer : clic droit → « Exécuter avec PowerShell », ou dans PowerShell :
#   powershell -ExecutionPolicy Bypass -File publier.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$depot = "https://github.com/vamihermann17-pixel/smadi-marahoue.git"

# Auteur des commits (demandé une seule fois)
if (-not (git config --global user.name)) {
    $nom = Read-Host "Votre nom (auteur des commits)"
    git config --global user.name "$nom"
}
if (-not (git config --global user.email)) {
    $mail = Read-Host "Votre adresse e-mail GitHub"
    git config --global user.email "$mail"
}

if (-not (Test-Path ".git")) { git init -b main }
git add .
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m "SMADI Marahoué : calcul Earth Engine, page web et mise à jour mensuelle"
}
if (-not (git remote)) { git remote add origin $depot }
git push -u origin main

Write-Host ""
Write-Host "Envoi terminé. Étapes suivantes sur GitHub (dépôt smadi-marahoue) :"
Write-Host " 1. Settings > Secrets and variables > Actions > secret EE_SERVICE_ACCOUNT_KEY (contenu du JSON)"
Write-Host " 2. Onglet Variables : EE_PROJECT = smadi-2026"
Write-Host " 3. Settings > Pages > Source : GitHub Actions"
Write-Host " 4. Actions > Mise à jour du SMADI > Run workflow"
Write-Host "Page : https://vamihermann17-pixel.github.io/smadi-marahoue/"
