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
  let hideOthers = $state(false) // Settings > "Normal users see only their own activity"

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

  const load = async () => {
    users = await api.users()
    hideOthers = !!(await api.settings()).settings.hide_others_activity
  }
  onMount(() => guard(load))

  const savePrivacy = () =>
    guard(async () => {
      await api.saveSettings({ hide_others_activity: hideOthers })
    }, hideOthers ? 'Normal users now see only their own activity.' : 'Normal users now see everyone\'s activity.')

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
  const initial = (u) => (u.username || '?').trim().charAt(0).toUpperCase()
</script>

<section class="card">
  <h2>People who can sign in</h2>
  <p class="hint">
    <strong>Admins</strong> can do everything: servers, settings, API keys, backups, logs and
    people. <strong>Normal users</strong> can look at Activity and Graphs and change their own
    password, nothing else.
  </p>

  <ul class="people">
    {#each users as u (u.id)}
      <li class="person">
        <span class="avatar" class:admin={u.role === 'admin'} aria-hidden="true">{initial(u)}</span>
        <div class="main">
          <div class="name">
            <strong>{u.username}</strong>
            {#if u.id === me.id}<span class="badge">you</span>{/if}
            <span class="badge" class:admin={u.role === 'admin'}>{u.role === 'admin' ? 'Admin' : 'User'}</span>
          </div>
          <div class="muted small">{u.last_login_at ? `Last signed in ${when(u.last_login_at)}` : 'Has not signed in yet'}</div>
          {#if u.role !== 'admin'}
            <form class="link" onsubmit={(e) => { e.preventDefault(); saveLink(u) }}>
              <input value={linkDraft[u.id] ?? u.media_username ?? ''} oninput={(e) => (linkDraft[u.id] = e.currentTarget.value)}
                placeholder="Plex/Emby name (needed for private activity)" aria-label="Plex or Emby name for {u.username}" maxlength="100" />
              {#if (linkDraft[u.id] ?? u.media_username ?? '') !== (u.media_username ?? '')}
                <button disabled={busy}>Save name</button>
              {/if}
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
            <button class="danger" disabled={busy} onclick={() => remove(u)}>Delete</button>
          {:else}
            <span class="muted small">Change your own password under Account.</span>
          {/if}
        </div>
        {#if resetFor === u.id}
          <form class="reset" onsubmit={(e) => { e.preventDefault(); reset(u) }}>
            <input type="password" bind:value={resetPassword} placeholder="New password (8+ characters)" autocomplete="new-password" required aria-label="New password for {u.username}" />
            <button class="primary" disabled={busy}>Set password</button>
            <button type="button" onclick={() => { resetFor = null; resetPassword = '' }}>Cancel</button>
          </form>
        {/if}
      </li>
    {/each}
  </ul>
</section>

<section class="card">
  <h2>Privacy between users</h2>
  <label class="check">
    <input type="checkbox" bind:checked={hideOthers} disabled={busy} onchange={savePrivacy} />
    Normal users see only their own activity
  </label>
  <p class="hint">
    When on, a normal user sees only plays by the Plex/Emby name you link to their account above
    (and no server-wide bandwidth). With no name linked they see nothing. Admins always see
    everything, and a person's API keys follow the same rule. This saves as soon as you tick it.
  </p>
</section>

<section class="card">
  <h2>Add a person</h2>
  <form class="add" onsubmit={(e) => { e.preventDefault(); add() }}>
    <label>Username
      <input bind:value={form.username} autocomplete="off" required minlength="3" maxlength="64" placeholder="Email address, or 3 to 64 letters, numbers, . - _ @ +" />
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
</section>

{#if message}<p class={ok ? 'okmsg' : 'bad'} role="status">{message}</p>{/if}

<style>
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 20px; margin-bottom: 16px; }
  h2 { margin: 0 0 8px; font-size: 17px; }
  .hint { color: var(--muted); font-size: 12.5px; margin: 6px 0 12px; max-width: 700px; }
  .small { font-size: 12px; }
  .people { list-style: none; margin: 0; padding: 0; }
  .person { display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; gap: 4px 14px;
    align-items: center; padding: 14px 0; border-top: 1px solid var(--border); }
  .avatar { width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center;
    background: var(--bg); border: 1px solid var(--border); color: var(--muted); font-weight: 600; font-size: 16px; }
  .avatar.admin { color: var(--accent); border-color: var(--accent); }
  .main { min-width: 0; }
  .name { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; overflow-wrap: anywhere; }
  .badge { font-size: 11.5px; padding: 1px 8px; border-radius: 10px; border: 1px solid var(--border);
    color: var(--muted); }
  .badge.admin { color: var(--accent); border-color: var(--accent); }
  .actions { display: flex; gap: 8px; align-items: center; justify-content: flex-end; flex-wrap: wrap; }
  .actions select { width: auto; }
  button.danger:hover:not(:disabled) { border-color: var(--bad); color: var(--bad); }
  .link { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
  .link input { width: 100%; max-width: 340px; }
  .reset { grid-column: 2 / -1; display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
  .reset input { width: 260px; max-width: 100%; }
  .add { display: grid; gap: 12px; max-width: 460px; }
  label { display: grid; gap: 4px; font-size: 13px; color: var(--muted); }
  label.check { display: flex; align-items: center; gap: 8px; color: inherit; font-size: 14px; }
  label.check input { width: auto; }
  .okmsg { color: var(--ok); }
  .bad { color: var(--bad); }
  @media (max-width: 720px) {
    .person { grid-template-columns: 40px minmax(0, 1fr); }
    .actions { grid-column: 1 / -1; justify-content: flex-start; }
    .reset { grid-column: 1 / -1; }
  }
</style>
