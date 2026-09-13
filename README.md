# Side A / Side B — Playlist Converter

Transfer playlists between YouTube Music, Apple Music, and Spotify. The app first imports and matches tracks, lets you review or skip uncertain results, then creates a destination playlist only after confirmation.

React + FastAPI + PostgreSQL + RabbitMQ, with a separate outbox relay and Python worker. A local SQLite worker and isolated demo catalog make the whole flow usable without provider credentials.

## Quick start: local demo

Python 3.12+ and Node 22.12+ are required. Run these commands from the repository root. Preserve your existing `backend/.env` if you already have one.

```sh
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
cd frontend
npm ci
cd ..
```

Start the API in one terminal:

```sh
cd backend
DEMO_MODE=true .venv/bin/uvicorn app.main:app --reload
```

Start the local worker in a second terminal, with the same database and demo settings:

```sh
cd backend
DEMO_MODE=true .venv/bin/python -m app.workers.runner local
```

Start React in a third terminal:

```sh
cd frontend
npm run dev
```

Open [localhost:5173](http://localhost:5173). Connect two demo providers, load “Late night favorites,” analyze, accept the suggested Get Lucky match, skip Lost recording, and confirm. Demo data never reaches real accounts. Use a separate `DATABASE_URL` for demo and real-provider work; never change modes against a database containing the other mode's accounts.

Local defaults use `backend/playlist.db`. API startup creates SQLite tables for development. Run exactly one local worker. PostgreSQL deployments use migrations and support multiple workers.

## Full stack with Docker

If `backend/.env` does not exist, copy `backend/.env.example` to it. Set `DEMO_MODE=true` for an account-free trial, or configure real credentials below. Docker Desktop must be running.

```sh
docker compose up --build
```

Open [localhost:5173](http://localhost:5173). The stack includes the frontend, API, PostgreSQL, RabbitMQ, schema migration, outbox relay, and worker. Do not run the local frontend/API simultaneously on the same ports. RabbitMQ management is at [localhost:15672](http://localhost:15672), with development username/password `playlist` / `playlist`. Database data is persisted in the `postgres` volume. These development credentials are not suitable for public deployment.

## Real provider setup

Set `DEMO_MODE=false` in both API and worker. Credentials stay on the backend; access and refresh tokens are encrypted in PostgreSQL. The browser receives only the Apple developer token required by MusicKit and supplies its Music User Token over the authenticated API.

**YouTube:** Enable YouTube Data API v3, configure an OAuth consent screen and authorized test users, and create a Web application OAuth client. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI=http://localhost:8000/auth/youtube/callback`. Register that exact redirect URI. The Connect button uses authorization code + PKCE, single-use state, offline access, and the YouTube force-ssl scope. Your account needs a YouTube channel to create playlists. YouTube Music uses YouTube playlists through the Data API.

**Apple Music:** Create a MusicKit key and set `APPLE_TEAM_ID`, `APPLE_KEY_ID`, and `APPLE_PRIVATE_KEY_PATH`. For local Python, use the private key's absolute path. For Compose, put it in `secrets/` and use `/run/secrets/your-key.p8`; that directory is mounted read-only into the API and worker and ignored by Git. The browser uses MusicKit authorization, and the API validates the user token by fetching the user's storefront. A suitable Apple Music subscription/account is needed for library operations.

**Spotify:** Create a Spotify developer application. Set `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, and a registered `SPOTIFY_REDIRECT_URI`. Spotify's loopback rules require an explicit loopback IP, so for local Spotify testing use `http://127.0.0.1:8000/auth/spotify/callback`, set `FRONTEND_URL=http://127.0.0.1:5173`, update Google's redirect to the same hostname if using both, and browse that hostname consistently. The integration uses `/playlists/{id}/items` and `/me/playlists`, with private playlist creation. Developer-mode access and account eligibility are governed by Spotify.

OAuth callbacks and the frontend must share the same hostname so the HttpOnly session cookie can bind the callback to the initiating browser. For HTTPS deployment, route both through one origin and update the registered callback URLs.

**Session identity:** Each browser has an anonymous, signed 30-day app session. Provider connections and transfer history belong to that session. There is no password login, cross-device account recovery, or email verification. Clearing cookies creates a new app identity. This is a session-based v1, not a complete customer identity platform.

## Reliability and recovery

- Creation uses a client idempotency key. The conversion and its outbox job commit together, so a broker outage cannot lose the requested work.
- The relay uses durable queues, persistent messages, and publisher confirms. Workers acknowledge only after persisting results. Unfinished published jobs become eligible for republication after ten minutes.
- PostgreSQL advisory locks serialize conversion jobs across worker processes and survive checkpoint commits. The local SQLite mode requires a single worker.
- Every completed match and transferred track is checkpointed. Playlist order and repeated tracks are preserved.
- Rate limits and transient reads retry up to five times using persisted exponential backoff and `Retry-After`. Delays live in the database outbox, rather than RabbitMQ TTL retry queues. Exhausted jobs are persisted as dead and messages go to the dead-letter queue. Provider quota exhaustion requires an explicit retry after the quota resets.
- A write intent is committed **before** calling the provider. If a write times out, returns a server error, or the worker dies after sending it, automatic replay stops. “Check provider for completed write” adopts an observed playlist with the conversion marker or an exact expected track sequence. It never treats a missing result as proof of failure.
- If an uncertain write remains absent or someone edits the destination, reconciliation stays blocked. Wait for provider consistency or inspect the destination manually. There is deliberately no button that guesses and replays an uncertain write. A transfer can remain failed rather than create duplicates.
- Verified mappings are scoped to the app user and destination storefront, and expire after 30 days. They reduce searches but cannot guarantee continued catalog availability.
- SSE provides status updates with a five-second polling fallback. Transfer history retains the latest 100 conversions in the UI.

See [architecture and implementation status](docs/implementation.md) for the plan coverage and remaining external validation.

## Tests

```sh
cd backend
.venv/bin/python -m pytest -q
cd ../frontend
npm run lint
npm run build
```

The suite covers six provider directions, 100 synthetic matching cases, API contracts via HTTP mock transports, OAuth state/refresh, account isolation, review gating, caching, duplicate jobs, retries, and ambiguous-write recovery. Synthetic results are not a real-catalog accuracy measurement.

For actual PostgreSQL/RabbitMQ integration:

```sh
# Repository root; creates only disposable test infrastructure.
docker compose -p playlist-converter-check -f compose.test.yaml up -d --wait
cd backend
TEST_DATABASE_URL=postgresql+psycopg://playlist:playlist@127.0.0.1:55432/playlist .venv/bin/python -m pytest -q
# Use a separate fresh database for migration and queue smoke tests.
docker exec playlist-converter-check-db-1 createdb -U playlist queue_smoke
export DATABASE_URL=postgresql+psycopg://playlist:playlist@127.0.0.1:55432/queue_smoke
export RABBITMQ_URL=amqp://playlist:playlist@127.0.0.1:55672/
export DEMO_MODE=true
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.queue_smoke
cd ..
docker compose -p playlist-converter-check -f compose.test.yaml down -v
```

`TEST_DATABASE_URL` must point to a disposable database: tests drop and recreate application tables. The queue smoke starts two worker processes, publishes duplicate deliveries, and verifies a single destination with five tracks.

After connecting real accounts, use the read-only experiment tool for milestones A/B:

```sh
cd backend
.venv/bin/python -m scripts.provider_probe USER_ID youtube
.venv/bin/python -m scripts.provider_probe USER_ID youtube PLAYLIST_ID --destination apple
```

Get the app user ID from `/auth/me` in your authenticated session. The probe lists playlists, fetches all normalized tracks, and optionally ranks candidates for the first track. It never writes to a provider; confirm transfers in the UI to test create/add operations.

## Deployment configuration

Set `DEV_ENV=production`, `DEMO_MODE=false`, a random `SECRET_KEY` of at least 32 characters, a Fernet `TOKEN_ENCRYPTION_KEY`, and `COOKIE_SECURE=true`. Generate secrets locally with `secrets.token_urlsafe(48)` and `cryptography.fernet.Fernet.generate_key()`. Keep them stable across API/worker restarts; changing the encryption key without migrating ciphertext makes saved credentials unreadable. Replace Compose development passwords, use HTTPS, register the production OAuth callbacks, and run `alembic upgrade head` before serving traffic. `/health` checks database connectivity; it does not claim the worker or broker is healthy. API schema is at `/docs`.

Official integration references: [YouTube playlists](https://developers.google.com/youtube/v3/guides/implementation/playlists), [Apple Music API](https://developer.apple.com/documentation/AppleMusicAPI), [Spotify playlist items](https://developer.spotify.com/documentation/web-api/reference/get-playlists-items), and [Spotify redirect URIs](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri).
