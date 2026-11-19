# Team Dashboard v1

A read-only live view of both cities for members and faculty, served only inside the VPN at **http://10.66.0.1:8088**. It complements Grafana, the alerting and analysis tool, with one page the team can present: the six sensors, their latest value, age and status, the last hour, and the Telegraf delay.

| Status | Rule (same as the Grafana alerts) |
| --- | --- |
| OK | Reading newer than 120 s and inside the Alert 1 range |
| Out of range | Latest reading outside the Alert 1 thresholds |
| Silent | No reading for more than 120 s (Alert 2) |
| No data | No reading in the last 30 days |

**Security design**
- The browser only talks to this server. It never sees InfluxDB or a token.
- The server runs three fixed Flux queries with a **read-only** token. No request parameter reaches a query. Schema names from the environment are validated at start-up.
- The container runs as `nobody` with a read-only filesystem, all capabilities dropped and `no-new-privileges`.
- Ports are bound to `10.66.0.1` (VPN) and `127.0.0.1` only. `vpn/firewall/docker-user.sh` limits 8088 to members and faculty.
- Responses carry a strict CSP, `X-Frame-Options: DENY` and `no-store`. There is no third-party JavaScript and no CDN.

Code: `server.py` (Python standard library), `static/` (HTML/CSS/JS). Tests: `tests/team-dashboard/` (4 tests against an InfluxDB API stand-in).

## Deploy next to the running `coil-checkin2` stack (Check-in 4)

This adds one container and changes nothing in the existing stack. It uses its own Compose project name (`projectd-dashboard`), so its `up`/`down` cannot touch `coil-checkin2`. Run as root from a checkout of the reviewed commit.

```bash
# 1. Find the Docker network of the running InfluxDB and the influx container name.
INFLUX_CT=coil-checkin2-influxdb-1
docker inspect "$INFLUX_CT" --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'

# 2. Confirm org, bucket and schema (expected: bucket sensors, measurement environment,
#    fields value and delay_ms, tag sensor_id). Run with the operator token if the CLI asks for one.
docker exec "$INFLUX_CT" influx org list
docker exec "$INFLUX_CT" influx bucket list
docker exec "$INFLUX_CT" influx query 'from(bucket:"sensors") |> range(start:-5m) |> limit(n:1)'

# 3. Create a READ-ONLY token for that bucket only.
BUCKET_ID=$(docker exec "$INFLUX_CT" influx bucket list --name sensors --hide-headers | awk '{print $1}')
docker exec "$INFLUX_CT" influx auth create --read-bucket "$BUCKET_ID" \
  --description "project-d team dashboard (read sensors)" --json | grep '"token"'

# 4. Store the settings outside Git.
sudo install -d -m 700 /etc/projectd
sudo install -m 600 /dev/null /etc/projectd/dashboard.env
sudoedit /etc/projectd/dashboard.env
#   LIVE_NETWORK=<network from step 1>
#   INFLUX_ORG=<org from step 2>
#   INFLUX_DASHBOARD_TOKEN=<token from step 3>
#   # only if step 2 showed different names:
#   # INFLUX_BUCKET=sensors  INFLUX_MEASUREMENT=environment  VALUE_FIELD=value  DELAY_FIELD=delay_ms

# 5. Start and check.
docker compose -p projectd-dashboard --env-file /etc/projectd/dashboard.env -f team-dashboard/compose.live.yml up -d
curl -s http://127.0.0.1:18088/api/summary | head -c 400; echo
```

Then open **http://10.66.0.1:8088** from a device on the VPN. If the page says *Data source unavailable*, look at `docker logs projectd-dashboard-team-dashboard-1`: `HTTP 401` means a wrong token or org, and `URLError` means the wrong network.

Rollback: `docker compose -p projectd-dashboard -f team-dashboard/compose.live.yml down` (removes only the dashboard container).

## On the dedicated VPS

The service `team-dashboard` is part of `deployment/contabo/compose.yml`. It uses `INFLUX_DASHBOARD_TOKEN` if set, otherwise Grafana's read-only token from `scoped_tokens.py`.

## Demo for the check-in (about 1 minute)
1. Open http://10.66.0.1:8088: six sensors, two cities, all OK, plus the delay per sensor (Mati versus Szczecin).
2. Stop one simulator (or run it with `--silence`). After 120 s the card turns **Silent**, at the same moment Grafana's Alert 2 fires.
3. Run a simulator with `--anomaly`. The card turns **Out of range**, matching Alert 1.
