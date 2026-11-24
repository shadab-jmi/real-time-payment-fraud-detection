# EC2 deployment with Caddy

This runbook upgrades an existing Linux Docker Compose installation. The public URL is `https://13.54.79.11.sslip.io`. Caddy terminates TLS, serves the React application through the internal web container, and proxies API requests directly to FastAPI. Only Caddy publishes TCP ports 80 and 443. PostgreSQL, Redis, Kafka, FastAPI, Grafana and Prometheus have no production host port mappings.

## 1. Prepare the EC2 host

- Keep the existing EC2 instance, application directory, Compose project name, private environment file and volumes. Take your usual database and volume backups before the change.
- Verify that `13.54.79.11` is the instance’s public IPv4 address and that public DNS for `13.54.79.11.sslip.io` resolves to it. Use a stable public address for ongoing service. Caddy needs inbound TCP 80 and 443 and outbound DNS and HTTPS for automatic certificate issuance and renewal.
- In the EC2 security group, allow inbound TCP 80 and 443 from the internet and restrict TCP 22 to your administrative IP or use your existing SSM access. Remove previously opened app, Grafana, database, broker and cache ports. Apply the equivalent host firewall rules. Check IPv6 rules separately if enabled.
- Use Docker Compose v2.24.4 or newer. Confirm ports 80 and 443 are available for Caddy. Schedule the switch if an existing reverse proxy owns them. The default proxy subnet is `172.30.251.0/28`; check the host routes, VPC and Docker networks for overlap. To change it, set `CADDY_PROXY_SUBNET`, `CADDY_PROXY_IP` and `API_PROXY_IP` together in the existing environment file. Choose two distinct usable addresses other than the gateway.
- This configuration assumes clients connect directly to Caddy. Revisit client-IP trust before adding CloudFront, an ALB or another upstream proxy. Keep a single API process for the in-process simulation runner and login limiter.

## 2. Transfer the prepared files when ready

From PowerShell in the local project root, enter the real SSH destination (for example, the EC2 login and public IP). These commands use the SSH identity already configured on your workstation. The archive contains only the four runtime files.

```powershell
$destination = Read-Host 'EC2 SSH destination (user@host)'
$bundle = Join-Path $env:TEMP 'payment-prod-config.tar.gz'
tar -czf $bundle compose.prod.yaml ops/Caddyfile apps/api/access.py apps/api/simulations.py
scp $bundle "${destination}:payment-prod-config.tar.gz"
ssh $destination
```

## 3. Select the existing installation

In **Bash on EC2**, identify the existing database container. Run the remaining Bash commands in the same shell. The commands below recover the project name, working directory and Compose file list from that container.

```bash
set -euo pipefail
docker compose ls
docker ps --filter label=com.docker.compose.service=db --format '{{.Names}}'
read -rp 'Existing payment database container name: ' db_container
project=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$db_container")
root=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$db_container")
file_list=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$db_container")
cd "$root"
read -rp 'Existing private env-file path [.env]: ' env_file
env_file=${env_file:-.env}
test -f "$env_file"
IFS=, read -ra previous_files <<< "$file_list"
base=(docker compose --project-directory "$root" --env-file "$env_file" -p "$project")
for file in "${previous_files[@]}"; do
  [[ "$(realpath "$file")" == "$root/compose.prod.yaml" ]] || base+=(-f "$file")
done
prod=("${base[@]}" -f "$root/compose.prod.yaml" --profile observe)
docker inspect -f '{{range .Mounts}}{{println .Destination .Name .Source}}{{end}}' "$db_container"
docker compose version
getent ahostsv4 13.54.79.11.sslip.io
sudo ss -ltnp
```

Stage and review the changes, particularly if the EC2 checkout has host-specific source edits. These files assume the published v1.0.0 application. Merge any EC2-specific edits before installing. Preserve the original copies outside the checkout for rollback.

```bash
stage=$(mktemp -d)
backup=$(mktemp -d)
tar -xzf ~/payment-prod-config.tar.gz -C "$stage"
files=(compose.prod.yaml ops/Caddyfile apps/api/access.py apps/api/simulations.py)
for file in "${files[@]}"; do
  if [[ -f "$file" ]]; then
    cp --parents "$file" "$backup"
    diff -u "$file" "$stage/$file" || true
  fi
done
printf 'Original files saved in %s\n' "$backup"
# After reviewing the differences:
for file in "${files[@]}"; do install -m 0644 "$stage/$file" "$file"; done
```

## 4. Validate and start

The hostname defaults to `13.54.79.11.sslip.io`. To switch later, set `PUBLIC_HOST` to the new DNS hostname in the same private environment file. Supply a hostname only. Caddy and FastAPI derive their settings from the same value.

```bash
"${prod[@]}" config --quiet
# Display published ports and volume names only. The full config contains secrets.
"${prod[@]}" config --format json | python3 -c '
import json, sys
c = json.load(sys.stdin)
published = {s: v["ports"] for s,v in c["services"].items() if v.get("ports")}
print("Published ports:", published)
assert set(published) == {"caddy"}
assert {(str(p["published"]),p["target"]) for p in published["caddy"]} == {("80",80),("443",443)}
print("Volumes:", {k:v["name"] for k,v in c["volumes"].items()})
'
# Confirm the db volume above matches the running db mount. Also check the
# existing model, credentials, broker and monitoring volumes under this project.
docker volume ls --filter "label=com.docker.compose.project=$project"
"${prod[@]}" pull caddy
"${prod[@]}" run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
"${prod[@]}" build tools web
"${prod[@]}" up -d --no-build --wait db redis kafka api relay worker web prometheus grafana caddy
"${prod[@]}" ps
"${prod[@]}" logs --tail=80 caddy
curl -sSI http://13.54.79.11.sslip.io/simulation
curl -fsS https://13.54.79.11.sslip.io/health/ready
```

Expect an HTTP-to-HTTPS redirect, a valid certificate and a successful readiness response. Open the HTTPS Simulation page, start a small Showcase run, then verify analyst sign-in and logout. Confirm the instance exposes only the intended application and administrative ports. The ACME challenge and public certificate require working public DNS and internet reachability. The production override makes no schema changes. Use the existing database, model and credentials.

Caddy stores its certificate state in the new project-scoped `caddy-data` and `caddy-config` volumes. Keep these alongside the existing volumes across restarts. Use the same Compose file list and environment file for subsequent operations. Grafana remains accessible to administrators through a private tunnel. For example, on EC2 obtain its internal address with the following command, then substitute it and your SSH destination in the workstation command.

```bash
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$("${prod[@]}" ps -q grafana)"
# On the workstation:
# ssh -N -L 127.0.0.1:3301:GRAFANA_CONTAINER_IP:3000 USER@EC2_HOST
```

Open `http://127.0.0.1:3301` while the tunnel is active. Recheck the container address after recreation.

## Rollback

Keep the security group restricted while rolling back. In the same EC2 shell, stop the proxy, restore any replaced files from the saved directory, and restart the prior configuration. The production-only files can stay on disk. These commands retain all named volumes.

```bash
"${prod[@]}" stop caddy
for file in "${files[@]}"; do
  if [[ -f "$backup/$file" ]]; then cp "$backup/$file" "$file"; fi
done
"${base[@]}" build tools web
"${base[@]}" --profile observe up -d --no-build --wait db redis kafka api relay worker web prometheus grafana
```

For reference, see [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https), [Caddy reverse proxy defaults](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy), [Uvicorn proxy settings](https://www.uvicorn.org/settings/), [Compose override semantics](https://docs.docker.com/reference/compose-file/merge/) and [EC2 security group rules](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/security-group-rules-reference.html).
