<script>
  import { onMount } from 'svelte'
  import { api } from './api.js'

  let rows = $state(null)
  let error = $state(null)

  async function load() {
    error = null
    try {
      rows = await api.seerrRequests()
    } catch (e) {
      error = e.message
    }
  }
  onMount(load)

  const when = (iso) => (iso ? new Date(iso).toLocaleDateString() : '')
</script>

<section class="card">
  <div class="head">
    <h2>Recent requests</h2>
    <button onclick={load}>Refresh</button>
  </div>
  {#if error}
    <p class="bad">{error}</p>
  {:else if rows === null}
    <p class="muted">Loading...</p>
  {:else if rows.length === 0}
    <p class="muted">No requests yet.</p>
  {:else}
    <table>
      <thead><tr><th>Title</th><th>Type</th><th>Status</th><th>Requested by</th><th>Date</th></tr></thead>
      <tbody>
        {#each rows as r (r.id)}
          <tr>
            <td>{r.title}</td>
            <td>{r.type === 'tv' ? 'Show' : 'Movie'}</td>
            <td><span class="status {r.status.toLowerCase().replace(' ', '-')}">{r.status}</span></td>
            <td>{r.requested_by}</td>
            <td class="muted">{when(r.requested_at)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</section>

<style>
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px 20px; }
  .head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  h2 { margin: 0; font-size: 17px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th { text-align: left; color: var(--muted); font-weight: 500; padding: 6px 10px; }
  td { padding: 8px 10px; border-top: 1px solid var(--border); }
  .status { padding: 1px 8px; border-radius: 10px; border: 1px solid var(--border); font-size: 12.5px; }
  .available, .completed { color: var(--ok); border-color: var(--ok); }
  .pending { color: var(--warn); border-color: var(--warn); }
  .declined, .failed { color: var(--bad); border-color: var(--bad); }
  .bad { color: var(--bad); }
</style>
