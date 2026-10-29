<script>
  import { onMount } from 'svelte'
  import { api } from './api.js'

  let { onsetup, canSetup = true, isAdmin = false } = $props()
  let data = $state(null)
  let error = $state(null)
  let refreshing = $state(false)
  let updatedAt = $state(null)

  // Seconds between automatic refreshes; 0 = manual only. Remembered per browser.
  const INTERVALS = [[0, 'Off'], [5, '5 seconds'], [15, '15 seconds'], [30, '30 seconds'],
    [60, '1 minute'], [300, '5 minutes'], [600, '10 minutes']]
  const KEY = 'horus.activity.interval'
  function savedInterval() {
    try {
      const raw = localStorage.getItem(KEY)
      const v = Number(raw)
      return raw !== null && INTERVALS.some(([n]) => n === v) ? v : 15
    } catch {
      return 15
    }
  }
  let interval = $state(savedInterval())
  function changeInterval(v) {
    interval = Number(v)
    try { localStorage.setItem(KEY, String(interval)) } catch { /* private mode: not remembered */ }
  }

  const METHOD = { direct_play: 'Direct Play', direct_stream: 'Direct Stream', transcode: 'Transcode' }

  function bandwidth(kbps) {
    if (kbps == null) return '—'
    return kbps >= 1000 ? `${(kbps / 1000).toFixed(1)} Mbps` : `${kbps} kbps`
  }

  function clock(ms) {
    const total = Math.max(0, Math.floor(ms / 1000))
    const h = Math.floor(total / 3600)
    const m = Math.floor((total % 3600) / 60)
    const sec = String(total % 60).padStart(2, '0')
    return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
  }

  // Estimated finish time, from when the data was fetched. Not shown while paused.
  function eta(s) {
    if (s.state === 'paused' || s.duration_ms == null || s.position_ms == null || !updatedAt) return null
    const end = new Date(updatedAt.getTime() + (s.duration_ms - s.position_ms))
    return end.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  function details(s) {
    return [
      ['Product', s.product],
      ['Player', s.device],
      ['Quality', s.quality],
      ['Stream', METHOD[s.play_method]],
      ['Container', s.container],
      ['Video', s.video],
      ['Audio', s.audio],
      ['Subtitle', s.subtitle ?? 'None'],
      ['Location', s.location],
      ['Bandwidth', bandwidth(s.bandwidth_kbps)],
    ].filter(([, v]) => v)
  }

  // One tile per server and one for everything: how many streams there are, how they are being
  // served, and the bandwidth they add up to, split into local (same network) and remote.
  // A paused stream counts as a stream but adds no bandwidth, because nothing is flowing.
  // Unreachable servers are left out.
  const streamsText = (n) => `${n} stream${n === 1 ? '' : 's'}`
  const sum = (list) => {
    const known = list.filter((s) => s.bandwidth_kbps != null && s.state !== 'paused')
    return known.length ? known.reduce((n, s) => n + s.bandwidth_kbps, 0) : list.length ? null : 0
  }
  const isRemote = (s) => (s.location ?? '').startsWith('WAN')
  const isLocal = (s) => (s.location ?? '').startsWith('LAN')
  function tally(name, sessions) {
    const by = { direct_play: 0, direct_stream: 0, transcode: 0 }
    for (const s of sessions) by[s.play_method] = (by[s.play_method] ?? 0) + 1
    const kinds = Object.entries(by).filter(([, n]) => n).map(([k, n]) => `${n} ${METHOD[k]}`)
    const local = sessions.filter(isLocal)
    const remote = sessions.filter(isRemote)
    return {
      name, count: sessions.length, kinds: kinds.join(', '), kbps: sum(sessions),
      paused: sessions.filter((s) => s.state === 'paused').length,
      local: { count: local.length, kbps: sum(local) },
      remote: { count: remote.length, kbps: sum(remote) },
    }
  }
  const totals = $derived.by(() => {
    if (!data) return null
    const live = data.servers.filter((s) => s.ok)
    if (live.length === 0) return null
    const rows = live.map((s) => tally(s.name, s.sessions))
    const all = tally('Total', live.flatMap((s) => s.sessions))
    return { rows, all, showTotal: live.length > 1 }
  })

  async function refresh() {
    if (refreshing) return
    refreshing = true
    try {
      data = await api.activity()
      error = null
      updatedAt = new Date()
    } catch (e) {
      error = e.message
    } finally {
      refreshing = false
    }
  }

  // Stopping a stream (admins only): the Stop button opens a small confirmation with an optional
  // message for the viewer; the stream is found again on the server, so a stale card is refused.
  let stopping = $state(null) // `${serverId}:${sessionKey}` of the card being confirmed
  let stopMessage = $state('')
  let stopBusy = $state(false)
  let stopError = $state(null)
  const stopId = (server, s) => `${server.id}:${s.session_key}`
  function askToStop(server, s) {
    stopping = stopId(server, s)
    stopMessage = ''
    stopError = null
  }
  async function stopNow(server, s) {
    stopBusy = true
    stopError = null
    try {
      await api.stopStream({ server_id: server.id, session_key: s.session_key, reason: stopMessage })
      stopping = null
      await refresh()
    } catch (e) {
      stopError = e.message
    } finally {
      stopBusy = false
    }
  }

  onMount(refresh)

  $effect(() => {
    if (!interval) return
    const timer = setInterval(refresh, interval * 1000)
    return () => clearInterval(timer)
  })
</script>

<div class="toolbar">
  <button onclick={refresh} disabled={refreshing}>{refreshing ? 'Refreshing...' : 'Refresh now'}</button>
  <label>
    Auto-refresh
    <select value={interval} onchange={(e) => changeInterval(e.currentTarget.value)}>
      {#each INTERVALS as [n, text]}<option value={n}>{text}</option>{/each}
    </select>
  </label>
  {#if updatedAt}<span class="muted small">Updated {updatedAt.toLocaleTimeString()}</span>{/if}
</div>

{#if error}
  <p class="bad">Could not load activity: {error}</p>
{:else if !data}
  <p class="muted">Loading...</p>
{:else if data.servers.length === 0}
  <div class="card empty">
    <p>No servers yet.</p>
    {#if canSetup}
      <button class="primary" onclick={onsetup}>Add a server</button>
    {:else}
      <p class="muted">Ask an admin to add one.</p>
    {/if}
  </div>
{:else}
  {#if totals}
    <div class="totals" aria-label="Streams now">
      {#each [...totals.rows, ...(totals.showTotal ? [totals.all] : [])] as r}
        <div class="tot">
          <span class="tname">{r.name}</span>
          <strong>{streamsText(r.count)}</strong>
          <span class="tbw">{r.count ? bandwidth(r.kbps) : '—'}</span>
          {#if r.paused}<span class="tkinds">{r.paused} paused (Bandwidth Not Counted)</span>{/if}
          {#if r.count}
            <span class="tkinds">
              Local {r.local.count}{r.local.count ? ` · ${bandwidth(r.local.kbps)}` : ''}
              &nbsp;&nbsp; Remote {r.remote.count}{r.remote.count ? ` · ${bandwidth(r.remote.kbps)}` : ''}
            </span>
          {/if}
          {#if r === totals.all && r.kinds}<span class="tkinds">{r.kinds}</span>{/if}
        </div>
      {/each}
      
    </div>
  {/if}
  {#each data.servers as server (server.id)}
    <section class="server">
      <h2>
        {server.name}
        <span class="type">{server.type}</span>
        {#if !server.ok}<span class="badge bad">unreachable</span>{/if}
      </h2>
      {#if !server.ok}
        <p class="muted">{server.error}</p>
      {:else if server.sessions.length === 0}
        <p class="muted">Nothing playing</p>
      {:else}
        <div class="sessions">
          {#each server.sessions as s}
            <article class="session">
              {#if s.poster}
                <img class="poster" loading="lazy" alt=""
                  src="/api/servers/{server.id}/poster?path={encodeURIComponent(s.poster)}"
                  onerror={(e) => (e.currentTarget.style.visibility = 'hidden')} />
              {:else}
                <div class="poster"></div>
              {/if}
              <div class="body">
                <h3>{s.title}{#if s.state === 'paused'} <span class="badge paused"><svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><rect x="2" y="1" width="3" height="10" rx="1" /><rect x="7" y="1" width="3" height="10" rx="1" /></svg>Paused</span>{/if}</h3>
                <p class="who">{s.user}</p>

                {#if s.position_ms != null && s.duration_ms}
                  <div class="progress">
                    <div class="bar"><div style="width:{s.progress_percent ?? 0}%"></div></div>
                    <div class="times">
                      <span>{clock(s.position_ms)} / {clock(s.duration_ms)}</span>
                      {#if eta(s)}<span>ETA {eta(s)}</span>{/if}
                    </div>
                  </div>
                {/if}

                <dl>
                  {#each details(s) as [label, value]}
                    <dt>{label}</dt>
                    <dd class:warn={label === 'Stream' && s.play_method === 'transcode'}>{value}</dd>
                  {/each}
                </dl>

                {#if isAdmin && s.session_key}
                  {#if stopping === stopId(server, s)}
                    <div class="stop-box">
                      <p>Stop this stream? {s.user} will lose playback right away.</p>
                      <input type="text" maxlength="200" placeholder="Optional message for the viewer"
                        bind:value={stopMessage} aria-label="Message for the viewer" />
                      <div class="stop-actions">
                        <button class="danger" disabled={stopBusy} onclick={() => stopNow(server, s)}>
                          {stopBusy ? 'Stopping...' : 'Stop stream'}
                        </button>
                        <button disabled={stopBusy} onclick={() => (stopping = null)}>Cancel</button>
                      </div>
                      {#if stopError}<p class="bad small">{stopError}</p>{/if}
                    </div>
                  {:else}
                    <button class="stop" onclick={() => askToStop(server, s)}>Stop stream</button>
                  {/if}
                {/if}
              </div>
            </article>
          {/each}
        </div>
      {/if}
    </section>
  {/each}
{/if}

<style>
  .toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 16px; margin-bottom: 16px; }
  .toolbar label { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; }
  .toolbar select { width: auto; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 20px; margin-bottom: 16px; }
  .empty { text-align: center; }
  .totals { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 20px; }
  .tot { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; padding: 10px 14px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px; min-width: 150px; }
  .tot .tname { flex-basis: 100%; font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
    color: var(--muted); }
  .tot strong { font-size: 18px; }
  .tot .tbw { font-size: 15px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .tot .tkinds { flex-basis: 100%; font-size: 12.5px; color: var(--muted); }
  .server { margin-bottom: 28px; }
  h2 { margin: 0 0 12px; font-size: 17px; display: flex; align-items: center; gap: 8px; }
  .type { font-size: 12px; text-transform: uppercase; color: var(--muted); font-weight: 400; }
  .sessions { display: grid; grid-template-columns: repeat(auto-fit, minmax(440px, 1fr)); gap: 16px; }
  .session { display: flex; gap: 20px; background: var(--panel); border: 1px solid var(--border);
    border-radius: 12px; padding: 18px; }
  .poster { width: 170px; height: 255px; object-fit: cover; border-radius: 8px; flex: none;
    background: var(--border); }
  .body { min-width: 0; flex: 1; }
  h3 { margin: 0; font-size: 20px; line-height: 1.25; overflow-wrap: anywhere; }
  .who { margin: 2px 0 12px; color: var(--muted); }
  .progress { margin-bottom: 14px; }
  .bar { height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
  .bar div { height: 100%; background: var(--accent); }
  .times { display: flex; justify-content: space-between; margin-top: 4px; font-size: 13px;
    color: var(--muted); font-variant-numeric: tabular-nums; }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; margin: 0; font-size: 13.5px; }
  dt { color: var(--muted); }
  dd { margin: 0; overflow-wrap: anywhere; }
  dd.warn { color: var(--warn); }
  .small { font-size: 12px; }
  .badge { font-size: 12px; padding: 2px 8px; border-radius: 10px; border: 1px solid var(--border);
    color: var(--muted); white-space: nowrap; font-weight: 400; vertical-align: middle; }
  .badge.bad { color: var(--bad); border-color: var(--bad); }
  .badge.paused { display: inline-flex; align-items: center; gap: 5px; margin-left: 6px; padding: 3px 10px;
    font-size: 13px; font-weight: 600; color: var(--warn); border-color: var(--warn);
    background: color-mix(in srgb, var(--warn) 14%, transparent); }
  .badge.paused svg { fill: currentColor; }
  button.stop { margin-top: 14px; }
  .stop-box { margin-top: 14px; padding: 10px 12px; border: 1px solid var(--bad); border-radius: 8px; }
  .stop-box p { margin: 0 0 8px; font-size: 13.5px; }
  .stop-box input { width: 100%; margin-bottom: 8px; }
  .stop-actions { display: flex; gap: 8px; }
  button.danger { color: var(--bad); border-color: var(--bad); font-weight: 600; }
  .bad { color: var(--bad); }
  @media (max-width: 560px) {
    .sessions { grid-template-columns: 1fr; }
    .session { flex-direction: column; }
    .poster { width: 120px; height: 180px; }
  }
</style>
