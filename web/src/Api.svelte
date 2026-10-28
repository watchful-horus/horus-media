<script>
  import { onMount } from 'svelte'
  import { api } from './api.js'

  let keys = $state([])
  let name = $state('Grafana')
  let fresh = $state(null) // the key just created: shown once
  let copied = $state(null)
  let info = $state(null)
  let message = $state(null)
  let busy = $state(false)

  const base = location.origin

  const ENDPOINTS = [
    ['/api/v1/plays', 'Plays per hour, day, week or month.', '?from=${__from}&to=${__to}&interval=day&by=server',
      'interval: hour | day | week | month. by: none | server | user | platform | play_method | media_type'],
    ['/api/v1/bandwidth', 'Average and peak bandwidth per server, plus a combined Total.', '?from=${__from}&to=${__to}',
      'interval: auto | 1m | 5m | 15m | 1h | 1d'],
    ['/api/v1/history', 'Individual plays, newest first.', '?from=${__from}&to=${__to}&limit=1000',
      'Also: server, user, offset. Up to 10000 rows.'],
    ['/api/v1/activity', 'What is playing right now, one row per stream.', '', 'Empty when nothing is playing.'],
    ['/api/v1/servers', 'The servers Horus watches (names and types only).', '', ''],
  ]

  async function load() {
    keys = await api.keys()
    info = (await api.settings()).info
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
  onMount(() => guard(load))

  const create = () =>
    guard(async () => {
      fresh = await api.createKey(name)
      name = ''
      await load()
    })

  const revoke = (k) => {
    if (!confirm(`Revoke the key "${k.name}"? Anything using it (like a Grafana dashboard) stops working.`)) return
    guard(async () => {
      await api.revokeKey(k.id)
      if (fresh?.id === k.id) fresh = null
      await load()
    })
  }

  async function copy(text, id) {
    try {
      await navigator.clipboard.writeText(text)
      copied = id
      setTimeout(() => (copied = null), 1500)
    } catch {
      message = 'Your browser blocked copying here. Select the text and copy it by hand.'
    }
  }

  const when = (iso) => (iso ? new Date(iso).toLocaleString() : 'never')
  const closed = $derived(info && info.listen.host_running === '127.0.0.1' && info.listen.host_source !== 'command')
</script>

<section class="card">
  <h2>API keys</h2>
  <p class="hint">
    Use an API key to let another program, such as Grafana, read your Horus data. A key can only read
    the data endpoints below. It can't change anything, add servers or see your server tokens.
  </p>

  {#if fresh}
    <div class="fresh" role="status">
      <strong>Your new key for {fresh.name}</strong>
      <p class="hint">Copy it now. For your security it is not shown again.</p>
      <div class="copyrow">
        <input readonly value={fresh.key} onfocus={(e) => e.currentTarget.select()} aria-label="New API key" />
        <button type="button" onclick={() => copy(fresh.key, 'key')}>{copied === 'key' ? 'Copied' : 'Copy'}</button>
      </div>
      <button type="button" class="link" onclick={() => (fresh = null)}>I have copied it</button>
    </div>
  {/if}

  {#if keys.length === 0}
    <p class="muted">No keys yet.</p>
  {/if}
  {#each keys as k (k.id)}
    <div class="row">
      <div>
        <strong>{k.name}</strong> <code>{k.prefix}...</code>{#if k.owner} <span class="muted small">({k.owner}'s)</span>{/if}
        <div class="muted small">Created {when(k.created_at)} &middot; last used {when(k.last_used_at)}</div>
      </div>
      <button disabled={busy} onclick={() => revoke(k)}>Revoke</button>
    </div>
  {/each}

  <form class="create" onsubmit={(e) => { e.preventDefault(); create() }}>
    <input bind:value={name} placeholder="Name, for example Grafana" maxlength="60" required aria-label="Key name" />
    <button class="primary" disabled={busy}>Create key</button>
  </form>
  {#if message}<p class="bad">{message}</p>{/if}
</section>

{#if closed}
  <section class="card warn">
    <h2>Horus only accepts connections from this computer</h2>
    <p class="hint">
      To let Grafana on another computer in, turn on <strong>Allow other computers to connect</strong>
      under Settings &gt; General &gt; Network, then restart Horus. Your admin password and API keys
      protect it.
    </p>
  </section>
{/if}

<section class="card">
  <h2>Data endpoints</h2>
  <p class="hint">
    All are read-only and return JSON (add <code>&format=csv</code> for CSV). Send your key as
    <code>Authorization: Bearer YOUR_KEY</code> or <code>X-API-Key: YOUR_KEY</code>. Times are ISO 8601, and
    <code>from</code> / <code>to</code> accept dates, date-times or Grafana's epoch milliseconds.
  </p>
  {#each ENDPOINTS as [path, text, query, notes], i}
    <div class="endpoint">
      <div><code class="path">GET {path}</code> <span class="muted">{text}</span></div>
      {#if notes}<div class="muted small">{notes}</div>{/if}
      <div class="copyrow">
        <input readonly value="{base}{path}{query}" onfocus={(e) => e.currentTarget.select()} aria-label="Example address for {path}" />
        <button type="button" onclick={() => copy(`${base}${path}${query}`, i)}>{copied === i ? 'Copied' : 'Copy'}</button>
      </div>
    </div>
  {/each}
  <p class="hint">
    Try one from a terminal:
    <code>curl -H "Authorization: Bearer YOUR_KEY" "{base}/api/v1/plays?interval=day&by=server"</code>
  </p>
</section>

<section class="card">
  <h2>Using it in Grafana</h2>
  <ol class="steps">
    <li>Install the <strong>Infinity</strong> data source plugin (<code>yesoreyeram-infinity-datasource</code>).</li>
    <li>Add a data source of that type. Under Authentication choose <strong>Bearer Token</strong> and paste your key. Add <code>{base}</code> to the allowed hosts.</li>
    <li>In a panel, pick that data source with type <strong>JSON</strong>, parser <strong>Backend</strong>, and one of the addresses above, with Grafana's time variables as shown.</li>
    <li>Set the columns, for example <code>time</code> as Timestamp, <code>server</code> as String and <code>plays</code> as Number. Format <strong>Time series</strong> turns the <code>server</code> column into separate lines.</li>
  </ol>
  <p class="hint">
    Grafana makes these requests itself from where it runs, so the address it uses must reach Horus
    from there (not <code>localhost</code> unless both run on the same computer).
  </p>
</section>

<style>
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 20px; margin-bottom: 16px; }
  .warn { border-color: var(--warn); }
  h2 { margin: 0 0 8px; font-size: 17px; }
  .hint { color: var(--muted); font-size: 12.5px; margin: 6px 0 12px; max-width: 700px; }
  .small { font-size: 12px; }
  code { font-size: 12.5px; background: var(--bg); padding: 1px 5px; border-radius: 4px; overflow-wrap: anywhere; }
  .path { font-weight: 600; }
  .row { display: flex; justify-content: space-between; align-items: center; gap: 12px;
    padding: 10px 0; border-top: 1px solid var(--border); }
  .create { display: flex; gap: 8px; margin-top: 12px; max-width: 460px; }
  .fresh { border: 1px solid var(--accent); border-radius: 8px; padding: 12px 14px; margin-bottom: 14px; }
  .fresh .hint { margin: 2px 0 8px; }
  .copyrow { display: flex; gap: 8px; margin: 6px 0 2px; }
  .copyrow input { font: 12.5px ui-monospace, Consolas, monospace; }
  .endpoint { padding: 10px 0; border-top: 1px solid var(--border); }
  .steps { margin: 0 0 8px; padding-left: 20px; display: grid; gap: 6px; font-size: 14px; }
  .link { border: 0; background: none; padding: 4px 0; color: var(--muted); text-decoration: underline; font-size: 12.5px; }
  .bad { color: var(--bad); }
</style>
