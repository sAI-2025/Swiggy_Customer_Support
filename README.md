---
title: Swiggy Support
emoji: 🐳
colorFrom: orange
colorTo: red
sdk: docker
app_port: 7860
pinned: false
---

# Swiggy Support

Docker-based Django application for Hugging Face Spaces.

## Runtime configuration

Set these in Hugging Face Spaces variables or secrets as appropriate:

- `SECRET_KEY`
- `DEBUG`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `SESSION_COOKIE_SECURE`
- `CSRF_COOKIE_SECURE`
- `DJANGO_LOG_LEVEL`
- `MEDIA_ROOT`
- `STATIC_ROOT`

## Deployment

GitHub Actions deploys this repository to the Hugging Face Space after CI passes on `main`.

## Hugging Face setup

Configure the Space's Trusted Publishers so GitHub Actions can mint a short-lived token without storing a long-lived `HF_TOKEN` secret.

- Provider: `GitHub Actions`
- Claims:
  - `repository` = your GitHub repository
  - `branch` = `main`
  - `workflow` = `deploy.yml`

Set optional GitHub Actions variables for deployment target resolution:

- `HF_OIDC_RESOURCE` (preferred) or `HF_SPACE_ID` as `<namespace>/<space-name>`
- If neither variable is set, deployment defaults to `${{ github.repository }}`
