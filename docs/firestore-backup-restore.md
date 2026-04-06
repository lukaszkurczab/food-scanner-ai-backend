# Firestore Backup and Restore Runbook

## Scope

This runbook defines minimum backup and recovery operations for production Firestore data used by `fitaly-backend`.

## Ownership

- Primary owner: Backend Engineer on duty
- Backup owner: Ops/Engineering Lead

## Frequency

- Daily automated backup export
- Weekly verification that the latest backup exists and is readable
- Monthly restore drill to a non-production project

## Prerequisites

- Google Cloud project with Firestore enabled
- `gcloud` CLI installed and authenticated
- IAM roles:
  - source project: read/export permissions for Firestore
  - backup bucket: write permissions
  - restore target project: import permissions for Firestore
- Dedicated GCS bucket for backups (for example `gs://fitaly-firestore-backups`)

## Backup Procedure (Export)

1. Set variables:

```bash
export SOURCE_PROJECT_ID="<prod-project-id>"
export BACKUP_BUCKET="gs://fitaly-firestore-backups"
export BACKUP_PREFIX="firestore"
export BACKUP_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
```

2. Start Firestore export:

```bash
gcloud firestore export "${BACKUP_BUCKET}/${BACKUP_PREFIX}/${BACKUP_STAMP}" \
  --project="${SOURCE_PROJECT_ID}"
```

3. Record the export path and operation id in the incident/ops log.

4. Verify export succeeded:

```bash
gcloud firestore operations list --project="${SOURCE_PROJECT_ID}" \
  --filter="metadata.outputUriPrefix:${BACKUP_BUCKET}/${BACKUP_PREFIX}/${BACKUP_STAMP}"
```

## Restore Procedure (Import to Staging/Recovery Project)

1. Set variables:

```bash
export TARGET_PROJECT_ID="<staging-or-recovery-project-id>"
export EXPORT_PATH="gs://fitaly-firestore-backups/firestore/<backup-stamp>"
```

2. Start Firestore import:

```bash
gcloud firestore import "${EXPORT_PATH}" --project="${TARGET_PROJECT_ID}"
```

3. Verify operation status:

```bash
gcloud firestore operations list --project="${TARGET_PROJECT_ID}" \
  --filter="metadata.inputUriPrefix:${EXPORT_PATH}"
```

4. Run backend health check and selected smoke checks against the restored environment.

## Monthly Restore Drill Checklist

- Pick the latest successful export from the previous 7 days.
- Import into a non-production target project.
- Validate:
  - `/api/v1/health` responds `200`
  - critical collections exist and are readable
  - app login and at least one read path works on restored data
- Document:
  - backup stamp used
  - restore duration
  - issues and follow-up actions

## Retention

- Keep at least 30 days of daily backups.
- Keep at least 3 monthly backup snapshots for long-tail recovery.

## Failure Handling

- If export fails: retry once, then escalate to incident channel.
- If restore fails: capture operation id, error output, and open a production-risk incident.
- Do not run restore into production without incident commander approval.
