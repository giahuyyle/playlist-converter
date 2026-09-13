# Playlist Converter frontend

React + Vite. See the root README for API, worker, provider, and Docker setup.

- `npm ci` installs dependencies.
- `npm run dev` starts localhost:5173 and proxies `/api`, `/auth`, and `/health` to localhost:8000.
- `npm run lint` validates React hooks and source code.
- `npm run build` creates the production bundle.

Production Docker uses Nginx to serve the bundle and proxy API/auth/SSE through one origin. Standalone `vite preview` does not provide the production API proxy; use Docker for that setup.
