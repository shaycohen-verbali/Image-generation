# Render operations record

## Release 2 desired topology

Release 2 code is prepared for two independently restartable Render services:

| Service | Start command | Role | Initial capacity |
|---|---|---|---:|
| `image-gen-api` | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` | `PROCESS_ROLE=web` | 2 fixed instances |
| `image-gen-worker` | `python -m app.worker` | `PROCESS_ROLE=worker` | 1 fixed instance |

The web service uses `/healthz` for a database readiness probe and `/livez` for
process liveness. The worker has no public HTTP listener. Neither service uses
a Render persistent disk or autoscaling in Release 2. The worker starts with
`WORKER_HARD_MAX_PARALLEL=2` and must be validated with
`WORKER_CLAIMING_ENABLED=false` before claims are enabled.

## Dashboard-managed exception

The Render MCP tools and Render CLI were not available in this development
environment, so the current service IDs, region, plan, repository branch,
custom domains, auto-deploy policy, and environment-variable key inventory
could not be inspected safely. No `render.yaml` was generated with guessed
values, and no Render service, environment variable, plan, instance count, or
deploy was changed.

Before applying the topology, an operator with Render access must record these
non-secret values here and confirm whether the existing web service can be
adopted without replacement:

- current web service ID and repository/branch
- region and current plan
- build, pre-deploy, and start commands
- health-check path and custom domains
- current auto-deploy policy
- current fixed instance count/scaling policy
- worker service ID after creation
- date/time and deploy IDs for the cutover gates

Do not copy secret values into this file. Keep provider credentials, Supabase
service-role keys, and Slack tokens in Render's secret environment settings.

## Safe cutover order

1. Deploy the code to the existing web service without changing its start command.
2. Create the worker with `WORKER_CLAIMING_ENABLED=false` and verify heartbeats.
3. Change only the existing web start command to uvicorn and verify `/livez`,
   `/healthz`, UI, and direct Slack control commands.
4. Enable worker claiming and run the approved no-cost canary.
5. Record the service IDs, health results, and rollback values above.

If any gate fails, set `WORKER_CLAIMING_ENABLED=false`; do not delete either
service or attach a persistent disk.
