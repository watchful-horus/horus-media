<script>
  import { onMount } from 'svelte'
  import { api } from './api.js'

  let { me } = $props() // the signed-in admin

  let users = $state([])
  let form = $state({ username: '', password: '', role: 'user', media_username: '' })
  let resetFor = $state(null) // id of the user whose password is being reset
  let resetPassword = $state('')
  let message = $state(null)
  let ok = $state(true)
  let busy = $state(false)

  async function guard(fn, okText) {
    busy = true
    message = null
    try {
      await fn()
      if (okText) { message = okText; ok = true }
    } catch (e) {
      message = e.message
      ok = false
    } finally {
      busy = false
    }
  }

  const load = async () => { users = await api.users() }
  onMount(() => guard(load))

  const add = () =>
    guard(async () => {
      await api.createUser(form)
      form = { username: '', password: '', role: 'user', media_username: '' }
      await load()
    }, 'User added.')

  const setRole = (u, role) =>
    guard(async () => {
      await api.updateUser(u.id, { role })
      await load()
    }, `${u.username} is now ${role === 'admin' ? 'an admin' : 'a normal user'}.`)

  let linkDraft = $state({}) // user id -> text being typed
  const saveLink = (u) =>
    guard(async () => {
      await api.updateUser(u.id, { media_username: linkDraft[u.id] ?? u.media_username ?? '' })
      delete linkDraft[u.id]
      await load()
    }, `Saved the Plex/Emby name for ${u.username}.`)

  const reset = (u) =>
    guard(async () => {
      await api.updateUser(u.id, { password: resetPassword })
      resetFor = null
      resetPassword = ''
    }, `Password reset for ${u.username}. They were signed out everywhere.`)

  const remove = (u) => {
    if (!confirm(`Delete ${u.username}? They are signed out straight away and can't sign in again.`)) return
    guard(async () => {
      await api.deleteUser(u.id)
      await load()
    }, `${u.username} was deleted.`)
  }

  const when = (iso) => (iso ? new Date(iso).toLocaleString() : 'never')
</script>

<section class="card">
  <h2>People who can sign in</h2>
  <p class="hint">
    <strong>Admins</strong> can do everything: servers, settings, API keys, backups, logs and
    people. <strong>Normal users</strong> can look at Activity and Graphs and change their own
    password, nothing else.
  </p>

  {#each users as u (u.id)}
    <div class="row">
      <div class="who">
        <strong>{u.username}</strong>
        {#if u.id === me.id}<span class="badge">you</span>{/if}
        <span class="badge" class:admin={u.role === 'admin'}>{u.role === 'admin' ? 'Admin' : 'User'}</span>
        <div class="muted small">Last signed in {when(u.last_login_at)}</div>
        {#if u.role !== 'admin'}
          <form class="link" onsubmit={(e) => { e.preventDefault(); saveLink(u) }}>
            <input value={linkDraft[u.id] ?? u.media_username ?? ''} oninput={(e) => (linkDraft[u.id] = e.currentTarget.value)}
              placeholder="Their Plex/Emby name (for private activity)" aria-label="Plex or Emby name for {u.username}" maxlength="100" />
            <button disabled={busy || (linkDraft[u.id] ?? u.media_username ?? '') === (u.media_username ?? '')}>Save name</button>
          </form>
        {/if}

        {#if resetFor === u.id}
          <form class="reset" onsubmit={(e) => { e.preventDefault(); reset(u) }}>
            <input type="password" bind:value={resetPassword} placeholder="New password (8+ characters)" autocomplete="new-password" required aria-label="New password for {u.username}" />
            <button class="primary" disabled={busy}>Set password</button>
            <button type="button" onclick={() => { resetFor = null; resetPassword = '' }}>Cancel</button>
          </form>
        {/if}
      </div>
      <div class="actions">
        {#if u.id !== me.id}
          <select value={u.role} disabled={busy} onchange={(e) => setRole(u, e.currentTarget.value)} aria-label="Role for {u.username}">
            <option value="user">User</option>
            <option value="admin">Admin</option>
          </select>
          <button disabled={busy} onclick={() => { resetFor = u.id; resetPassword = '' }}>Reset password</button>
          <button disabled={busy} onclick={() => remove(u)}>Delete</button>
        {:else}
          <span class="muted small">Change your own password under Account.</span>
        {/if}
      </div>
    </div>
  {/each}
</section>

<section class="card">
  <h2>Add a person</h2>
  <form class="add" onsubmit={(e) => { e.preventDefault(); add() }}>
    <label>Username
      <input bind:value={form.username} autocomplete="off" required minlength="3" maxlength="32" placeholder="3 to 32 letters, numbers, . - _" />
    </label>
    <label>Password
      <input type="password" bind:value={form.password} autocomplete="new-password" required minlength="8" placeholder="At least 8 characters" />
    </label>
    <label>Plex/Emby name (optional)
      <input bind:value={form.media_username} autocomplete="off" maxlength="100" placeholder="Used when users only see their own activity" />
    </label>
    <label>Role
      <select bind:value={form.role}>
        <option value="user">User: can view Activity and Graphs</option>
        <option value="admin">Admin: can do everything</option>
      </select>
    </label>
    <div class="actions"><button class="primary" disabled={busy}>Add user</button></div>
  </form>
  <p class="hint">Tell them their username and password yourself. They can change the password after signing in.</p>
  {#if message}<p class={ok ? 'okmsg' : 'bad'}>{message}</p>{/if}
</section>

<style>
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 20px; margin-bottom: 16px; }
  h2 { margin: 0 0 8px; font-size: 17px; }
  .hint { color: var(--muted); font-size: 12.5px; margin: 6px 0 12px; max-width: 700px; }
  .small { font-size: 12px; }
  .row { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px;
    padding: 12px 0; border-top: 1px solid var(--border); flex-wrap: wrap; }
  .who { min-width: 0; flex: 1; }
  .actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .actions select { width: auto; }
  .badge { font-size: 11.5px; padding: 1px 8px; border-radius: 10px; border: 1px solid var(--border);
    color: var(--muted); margin-left: 4px; }
  .badge.admin { color: var(--accent); border-color: var(--accent); }
  .link { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
  .link input { width: 300px; }
  .reset { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
  .reset input { width: 260px; }
  .add { display: grid; gap: 12px; max-width: 460px; }
  label { display: grid; gap: 4px; font-size: 13px; color: var(--muted); }
  .okmsg { color: var(--ok); }
  .bad { color: var(--bad); }
</style>
