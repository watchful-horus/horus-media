<script>
  import { onMount } from 'svelte'
  import { api } from './api.js'

  let events = $state({})
  let kinds = $state([])
  let channels = $state([])
  let form = $state({ name: '', kind: 'discord', url: '', events: ['server_down', 'server_up'] })
  let seerr = $state({ configured: false, url: '' })
  let seerrForm = $state({ url: '', api_key: '' })
  let results = $state({}) // channel id -> test outcome
  let seerrResult = $state(null)
  let message = $state(null)
  let busy = $state(false)

  const KIND_TEXT = {
    discord: 'Discord (channel webhook address)',
    ntfy: 'ntfy (full topic address)',
    webhook: 'Any web address (receives JSON)',
  }

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

  async function load() {
    const n = await api.notifications()
    events = n.events
    kinds = n.kinds
    channels = n.channels
    seerr = await api.seerr()
    seerrForm.url = seerr.url
  }
  onMount(() => guard(load))

  const toggleEvent = (id) => {
    form.events = form.events.includes(id) ? form.events.filter((e) => e !== id) : [...form.events, id]
  }

  const add = () =>
    guard(async () => {
      await api.addChannel({ ...form })
      form = { ...form, name: '', url: '' }
      await load()
    })
  const test = (c) =>
    guard(async () => {
      results[c.id] = await api.testChannel(c.id)
    })
  const pause = (c) =>
    guard(async () => {
      await api.updateChannel(c.id, { enabled: !c.enabled })
      await load()
    })
  const remove = (c) => {
    if (!confirm(`Remove the channel "${c.name}"?`)) return
    guard(async () => {
      await api.deleteChannel(c.id)
      await load()
    })
  }

  const saveSeerr = () =>
    guard(async () => {
      seerr = await api.saveSeerr({ url: seerrForm.url, api_key: seerrForm.api_key || undefined })
      seerrForm.api_key = ''
      seerrResult = null
      window.dispatchEvent(new Event('horus:seerr'))
    })
  const testSeerr = () =>
    guard(async () => {
      const r = await api.testSeerr()
      seerrResult = r.ok ? `Connected (Seerr ${r.version}).` : r.error
    })
  const removeSeerr = () => {
    if (!confirm('Disconnect Seerr? Horus forgets the address and key.')) return
    guard(async () => {
      await api.deleteSeerr()
      seerrForm = { url: '', api_key: '' }
      seerrResult = null
      await load()
      window.dispatchEvent(new Event('horus:seerr'))
    })
  }
</script>

<section class="card">
  <h2>Notifications</h2>
  <p class="hint">
    Horus can post a message to Discord, ntfy or any web address when something happens. The address
    you enter is a secret: it is stored encrypted and never shown again, only its host name.
  </p>
  {#each channels as c (c.id)}
    <div class="row">
      <div>
        <strong>{c.name}</strong> <span class="badge">{c.kind}</span>
        {#if !c.enabled}<span class="badge">paused</span>{/if}
        <div class="muted small">Sends to {c.host} &middot; {c.events.map((e) => events[e]).join(', ')}</div>
        {#if results[c.id]}
          <div class="small" class:okmsg={results[c.id].ok} class:bad={!results[c.id].ok}>
            {results[c.id].ok ? 'Test message sent.' : results[c.id].error}
          </div>
        {/if}
      </div>
      <div class="actions">
        <button disabled={busy} onclick={() => test(c)}>Send test</button>
        <button disabled={busy} onclick={() => pause(c)}>{c.enabled ? 'Pause' : 'Resume'}</button>
        <button disabled={busy} onclick={() => remove(c)}>Remove</button>
      </div>
    </div>
  {:else}
    <p class="muted">No channels yet.</p>
  {/each}

  <h3>Add a channel</h3>
  <form onsubmit={(e) => { e.preventDefault(); add() }}>
    <label>Name<input bind:value={form.name} maxlength="60" required placeholder="For example Phone" /></label>
    <label>Type
      <select bind:value={form.kind}>
        {#each kinds as k}<option value={k}>{KIND_TEXT[k] ?? k}</option>{/each}
      </select>
    </label>
    <label>Address<input type="url" bind:value={form.url} required autocomplete="off" placeholder="https://..." /></label>
    <fieldset>
      <legend>Send a message when</legend>
      {#each Object.entries(events) as [id, text]}
        <label class="check"><input type="checkbox" checked={form.events.includes(id)} onchange={() => toggleEvent(id)} /> {text}</label>
      {/each}
    </fieldset>
    <div class="actions"><button class="primary" disabled={busy || form.events.length === 0}>Add channel</button></div>
  </form>
  {#if message}<p class="bad">{message}</p>{/if}
</section>

<section class="card">
  <h2>Seerr requests</h2>
  <p class="hint">
    Connect Overseerr or Jellyseerr to see recent media requests in Horus (a Requests tab appears).
    Horus only reads from Seerr. Find the key in Seerr under Settings &gt; General.
  </p>
  <form onsubmit={(e) => { e.preventDefault(); saveSeerr() }}>
    <label>Seerr address<input type="url" bind:value={seerrForm.url} required placeholder="http://seerr:5055" /></label>
    <label>API key
      <input type="password" bind:value={seerrForm.api_key} autocomplete="off"
        placeholder={seerr.configured ? 'Saved. Leave blank to keep it.' : 'Paste the Seerr API key'} />
    </label>
    <div class="actions">
      <button class="primary" disabled={busy}>{seerr.configured ? 'Save' : 'Connect'}</button>
      {#if seerr.configured}
        <button type="button" disabled={busy} onclick={testSeerr}>Test</button>
        <button type="button" disabled={busy} onclick={removeSeerr}>Disconnect</button>
      {/if}
      {#if seerrResult}<span class="small">{seerrResult}</span>{/if}
    </div>
  </form>
</section>

<style>
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 20px; margin-bottom: 16px; }
  h2 { margin: 0 0 8px; font-size: 17px; }
  h3 { margin: 18px 0 8px; font-size: 14px; }
  .hint { color: var(--muted); font-size: 12.5px; margin: 6px 0 12px; max-width: 700px; }
  .small { font-size: 12px; }
  .row { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px;
    padding: 12px 0; border-top: 1px solid var(--border); flex-wrap: wrap; }
  .actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .badge { font-size: 11.5px; padding: 1px 8px; border-radius: 10px; border: 1px solid var(--border);
    color: var(--muted); margin-left: 4px; }
  form { display: grid; gap: 12px; max-width: 520px; }
  label { display: grid; gap: 4px; font-size: 13px; color: var(--muted); }
  label.check { display: flex; gap: 8px; align-items: center; color: var(--text); }
  label.check input { width: auto; }
  fieldset { border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; display: grid; gap: 6px; }
  legend { font-size: 13px; color: var(--muted); padding: 0 4px; }
  .okmsg { color: var(--ok); }
  .bad { color: var(--bad); }
</style>
