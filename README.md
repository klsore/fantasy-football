# Matchup — Fantasy Football Dashboard

A self-hosted, read-only Yahoo Fantasy Football dashboard with side-by-side weekly lineups, player statistics, league management tools, and optional on-demand Azure AI reports.

## Features

- Public cached matchup page with anonymized fantasy-team names.
- Private portal with SQLite accounts, admin-assigned teams, and personal report history.
- Player photos, NFL opponents and kickoff times, and season/week/range statistics.
- League standings, lineup checks, performance trends, bye planning, personal watchlists and decision logs.
- Admin-controlled full-data refresh interval (three hours by default).
- Optional five-minute scoring refreshes during scheduled game windows, with an admin toggle, request budget and backoff.
- Manual Azure Responses API reports with configurable shared monthly attempt and output/reasoning token limits. No automatic AI generation or retries.

## Requirements

Docker Compose, approved Yahoo Fantasy API access, and Yahoo OAuth client credentials plus a refresh token. Optional Azure access is required only for AI reports. Yahoo authentication credentials must be obtained separately; an interactive OAuth authorization flow is not included.

## Local setup

1. Copy `.env.example` to `.env` and supply Yahoo credentials, the league/team keys, and a unique admin password of at least 16 characters.
2. For local Compose, set `PUBLIC_HOST` and `PRIVATE_HOST` to `fantasy.localhost`; Compose also supplies these hosts to the services.
3. Run `docker compose up --build -d`.
4. Open `https://fantasy.localhost:9443/`. The local Caddy certificate authority must be trusted on your device for verified HTTPS. Do not use the development certificate in production.
5. Sign in through `/login`; the private portal is `/private/`. The refresh worker publishes the initial snapshot asynchronously, which can take several minutes.

Keep `.env`, runtime data, databases, refresh tokens, certificates and keys out of source control and shared archives. The initial admin is created only when the user database is empty; changing its environment password does not change an existing account's password.

## Production deployment

See [Portainer deployment](deploy/PORTAINER.md). Use your own image registry and hostnames. Terminate HTTPS at your reverse proxy, preserve forwarding headers, restrict access to the gateway, and set its trusted proxy CIDR narrowly. Preserve and back up the SQLite and snapshot volumes.

The public backend has no Yahoo/Azure credentials or outbound network access. Visitors read shared snapshots; they cannot directly trigger upstream requests. Account and paid-analysis routes require authentication. Cookies are Secure, HttpOnly and SameSite=Strict. Sessions last up to 30 days with a seven-day inactivity limit; logout and password changes revoke them.

## Administration

Use **Manage users** to add/delete accounts and assign fantasy teams. Team assignment sets a user's default team and permits report generation for that team; admins may generate reports for any matchup. Reports, report comparisons and background job status are restricted to their creator. Existing legacy reports are assigned to matching account authors during migration.

**League tools** includes admin controls for full-data refresh cadence, optional live scoring, manual refresh, and AI budget settings. Settings persist in SQLite and take precedence over the environment defaults once saved. AI attempt limits are shared across users; saving limits does not reset usage. Failed Azure attempts count toward the local request limit. The output/reasoning token cap excludes input tokens and is not a dollar spending limit.

Live scoring refreshes every five minutes from 15 minutes before a scheduled NFL kickoff through six hours afterward. This is periodic scoring, not a real-time push feed. Schedule changes, source outages and long games can affect freshness. Visible pages read the cache every minute while live scoring is enabled. Full refreshes and manual refresh limits are independent of the live-scoring budget.

## Data and limitations

Fantasy statistics and player metadata are provided by Yahoo Fantasy. NFL schedules come from [nflverse/nfldata](https://github.com/nflverse/nfldata); kickoff times display in the viewer's device timezone. Data-source attribution is displayed on the site. API access, permissions and applicable data-source terms must be satisfied separately; publishing this source does not grant rights to redistribute provider data.

Yahoo injury detail is shown only when available. Live injury news and career statistics are not connected. Recent averages use observed completed weeks, including bye/inactive zeroes, rather than games played. All lineup moves, waiver claims and trades must be performed in Yahoo.

## Tests

The Python suite uses mocked upstream services and should run in the Linux container. Mount `tests/` read-only into `/app/tests` and provide writable temporary `/app/data` when running `python -m unittest discover -s tests -q`. JavaScript report-rendering tests run with `node tests/report.test.js`. `scripts/check_https.py` is an optional local smoke check using local credentials and CA files; it never generates paid reports.

## Repository scope

This public repository contains a sanitized source snapshot with fresh Git history. It includes no deployment credentials, runtime snapshots or account databases. Example hostnames and image registry paths are placeholders; build your own images before deployment.
