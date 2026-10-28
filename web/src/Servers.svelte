<script>
  import { onDestroy, onMount } from 'svelte'
  import { api } from './api.js'

  let servers = $state([])
  let results = $state({}) // id -> test result for saved servers
  let form = $state({ type: 'plex', name: '', url: '', token: '' })
  let formResult = $state(null)
  let message = $state(null)
  let busy = $state(false)

  // "Sign in with Plex": idle -> waiting (sign-in open in another tab) -> choose a server
  let link = $state({ phase: 'idle', pinId: null, authUrl: '', servers: [], picks: {} })
  let showShared = $state(false)
  let shown = $derived(link.servers.filter((s) => s.owned || showShared))
  let sharedCount = $derived(link.servers.filter((s) => !s.owned).length)
  let timer = null
  const stopPolling = () => { clearInterval(timer); timer = null }
  onDestroy(stopPolling)

  async function startLink() {
    // Opened synchronously from the click so popup blockers allow it.
    const win = window.open('', '_blank')
    message = null
    try {
      const { pin_id, auth_url } = await api.plexLinkStart()
      link = { phase: 'waiting', pinId: pin_id, authUrl: auth_url, servers: [], picks: {} }
      if (win) win.location = auth_url
      const deadline = Date.now() + 5 * 60 * 1000
      stopPolling()
      timer = setInterval(async () => {
        try {
          const r = await api.plexLinkStatus(pin_id)
          if (r.status === 'done') {
            stopPolling()
            const picks = Object.fromEntries(r.servers.map((s) => [s.id, s.connections[0]?.url ?? '']))
            link = { ...link, phase: 'choose', servers: r.servers, picks }
          } else if (r.status === 'expired' || Date.now() > deadline) {
            stopPolling()
            link = { ...link, phase: 'idle' }
            message = 'Plex sign-in timed out. Please try again.'
          }
        } catch (e) {
          stopPolling()
          link = { ...link, phase: 'idle' }
          message = e.message
        }
      }, 2000)
    } catch (e) {
      win?.close()
      message = e.message
    }
  }

  function cancelLink() {
    stopPolling()
    link = { phase: 'idle', pinId: null, authUrl: '', servers: [], picks: {} }
  }

  const addFromPlex = (s) =>
    guard(async () => {
      await api.plexLinkAdd(link.pinId, { resource_id: s.id, url: link.picks[s.id] })
      link = { ...link, servers: link.servers.filter((x) => x.id !== s.id) }
      await load()
      if (link.servers.length === 0) cancelLink()
      else if (shown.length === 0 && !showShared) message = 'Added. Only shared servers are left; tick the box above to see them.'
    })

  const PLACEHOLDER = { plex: 'http://host:32400', emby: 'http://host:8096' }

  async function load() {
    servers = await api.servers()
  }
  onMount(() => guard(load))

  async function guard(fn) {
    busy = true
    message = null
    try {
      await fn()
    } catch (e) {
      message = e.message
    } finally {
      busy = false
    }
  }

  const describe = (r) =>
    r.ok ? `Connected${r.version ? ' (v' + r.version + ')' : ''}` : r.error

  const testForm = () =>
    guard(async () => {
      const { type, url, token } = form
      formResult = await api.testUnsaved({ type, url, token })
    })

  const add = () =>
    guard(async () => {
      await api.addServer({ ...form })
      form = { type: form.type, name: '', url: '', token: '' }
      formResult = null
      await load()
    })

  const testSaved = (id) =>
    guard(async () => {
      results[id] = await api.testSaved(id)
    })

  let imported = $state({}) // server id -> message after an import
  const importHistory = (s) => {
    if (!confirm(`Import ${s.name}'s past plays into Horus? Nothing changes on the server. You only need to do this once; running it again adds nothing new.`)) return
    guard(async () => {
      try {
        const r = await api.importHistory(s.id)
        imported[s.id] = { ok: true, text: `Imported ${r.added} past play${r.added === 1 ? '' : 's'} (${r.skipped} already in Horus).` }
      } catch (e) {
        imported[s.id] = { ok: false, text: e.message }
      }
    })
  }

  const remove = (s) => {
    if (!confirm(`Remove ${s.name}? Horus will stop monitoring it. Nothing changes on the server.`)) return
    guard(async () => {
      await api.deleteServer(s.id)
      await load()
    })
  }
</script>

<section class="card">
  <h2>Connected servers</h2>
  {#if servers.length === 0}
    <p class="muted">None yet. Add one below.</p>
  {/if}
  {#each servers as s (s.id)}
    <div class="row">
      <div>
        <strong>{s.name}</strong> <span class="type">{s.type}</span>
        <div class="muted small">{s.url}</div>
        {#if results[s.id]}
          <div class="small" class:ok={results[s.id].ok} class:bad={!results[s.id].ok}>
            {describe(results[s.id])}
          </div>
        {/if}
        {#if imported[s.id]}
          <div class="small" class:ok={imported[s.id].ok} class:bad={!imported[s.id].ok}>{imported[s.id].text}</div>
        {/if}
        <div class="muted small note">
          {#if s.type === 'emby'}
            To import Emby's past plays, install the Playback Reporting plugin in Emby (Dashboard &gt; Plugins &gt; Catalogue).
            It only has plays from after it was installed.
            Import once, soon after adding the server: Emby may name a play differently than Horus saw it live, so a play recorded both ways can count twice.
          {:else}
            <strong>Import history</strong> copies this server's past plays into Horus. Do it once, right after adding the server.
            Running it again won't add duplicates, but there is nothing new to gain.
          {/if}
        </div>
      </div>
      <div class="actions">
        <button disabled={busy} onclick={() => testSaved(s.id)}>Test</button>
        <button disabled={busy} onclick={() => importHistory(s)} title={s.type === 'emby' ? 'Needs the Playback Reporting plugin' : 'Reads the history Plex keeps'}>Import history</button>
        <button disabled={busy} onclick={() => remove(s)}>Remove</button>
      </div>
    </div>
  {/each}
</section>

<section class="card">
  <h2>Add a Plex server</h2>
  {#if link.phase === 'idle'}
    <p class="muted small">
      The easiest way: sign in with your Plex account and pick a server. Horus only reads from it.
    </p>
    <button class="primary" disabled={busy} onclick={startLink}>Sign in with Plex</button>
  {:else if link.phase === 'waiting'}
    <p>Waiting for you to sign in on plex.tv...</p>
    <p class="muted small">
      A new tab should have opened. If not,
      <a href={link.authUrl} target="_blank" rel="noopener noreferrer">open the sign-in page</a>.
    </p>
    <button onclick={cancelLink}>Cancel</button>
  {:else}
    <p class="muted small">Signed in. Choose how Horus should reach each server.</p>
    {#if shown.length === 0}<p class="muted">No Plex servers of your own found on this account.</p>{/if}
    {#if sharedCount > 0}
      <label class="check">
        <input type="checkbox" bind:checked={showShared} />
        Also show {sharedCount} server{sharedCount === 1 ? '' : 's'} shared with me
      </label>
      {#if showShared}
        <p class="muted small">Plex usually only lets a server's owner see what is playing, so activity will probably not work for these.</p>
      {/if}
    {/if}
    {#each shown as s (s.id)}
      <div class="row">
        <div class="grow">
          <strong>{s.name}</strong>
          {#if !s.owned}<span class="type">shared with you</span>{/if}
          <select bind:value={link.picks[s.id]} aria-label="Address for {s.name}">
            {#each s.connections as c}
              <option value={c.url}>{c.local ? 'Local' : 'Remote'}: {c.url}</option>
            {/each}
          </select>
        </div>
        <button class="primary" disabled={busy || !link.picks[s.id]} onclick={() => addFromPlex(s)}>Add</button>
      </div>
    {/each}
    <button onclick={cancelLink}>Done</button>
  {/if}
  {#if message}<p class="bad">{message}</p>{/if}
</section>

<section class="card">
  <h2>Add a server manually</h2>
  <p class="muted small">For Emby, or a Plex server you would rather connect with a token.</p>
  <form onsubmit={(e) => { e.preventDefault(); add() }}>
    <label>Type
      <select bind:value={form.type}>
        <option value="plex">Plex</option>
        <option value="emby">Emby</option>
      </select>
    </label>
    <label>Name <input bind:value={form.name} placeholder="Living room" required /></label>
    <label>URL <input bind:value={form.url} placeholder={PLACEHOLDER[form.type]} required /></label>
    <label>{form.type === 'plex' ? 'Plex token' : 'Emby API key'}
      <input type="password" bind:value={form.token} autocomplete="off" required />
    </label>
    <p class="muted small">The token is stored encrypted and is never shown again.</p>
    <div class="actions">
      <button type="button" disabled={busy || !form.url || !form.token} onclick={testForm}>
        Test connection
      </button>
      <button class="primary" disabled={busy}>Add server</button>
    </div>
    {#if formResult}
      <p class:ok={formResult.ok} class:bad={!formResult.ok}>{describe(formResult)}</p>
    {/if}
    {#if message}<p class="bad">{message}</p>{/if}
  </form>
</section>

<style>
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 20px; margin-bottom: 16px; }
  h2 { margin: 0 0 12px; font-size: 17px; }
  .row { display: flex; justify-content: space-between; align-items: center; gap: 16px;
    padding: 12px 0; border-top: 1px solid var(--border); }
  /* The text may wrap; the buttons never shrink, so every row's buttons line up. */
  .row > div:first-child { flex: 1; min-width: 0; overflow-wrap: anywhere; }
  .type { font-size: 12px; text-transform: uppercase; color: var(--muted); }
  .small { font-size: 12px; }
  .note { margin-top: 4px; max-width: 62ch; }
  .actions { display: grid; grid-template-columns: repeat(3, 120px); gap: 8px; flex: none; }
  .actions > button { min-width: 0; }
  .grow { flex: 1; display: grid; gap: 6px; }
  a { color: var(--accent); }
  .check { display: flex; align-items: center; gap: 8px; margin: 8px 0; }
  .check input { width: auto; }
  form { display: grid; gap: 12px; max-width: 460px; }
  label { display: grid; gap: 4px; font-size: 13px; color: var(--muted); }
  .ok { color: var(--ok); }
  .bad { color: var(--bad); }
</style>
