<script>
  import { onMount } from 'svelte'
  import { api } from './api.js'

  let { onsetup, canSetup = true } = $props()
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
                <h3>{s.title}{#if s.state === 'paused'} <span class="badge">paused</span>{/if}</h3>
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
  .bad { color: var(--bad); }
  @media (max-width: 560px) {
    .sessions { grid-template-columns: 1fr; }
    .session { flex-direction: column; }
    .poster { width: 120px; height: 180px; }
  }
</style>
