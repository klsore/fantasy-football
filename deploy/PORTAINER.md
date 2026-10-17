# One-domain deployment with Portainer and your existing Nginx

Website: `https://fantasy.example.com/`. Sign-in: `/login`. Private dashboard: `/private/`.

## Images

- `registry.example.com/docker-local/fantasy-football:latest` â€” same app image reused for public, private and refresh roles.
- `registry.example.com/docker-local/fantasy-football-gateway:latest` â€” path router for the one domain.

Both are Linux/amd64 images. Use a version tag in `IMAGE_TAG` for a fixed deployment instead of `latest`. API keys, passwords, local snapshots and user data are not embedded in either image.

## Portainer

1. Add `registry.example.com` as an authenticated registry in Portainer if required.
2. Create a Docker Standalone stack and paste `deploy/portainer-stack.yaml`. Do not use the local Caddy Compose file in Portainer.
3. Set these stack environment variables using your ignored local `.env` as reference:

| Variable | Value |
| --- | --- |
| `PUBLIC_HOST` | `fantasy.example.com` |
| `APP_PORT` | `8088` or an available port on the Docker host |
| `LISTEN_IP` | Docker host LAN IP, or `0.0.0.0` with a firewall restriction |
| `TRUSTED_PROXY_CIDR` | Your existing HTTPS Nginx proxy's actual source IP with `/32`, or the narrow network Docker sees |
| `ADMIN_USERNAME` | `admin` or your chosen lowercase username |
| `ADMIN_PASSWORD` | Unique password, 16-128 characters |
| `YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET`, `YAHOO_REFRESH_TOKEN` | Your Yahoo settings |
| `PUBLIC_LEAGUE_KEY`, `PUBLIC_TEAM_KEY` | Your selected league/team keys from `.env` |
| `AZURE_OPENAI_ENABLED` | `true` to allow admins to generate reports, otherwise `false` |
| `AZURE_OPENAI_API_KEY` | Your Azure key |

The endpoint and deployment default to your configured GPT-6.1 Sol resource. Optional `AI_MONTHLY_REQUEST_LIMIT`, `AI_COOLDOWN_SECONDS`, and `AI_MAX_OUTPUT_TOKENS` default to 20, 60 and 4096. No ACME email or certificates are needed in the Portainer stack: your existing Nginx owns HTTPS.

4. Deploy and allow the first daily refresh to finish (a few minutes). A new Portainer deployment has new state volumes and creates its first admin from the supplied settings. It does not automatically copy your local users, AI ledger, or refreshed Yahoo token. For continuity, stop local writers and securely migrate the local `yahoo-tokens` volume to Portainer's `private-data` volume before starting remote services. Do not operate two refresh workers against the same Yahoo credentials during migration. Use the most recently saved refresh token if Yahoo has rotated it.

## Existing Nginx proxy

Configure the HTTPS host `fantasy.example.com` and its certificate through your existing Nginx setup. Forward **all paths** to `http://DOCKER_HOST_IP:8088`. The Docker host is the machine managed by Portainer; this is not Portainer's UI/API port.

Use `deploy/nginx-external.conf.example` as the location configuration. It must preserve Host, override `X-Forwarded-For` with the real client IP, set `X-Forwarded-Proto https`, and allow a 150-second upstream response for manual AI reports. Redirect external HTTP to HTTPS.

Restrict the host's published app port to your Nginx source IP at the firewall. The internal gateway also denies untrusted source IPs and requests not marked HTTPS. Do not set `TRUSTED_PROXY_CIDR=0.0.0.0/0`; configure the narrow actual proxy source. If another proxy/CDN precedes your existing Nginx, configure its trusted real-IP behavior there first.

The internal gateway does not issue public certificates. Browser-to-edge traffic is HTTPS; edge-to-Portainer traffic is HTTP on your trusted LAN. If that hop crosses an untrusted network, use a VPN or add verified upstream TLS before deployment.

## Verify

- `/` loads an anonymized cached dashboard without login.
- `/login` signs in and redirects to `/private/` on the same domain.
- Private API requests without a session return 401; viewers cannot manage users or generate paid AI.
- Admins see Manage users, Change password and manual report generation.
- Secure/HttpOnly cookies, accurate client IP limiting, and valid HTTPS certificate are present.
- Public refreshes never contact Yahoo/Azure; only the scheduled worker calls Yahoo.

Keep all volumes when updating. Changing `ADMIN_PASSWORD` in Portainer does not overwrite an existing SQLite user; use Change password in the app. Enable registry authentication when Portainer pulls updates.