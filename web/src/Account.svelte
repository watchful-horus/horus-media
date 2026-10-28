<script>
  import { api } from './api.js'
  import MyKeys from './MyKeys.svelte'

  let { user } = $props()

  let pw = $state({ current: '', next: '', again: '' })
  let message = $state(null)
  let ok = $state(true)
  let busy = $state(false)

  async function change() {
    message = null
    if (pw.next.length < 8) { message = 'Use at least 8 characters.'; ok = false; return }
    if (pw.next !== pw.again) { message = 'The two new passwords do not match.'; ok = false; return }
    busy = true
    try {
      await api.changePassword(pw.current, pw.next)
      pw = { current: '', next: '', again: '' }
      message = 'Password changed. Your other signed-in browsers were signed out.'
      ok = true
    } catch (e) {
      message = e.message
      ok = false
    } finally {
      busy = false
    }
  }
</script>

<section class="card">
  <h2>Your account</h2>
  <dl>
    <dt>Username</dt><dd>{user.username}</dd>
    <dt>Role</dt><dd>{user.role === 'admin' ? 'Admin' : 'User'}</dd>
  </dl>
</section>

<section class="card">
  <h2>Change password</h2>
  <form onsubmit={(e) => { e.preventDefault(); change() }}>
    <label>Current password<input type="password" bind:value={pw.current} autocomplete="current-password" required /></label>
    <label>New password<input type="password" bind:value={pw.next} autocomplete="new-password" required /></label>
    <label>Repeat new password<input type="password" bind:value={pw.again} autocomplete="new-password" required /></label>
    <div class="actions">
      <button class="primary" disabled={busy}>Change password</button>
      {#if message}<span class={ok ? 'okmsg' : 'bad'}>{message}</span>{/if}
    </div>
  </form>
</section>

{#if user.role !== 'admin'}
  <MyKeys />
{/if}

<style>
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 20px; margin-bottom: 16px; max-width: 560px; }
  h2 { margin: 0 0 10px; font-size: 17px; }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; margin: 0; }
  dt { color: var(--muted); }
  dd { margin: 0; }
  form { display: grid; gap: 12px; }
  label { display: grid; gap: 4px; font-size: 13px; color: var(--muted); }
  .actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .okmsg { color: var(--ok); }
  .bad { color: var(--bad); }
</style>
