<script>
  import { api } from './api.js'

  // mode: 'setup' (first run: choose the admin password) or 'login'
  let { mode, needsCode = false, ondone } = $props()

  let username = $state('')
  let password = $state('')
  let confirm = $state('')
  let code = $state('')
  let error = $state(null)
  let busy = $state(false)
  let restoring = $state(false) // the "restore from a backup instead" panel is open
  let restoreCode = $state('')

  async function restoreFrom(e) {
    const file = e.currentTarget.files?.[0]
    e.currentTarget.value = ''
    if (!file) return
    error = null
    busy = true
    try {
      await api.restoreAtSetup(file, restoreCode)
      ondone() // the backup's accounts are in place now: shows the sign-in screen
    } catch (err) {
      error = err.message
    } finally {
      busy = false
    }
  }

  async function submit() {
    error = null
    if (mode === 'setup') {
      if (password.length < 8) { error = 'Use at least 8 characters.'; return }
      if (password !== confirm) { error = 'The two passwords do not match.'; return }
    }
    busy = true
    try {
      if (mode === 'setup') await api.setup(username, password, code)
      else await api.login(username, password)
      password = confirm = ''
      ondone()
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }
</script>

<main>
  <form class="card" onsubmit={(e) => { e.preventDefault(); submit() }}>
    <h1>Horus</h1>
    {#if mode === 'setup'}
      <p class="muted">Welcome. Choose a username and password for the first account. It will be the admin. Anyone who can reach this page will need to sign in.</p>
    {:else}
      <p class="muted">Sign in to continue.</p>
    {/if}

    <label>
      Username
      <input bind:value={username} autocomplete="username" required minlength="3" maxlength="32"
        placeholder={mode === 'setup' ? '3 to 32 letters, numbers, . - _' : ''} autofocus />
    </label>
    <label>
      Password
      <input type="password" bind:value={password} autocomplete={mode === 'setup' ? 'new-password' : 'current-password'} required />
    </label>
    {#if mode === 'setup'}
      <label>
        Confirm password
        <input type="password" bind:value={confirm} autocomplete="new-password" required />
      </label>
      {#if needsCode}
        <label>
          Setup code
          <input bind:value={code} autocomplete="off" placeholder="Shown in the Horus console or log" required />
        </label>
        <p class="muted small">You are not on the computer running Horus, so enter the code it printed when it started.</p>
      {/if}
    {/if}
    {#if error}<p class="bad" role="alert">{error}</p>{/if}
    <button class="primary" disabled={busy}>{mode === 'setup' ? 'Create account' : 'Sign in'}</button>
  </form>
  {#if mode === 'setup'}
    <div class="card restore">
      {#if !restoring}
        <button type="button" class="link" onclick={() => (restoring = true)}>Already use Horus? Restore from a backup instead</button>
      {:else}
        <p class="muted small">
          Choose a Horus backup (the zip you downloaded from Settings &gt; Backups). Its
          accounts, servers and history come back, and you sign in with the account from that backup.
        </p>
        {#if needsCode}
          <label>
            Setup code
            <input bind:value={restoreCode} autocomplete="off" placeholder="Shown in the Horus console or log" />
          </label>
        {/if}
        <input type="file" accept=".zip,.db" disabled={busy || (needsCode && !restoreCode)} onchange={restoreFrom} aria-label="Backup file" />
        {#if error}<p class="bad" role="alert">{error}</p>{/if}
      {/if}
    </div>
  {/if}
</main>

<style>
  main { min-height: 100vh; display: grid; place-items: center; align-content: center; gap: 14px; padding: 24px; }
  .restore { padding: 16px 28px; }
  .link { border: 0; background: none; padding: 0; color: var(--muted); text-decoration: underline; text-align: left; }
  .card { width: min(380px, 100%); background: var(--panel); border: 1px solid var(--border);
    border-radius: 12px; padding: 28px; display: grid; gap: 14px; }
  h1 { margin: 0; font-size: 22px; }
  p { margin: 0; }
  label { display: grid; gap: 4px; font-size: 13px; color: var(--muted); }
  .small { font-size: 12.5px; }
  .bad { color: var(--bad); }
</style>
