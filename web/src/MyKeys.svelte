<script>
  import { onMount } from 'svelte'
  import { api } from './api.js'

  let keys = $state([])
  let name = $state('')
  let fresh = $state(null) // shown once
  let message = $state(null)
  let busy = $state(false)

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
  const load = async () => { keys = await api.myKeys() }
  onMount(() => guard(load))

  const create = () =>
    guard(async () => {
      fresh = await api.createMyKey(name)
      name = ''
      await load()
    })

  const revoke = (k) => {
    if (!confirm(`Revoke the key "${k.name}"? Anything using it stops working.`)) return
    guard(async () => {
      await api.revokeMyKey(k.id)
      if (fresh?.id === k.id) fresh = null
      await load()
    })
  }
  const when = (iso) => (iso ? new Date(iso).toLocaleString() : 'never')
</script>

<section class="card">
  <h2>Your API keys</h2>
  <p class="hint">
    A key lets another program, such as Grafana, read Horus data as you: it sees only what you can
    see here. Send it as <code>Authorization: Bearer YOUR_KEY</code> or <code>X-API-Key: YOUR_KEY</code> to
    the <code>/api/v1</code> addresses.
  </p>
  {#if fresh}
    <div class="fresh" role="status">
      <strong>Your new key for {fresh.name}</strong>
      <p class="hint">Copy it now. It is not shown again.</p>
      <input readonly value={fresh.key} onfocus={(e) => e.currentTarget.select()} aria-label="New API key" />
      <button type="button" onclick={() => (fresh = null)}>I have copied it</button>
    </div>
  {/if}
  {#each keys as k (k.id)}
    <div class="row">
      <div>
        <strong>{k.name}</strong> <code>{k.prefix}...</code>
        <div class="muted small">Created {when(k.created_at)} &middot; last used {when(k.last_used_at)}</div>
      </div>
      <button disabled={busy} onclick={() => revoke(k)}>Revoke</button>
    </div>
  {:else}
    <p class="muted">No keys yet.</p>
  {/each}
  <form onsubmit={(e) => { e.preventDefault(); create() }}>
    <input bind:value={name} placeholder="Name, for example Grafana" maxlength="60" required aria-label="Key name" />
    <button class="primary" disabled={busy}>Create key</button>
  </form>
  {#if message}<p class="bad">{message}</p>{/if}
</section>

<style>
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 20px; margin-bottom: 16px; max-width: 560px; }
  h2 { margin: 0 0 8px; font-size: 17px; }
  .hint { color: var(--muted); font-size: 12.5px; margin: 6px 0 12px; }
  .small { font-size: 12px; }
  .row { display: flex; justify-content: space-between; align-items: center; gap: 12px;
    padding: 10px 0; border-top: 1px solid var(--border); }
  form { display: flex; gap: 8px; margin-top: 12px; }
  .fresh { border: 1px solid var(--accent); border-radius: 8px; padding: 10px 14px; margin-bottom: 12px;
    display: grid; gap: 8px; }
  .bad { color: var(--bad); }
</style>
