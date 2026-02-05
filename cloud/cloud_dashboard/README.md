# Omega Cloud Dashboard

Deploy the Flask dashboard to Cloud Run for "access from anywhere."

## Prerequisites

1. Google Cloud project with Cloud Run enabled
2. Cloud SQL instance (PostgreSQL)
3. `.env` file with database credentials

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DB_DSN` | Yes* | Direct PostgreSQL connection string |
| `DB_INSTANCE_CONNECTION_NAME` | Yes* | Cloud SQL instance (alternative to DSN) |
| `DB_USER` | If using Cloud SQL | Database user |
| `DB_PASS` | If using Cloud SQL | Database password |
| `DB_NAME` | If using Cloud SQL | Database name |
| `OMEGA_ADMIN_TOKEN` | Recommended | Token for admin operations |
| `OMEGA_JOBS_BUCKET` | Optional | GCS bucket for job artifacts |

*Either `DB_DSN` or `DB_INSTANCE_CONNECTION_NAME` + `DB_PASS` is required.

## Deployment

```bash
# From the SubtitleWorkflow directory:
bash scripts/deploy_cloud_dashboard.sh
```

The script will:
1. Build the Docker image using Cloud Build
2. Deploy to Cloud Run
3. Print the dashboard URL

## Testing Locally

If you have Docker installed:

```bash
docker build -f cloud/cloud_dashboard/Dockerfile -t omega-dashboard:test .

docker run -p 8080:8080 \
  -e DB_DSN="postgresql://user:pass@host/omega_db" \
  omega-dashboard:test
```

Then open http://localhost:8080

## Health Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/healthz` | Cloud Run liveness probe |
| `/health` | Liveness probe (tests DB connection) |
| `/ready` | Startup probe (verifies schema) |

## After Deployment

1. Test: `curl https://YOUR-DASHBOARD-URL/healthz`
2. Update DNS: Point `dashboard.azotus.io` to the Cloud Run URL
3. Access from anywhere!
