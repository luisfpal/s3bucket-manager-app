# Testing and Continuous Integration

Buckets Explorer uses **pytest** for backend tests and **`npm run build`** as the frontend compile gate. Coverage reports upload to [Codecov](https://app.codecov.io/gh/luisfpal/s3bucket-manager-app) on every CI run.

There is no Jest test suite—the Codecov onboarding wizard may suggest Jest; ignore that for this project.

## Run checks locally

From the repository root:

```bash
./k8s/app.sh verify
```

This runs the same steps as the GitHub Actions `verify` job:

1. `bash -n` on `k8s/app.sh` and `k8s/infra.sh`
2. `pip install ./backend[dev]` and `pytest --cov=storage`
3. `npm run build` in `frontend/`

### Backend only

```bash
cd backend
python -m pip install "./[dev]"
DJANGO_DEBUG=True python -m pytest --cov=storage --cov-report=term-missing
```

Tests live in `backend/storage/tests.py` (auth, admin, OAuth pipeline, RGWSquared client, bucket lifecycle, sync service, crypto, user provisioning).

### Frontend only

```bash
cd frontend
npm ci
npm run build
```

## GitHub Actions

Workflow: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

| Job | When | What |
| --- | --- | --- |
| `verify` | Every push/PR to `dev` and `main` | pytest + coverage.xml + frontend build + Codecov upload |
| `deploy-dev` | Push to `dev` only (after `verify`) | Build and push images to GHCR |
| `deploy-dev-k8s` | After `deploy-dev` | Roll out to dev K3s via self-hosted runner |

Production deployment remains **manual** (`kubectl` ladder). See [production-deployment.md](production-deployment.md).

## Codecov setup (one-time)

1. Link the repo at [codecov.io](https://codecov.io) (this project currently uses `luisfpal/s3bucket-manager-app` for coverage uploads during development).
2. Install the [Codecov GitHub App](https://github.com/apps/codecov) on the repository.
3. Add repository secret `CODECOV_TOKEN` (Settings → Secrets → Actions) with the upload token from Codecov → Settings → General.

After the first green CI run, open the Codecov dashboard for coverage history and line-level reports.

Future maintainers should point Codecov at their own GitHub organisation or fork and update the workflow token accordingly.

## Dev bash scripts vs GitHub Actions

Both paths should pass the same `verify` checks before deploy:

| Step | Local (`k8s/app.sh`) | GitHub Actions |
| --- | --- | --- |
| Tests + build | `./app.sh verify` | `verify` job |
| Build + push images | `./app.sh deploy --rebuild` or `./app.sh backend` | `deploy-dev` job |
| Roll out to cluster | `kubectl` via tunnel/kubeconfig | `deploy-dev-k8s` on self-hosted runner |

The self-hosted runner (`bucket-explorer-runner`) is installed with `k8s/ci.sh` and has in-cluster `kubectl` access. It runs only the `deploy-dev-k8s` job after images are pushed to GHCR; the `verify` job uses GitHub-hosted `ubuntu-latest`.

## Before production image push

Run `./k8s/app.sh verify` (or confirm CI is green on the commit you are deploying). Do not push production images from a commit that fails tests.

## Dependencies

Backend Python dependencies are declared in [`backend/pyproject.toml`](../backend/pyproject.toml). Production images install with `pip install .`; dev/CI install with `pip install ./backend[dev]`.
