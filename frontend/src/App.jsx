import { useCallback, useEffect, useRef, useState } from "react";
import "./App.css";

const labels = {
  youtube: "YouTube Music",
  apple: "Apple Music",
  spotify: "Spotify",
};
const logos = {
  youtube: "/brands/youtube-music.svg",
  apple: "/brands/apple-music.svg",
  spotify: "/brands/spotify.svg",
};
const providers = Object.keys(labels);
const active = ["queued", "matching", "transferring"];
const needsDecision = ["review", "unmatched", "pending"];
const statusLabels = {
  queued: "Queued",
  matching: "Matching tracks",
  review_required: "Needs review",
  transferring: "Transferring",
  completed: "Complete",
  failed: "Needs attention",
};

function statusLabel(status) {
  return statusLabels[status] || status.replaceAll("_", " ");
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "include",
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "PlaylistConverter",
      ...options.headers,
    },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : `Request failed (${response.status})`,
    );
  }
  return response.json();
}
function post(path, body) {
  return api(path, {
    method: "POST",
    body: body ? JSON.stringify(body) : undefined,
  });
}
function Track({ track }) {
  return (
    <div className="track">
      <strong>{track.title}</strong>
      <small>
        {track.artists.join(", ") || "Unknown artist"}
        {track.duration_ms
          ? ` · ${Math.floor(track.duration_ms / 60000)}:${String(Math.floor(track.duration_ms / 1000) % 60).padStart(2, "0")}`
          : ""}
      </small>
    </div>
  );
}
function destinationUrl(c) {
  if (!c.destination_playlist_id) return null;
  if (c.destination_provider === "youtube")
    return `https://www.youtube.com/playlist?list=${encodeURIComponent(c.destination_playlist_id)}`;
  if (c.destination_provider === "spotify")
    return `https://open.spotify.com/playlist/${encodeURIComponent(c.destination_playlist_id)}`;
  return "https://music.apple.com/library/playlists";
}
function App() {
  const [me, setMe] = useState(null);
  const [source, setSource] = useState("youtube");
  const [destination, setDestination] = useState("apple");
  const [playlists, setPlaylists] = useState([]);
  const [playlistId, setPlaylistId] = useState("");
  const [name, setName] = useState("");
  const [history, setHistory] = useState([]);
  const [conversion, setConversion] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState("all");
  const requestKey = useRef(null);
  const playlistRequest = useRef(0);
  const refresh = useCallback(async () => {
    const [user, transfers] = await Promise.all([
      api("/auth/me"),
      api("/api/conversions"),
    ]);
    setMe(user);
    setHistory(transfers);
  }, []);
  const perform = useCallback(async (action) => {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, []);
  useEffect(() => {
    let ignore = false;
    post("/auth/session")
      .then(() => {
        if (!ignore) return refresh();
      })
      .catch((e) => {
        if (!ignore) setError(e.message);
      });
    return () => {
      ignore = true;
    };
  }, [refresh]);
  const id = conversion?.id;
  const status = conversion?.status;
  useEffect(() => {
    if (!id || !active.includes(status)) return;
    let closed = false;
    const update = async () => {
      try {
        const result = await api(`/api/conversions/${id}`);
        if (!closed) {
          setConversion(result);
          if (result.status === "review_required" && status !== result.status)
            setFilter("review");
          if (!active.includes(result.status)) await refresh();
        }
      } catch (e) {
        if (!closed) setError(e.message);
      }
    };
    const events = new EventSource(`/api/conversions/${id}/events`, {
      withCredentials: true,
    });
    events.onmessage = () => {
      update();
    };
    // Polling remains available if a proxy buffers or drops the event stream.
    const timer = setInterval(update, 5000);
    return () => {
      closed = true;
      events.close();
      clearInterval(timer);
    };
  }, [id, status, refresh]);
  async function connect(provider) {
    if (me.demo) {
      await post(`/auth/demo/${provider}`);
      await refresh();
      return;
    }
    if (provider !== "apple") {
      window.location.assign(`/auth/${provider}`);
      return;
    }
    const { developer_token: developerToken } = await api("/auth/apple/token");
    if (!window.MusicKit) {
      await new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "https://js-cdn.music.apple.com/musickit/v3/musickit.js";
        script.onload = resolve;
        script.onerror = () =>
          reject(new Error("Could not load Apple Music authorization"));
        document.head.appendChild(script);
      });
    }
    await window.MusicKit.configure({
      developerToken,
      app: { name: "Playlist Converter", build: "1.0.0" },
    });
    const music_user_token = await window.MusicKit.getInstance().authorize();
    await post("/auth/apple", { music_user_token });
    await refresh();
  }
  async function loadPlaylists() {
    const request = ++playlistRequest.current;
    setLoading(true);
    setError("");
    setPlaylists([]);
    setPlaylistId("");
    try {
      const result = await api(`/api/playlists/${source}`);
      if (request === playlistRequest.current) setPlaylists(result);
    } catch (e) {
      if (request === playlistRequest.current) setError(e.message);
    } finally {
      if (request === playlistRequest.current) setLoading(false);
    }
  }
  function swapRoute() {
    playlistRequest.current++;
    setLoading(false);
    setSource(destination);
    setDestination(source);
    setPlaylists([]);
    setPlaylistId("");
    setName("");
  }
  async function analyze() {
    const body = {
      source_provider: source,
      destination_provider: destination,
      source_playlist_id: playlistId,
      name: name.trim(),
    };
    const fingerprint = JSON.stringify(body);
    if (requestKey.current?.fingerprint !== fingerprint)
      requestKey.current = { fingerprint, key: crypto.randomUUID() };
    const result = await post("/api/conversions", {
      ...body,
      request_key: requestKey.current.key,
    });
    setConversion(await api(`/api/conversions/${result.id}`));
    await refresh();
    requestKey.current = null;
  }
  async function review(matchId, value) {
    const body =
      value === "skip" ? { skip: true } : { candidate_index: Number(value) };
    setConversion(
      await api(`/api/conversions/${id}/matches/${matchId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    );
  }
  async function action(kind) {
    await post(`/api/conversions/${id}/${kind}`);
    setConversion(await api(`/api/conversions/${id}`));
    await refresh();
  }
  const unresolved =
    conversion?.matches?.filter((m) => needsDecision.includes(m.status))
      .length || 0;
  const rows =
    conversion?.matches?.filter(
      (m) => filter === "all" || needsDecision.includes(m.status),
    ) || [];
  const sourceConnected = me?.connected.includes(source);
  const destinationConnected = me?.connected.includes(destination);
  return (
    <main>
      <a className="skip-link" href="#workspace">
        Skip to converter
      </a>
      <header>
        <a className="brand" href="/">
          <span className="brand-mark" aria-hidden="true">
            A/B
          </span>
          <span>Side A / Side B</span>
        </a>
        <span className={`header-meta ${me?.demo ? "is-demo" : ""}`}>
          {me?.demo ? "Demo data" : "Private session"}
        </span>
      </header>
      <section className="intro">
        <h1>Move a playlist.</h1>
        <p>Choose the services, review uncertain matches, then transfer.</p>
      </section>
      {error && (
        <div className="error" role="alert">
          <span>{error}</span>
          <button onClick={() => setError("")}>
            Dismiss
          </button>
        </div>
      )}
      {!me ? (
        <div className="loading-state" role="status">
          <span className="loading-dot" aria-hidden="true" />
          <p>Connecting to the app…</p>
          {error && (
            <button onClick={() => window.location.reload()}>Try again</button>
          )}
        </div>
      ) : (
        <div id="workspace">
          <div className="workspace-grid">
            <section className="panel connections-panel" aria-labelledby="connections-title">
              <div className="section-heading">
                <h2 id="connections-title">Services</h2>
              </div>
              <div className="accounts">
              {providers.map((p) => {
                const connected = me.connected.includes(p);
                return (
                  <div className={`account ${p}`} data-connected={connected} key={p}>
                    <div className="account-heading">
                      <span className="provider-logo" aria-hidden="true">
                        <img src={logos[p]} alt="" />
                      </span>
                      <h3>{labels[p]}</h3>
                    </div>
                    <p className={`connection-state ${connected ? "is-connected" : ""}`}>
                      <span aria-hidden="true" />
                      {connected
                        ? "Connected"
                        : me.available[p]
                          ? "Ready to connect"
                          : "Setup required"}
                    </p>
                    <button
                      className={connected ? "quiet" : "connect-button"}
                      disabled={busy || !me.available[p]}
                      aria-label={`${connected ? "Disconnect" : "Connect"} ${labels[p]}`}
                      onClick={() =>
                        perform(async () => {
                          if (connected) {
                            await api(`/auth/${p}`, { method: "DELETE" });
                            await refresh();
                          } else await connect(p);
                        })
                      }
                    >
                      {connected ? "Disconnect" : "Connect"}
                    </button>
                  </div>
                );
              })}
              </div>
            </section>
            <section className="panel route-panel" aria-labelledby="playlist-title">
            <div className="section-heading">
              <h2 id="playlist-title">New transfer</h2>
            </div>
            <div className="form-row route-row">
              <label className="route-field">
                <span>From</span>
                <span className={`provider-logo ${source}`} aria-hidden="true">
                  <img src={logos[source]} alt="" />
                </span>
                <select
                  value={source}
                  disabled={busy}
                  onChange={(e) => {
                    playlistRequest.current++;
                    setLoading(false);
                    setSource(e.target.value);
                    setPlaylists([]);
                    setPlaylistId("");
                    if (destination === e.target.value)
                      setDestination(
                        providers.find((p) => p !== e.target.value),
                      );
                  }}
                >
                  {providers.map((p) => (
                    <option key={p} value={p}>
                      {labels[p]}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="swap-button"
                type="button"
                disabled={busy || loading}
                aria-label="Swap source and destination"
                title="Swap services"
                onClick={swapRoute}
              >
                ⇄
              </button>
              <label className="route-field">
                <span>To</span>
                <span className={`provider-logo ${destination}`} aria-hidden="true">
                  <img src={logos[destination]} alt="" />
                </span>
                <select
                  value={destination}
                  disabled={busy}
                  onChange={(e) => setDestination(e.target.value)}
                >
                  {providers
                    .filter((p) => p !== source)
                    .map((p) => (
                      <option key={p} value={p}>
                        {labels[p]}
                      </option>
                    ))}
                </select>
              </label>
              <button
                className="load-button"
                disabled={busy || loading || !me.connected.includes(source)}
                onClick={loadPlaylists}
              >
                {loading ? "Loading…" : "Show playlists"}
              </button>
            </div>
            {playlists.length > 0 && (
              <div className="form-row">
                <label>
                  Playlist
                  <select
                    value={playlistId}
                    onChange={(e) => {
                      setPlaylistId(e.target.value);
                      setName(
                        playlists.find((p) => p.id === e.target.value)?.name ||
                          "",
                      );
                    }}
                  >
                    <option value="">Choose a playlist</option>
                    {playlists.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                        {p.track_count != null
                          ? ` · ${p.track_count} tracks`
                          : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Destination playlist name
                  <input
                    maxLength={200}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <button
                  className="primary"
                  disabled={
                    busy ||
                    !playlistId ||
                    !name.trim() ||
                    !me.connected.includes(destination)
                  }
                  onClick={() => perform(analyze)}
                >
                  Review matches
                </button>
              </div>
            )}
            {!loading && playlists.length === 0 && !sourceConnected && (
              <p className="helper-text" role="status">
                Connect {labels[source]} to view its playlists.
              </p>
            )}
            {playlists.length > 0 && !destinationConnected && (
              <p className="helper-text warning" role="status">
                Connect {labels[destination]} above before analyzing this playlist.
              </p>
            )}
            </section>
          </div>
          {conversion && (
            <section className="panel transfer-panel" aria-label="Transfer details" aria-busy={active.includes(status)}>
              <div className="section-heading">
                <div>
                  <span className="eyebrow">
                    {labels[conversion.source_provider]} →{" "}
                    {labels[conversion.destination_provider]}
                  </span>
                  <h2>{conversion.name}</h2>
                </div>
                <span className={`badge ${status}`}>
                  <span className="status-dot" aria-hidden="true" />
                  {statusLabel(status)}
                </span>
              </div>
              <div className="stats">
                <div>
                  <strong>{conversion.total_tracks}</strong>
                  <span>total tracks</span>
                </div>
                <div>
                  <strong>{conversion.matched_tracks}</strong>
                  <span>selected</span>
                </div>
                <div>
                  <strong>{unresolved}</strong>
                  <span>need a decision</span>
                </div>
                <div>
                  <strong>{conversion.transferred_tracks}</strong>
                  <span>transferred</span>
                </div>
              </div>
              {active.includes(status) && (
                <div className="progress-state" role="status" aria-live="polite">
                  <p>
                    {status === "matching"
                      ? "Finding the best matches…"
                      : status === "transferring"
                        ? "Building your new playlist…"
                        : "Waiting for the worker…"}
                  </p>
                  <progress
                    max={conversion.total_tracks || 1}
                    value={
                      status === "transferring"
                        ? conversion.transferred_tracks
                        : conversion.matches?.filter(
                            (m) => m.status !== "pending",
                          ).length || 0
                    }
                  />
                </div>
              )}
              {conversion.error && (
                <p className="error" role="alert">
                  {conversion.error}
                </p>
              )}
              {status === "failed" && (
                <button
                  disabled={busy}
                  onClick={() =>
                    perform(() =>
                      action(conversion.pending_write ? "reconcile" : "retry"),
                    )
                  }
                >
                  {conversion.pending_write
                    ? "Check provider for completed write"
                    : "Retry transfer"}
                </button>
              )}
              {status === "review_required" && (
                <div className="review-toolbar">
                  <label>
                    Show
                    <select
                      value={filter}
                      onChange={(e) => setFilter(e.target.value)}
                    >
                      <option value="review">Needs a decision ({unresolved})</option>
                      <option value="all">All tracks ({conversion.total_tracks})</option>
                    </select>
                  </label>
                  <p aria-live="polite">
                    {unresolved
                      ? `${unresolved} ${unresolved === 1 ? "track needs" : "tracks need"} your decision.`
                      : "All decisions made. Ready to transfer."}
                  </p>
                  <button
                    className="primary"
                    disabled={
                      busy || unresolved > 0 || !conversion.matched_tracks
                    }
                    onClick={() => perform(() => action("confirm"))}
                  >
                    Transfer {conversion.matched_tracks} tracks →
                  </button>
                </div>
              )}
              {status === "completed" && (
                <div className="success">
                  <h3>Your playlist is ready.</h3>
                  <p>
                    {conversion.transferred_tracks} tracks transferred ·{" "}
                    {conversion.failed_tracks} skipped.
                  </p>
                  {!me.demo && (
                    <a
                      target="_blank"
                      rel="noreferrer"
                      href={destinationUrl(conversion)}
                    >
                      Open {labels[conversion.destination_provider]} ↗
                    </a>
                  )}
                </div>
              )}
              <div className="matches">
                {status === "review_required" && rows.length > 0 && (
                  <div className="match-header" aria-hidden="true">
                    <span />
                    <span>Original</span>
                    <span />
                    <span>Closest match</span>
                    <span>Decision</span>
                  </div>
                )}
                {rows.map((m) => (
                  <article className="match" key={m.id}>
                    <span className="position">{m.position + 1}</span>
                    <div className="track-cell source-track">
                      <span className="mobile-label">Original</span>
                      <Track track={m.source} />
                    </div>
                    <span className="arrow">→</span>
                    <div className="track-cell destination-track">
                      <span className="mobile-label">Closest match</span>
                      {m.destination ? (
                        <Track track={m.destination} />
                      ) : (
                        <p className="muted">
                          {m.status === "skipped"
                            ? "Skipped"
                            : "No match selected"}
                        </p>
                      )}
                      <small className="muted">
                        {statusLabel(m.status)}
                        {m.confidence
                          ? ` · ${Math.round(m.confidence * 100)}% confidence`
                          : ""}
                        {m.transferred ? " · transferred" : ""}
                      </small>
                    </div>
                    {status === "review_required" && (
                      <label className="choice">
                        <span className="sr-only">
                          Match for {m.source.title}
                        </span>
                        <select
                          disabled={busy}
                          value={
                            m.status === "skipped"
                              ? "skip"
                              : needsDecision.includes(m.status)
                                ? ""
                                : m.destination
                                  ? String(
                                      m.candidates.findIndex(
                                        (c) =>
                                          c.track.provider_id ===
                                          m.destination.provider_id,
                                      ),
                                    )
                                  : ""
                          }
                          onChange={(e) =>
                            perform(() => review(m.id, e.target.value))
                          }
                        >
                          <option value="" disabled>
                            Choose a match
                          </option>
                          {m.candidates.map((c, i) => (
                            <option key={i} value={i}>
                              {c.track.title} — {c.track.artists.join(", ")} (
                              {Math.round(c.confidence * 100)}%)
                            </option>
                          ))}
                          <option value="skip">Skip this track</option>
                        </select>
                      </label>
                    )}
                  </article>
                ))}
              </div>
            </section>
          )}
          <section className="panel">
            <div className="section-heading">
              <h2>Transfer history</h2>
              <button
                className="icon-button"
                aria-label="Refresh transfer history"
                disabled={busy}
                onClick={() => perform(refresh)}
              >
                ↻
              </button>
            </div>
            {history.length ? (
              <div className="history">
                {history.map((c) => (
                  <button
                    key={c.id}
                    disabled={busy}
                    onClick={() =>
                      perform(async () => {
                        setConversion(await api(`/api/conversions/${c.id}`));
                        setFilter(
                          c.status === "review_required" ? "review" : "all",
                        );
                      })
                    }
                  >
                    <span>
                      <strong>{c.name}</strong>
                      <small>
                        {labels[c.source_provider]} →{" "}
                        {labels[c.destination_provider]} ·{" "}
                        {new Date(c.created_at * 1000).toLocaleDateString()}
                      </small>
                    </span>
                    <span className={`badge ${c.status}`}>
                      <span className="status-dot" aria-hidden="true" />
                      {statusLabel(c.status)}
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="muted">Your transfers will appear here.</p>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
export default App;
