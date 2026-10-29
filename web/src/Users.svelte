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

  // Clicking a name opens that person's panel; `draft` is what is being edited there.
  let openId = $state(null)
  let draft = $state({})
  const openPanel = (u) => {
    if (openId === u.id) { openId = null; return }
    openId = u.id
    resetFor = null
    draft = { username: u.username, display_name: u.display_name ?? '', media_username: u.media_username ?? '', role: u.role }
  }
  const changed = (u) =>
    draft.username?.trim() !== u.username || draft.display_name !== (u.display_name ?? '') ||
    draft.media_username !== (u.media_username ?? '') || draft.role !== u.role

  const saveDetails = (u) =>
    guard(async () => {
      const changes = { username: draft.username, display_name: draft.display_name, media_username: draft.media_username }
      if (u.id !== me.id && draft.role !== u.role) changes.role = draft.role
      await api.updateUser(u.id, changes)
      await load()
      if (u.id === me.id) window.dispatchEvent(new Event('horus:me')) // the top bar shows your name
      const fresh = users.find((x) => x.id === u.id)
      if (fresh) draft = { username: fresh.username, display_name: fresh.display_name ?? '', media_username: fresh.media_username ?? '', role: fresh.role }
    }, `Saved the details for ${draft.display_name || draft.username}.`)

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
  const shown = (u) => u.display_name || u.username
  const initial = (u) => (shown(u) || '?').trim().charAt(0).toUpperCase()
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
      <li class="person" class:open={openId === u.id}>
        <span class="avatar" class:admin={u.role === 'admin'} aria-hidden="true">{initial(u)}</span>
        <div class="main">
          <div class="name">
            <button type="button" class="namebtn" aria-expanded={openId === u.id} onclick={() => openPanel(u)}>{shown(u)}</button>
            {#if u.display_name}<span class="muted small">{u.username}</span>{/if}
            {#if u.id === me.id}<span class="badge">you</span>{/if}
            <span class="badge" class:admin={u.role === 'admin'}>{u.role === 'admin' ? 'Admin' : 'User'}</span>
          </div>
          <div class="muted small">{u.last_login_at ? `Last signed in ${when(u.last_login_at)}` : 'Has not signed in yet'}{u.media_username ? ` · Plex/Emby: ${u.media_username}` : ''}</div>
        </div>
        <button type="button" class="manage" aria-expanded={openId === u.id} onclick={() => openPanel(u)}>{openId === u.id ? 'Close' : 'Edit'}</button>

        {#if openId === u.id}
          <div class="panel">
            <form class="details" onsubmit={(e) => { e.preventDefault(); saveDetails(u) }}>
              <label>Username
                <input bind:value={draft.username} required minlength="3" maxlength="64" autocomplete="off" />
              </label>
              <label>Display name (optional)
                <input bind:value={draft.display_name} maxlength="60" autocomplete="off" placeholder="Shown instead of the username" />
              </label>
              <label>Plex/Emby name
                <input bind:value={draft.media_username} maxlength="100" autocomplete="off" placeholder="Needed when users only see their own activity" />
              </label>
              {#if u.id !== me.id}
                <label>Role
                  <select bind:value={draft.role}>
                    <option value="user">User: Activity and Graphs only</option>
                    <option value="admin">Admin: everything</option>
                  </select>
                </label>
              {/if}
              <div class="actions left">
                <button class="primary" disabled={busy || !changed(u)}>Save details</button>
              </div>
            </form>

            <div class="more">
              {#if u.id === me.id}
                <p class="muted small">Your password and API keys are on your <a href="#account">Account page</a>.</p>
              {:else}
                {#if resetFor === u.id}
                  <form class="reset" onsubmit={(e) => { e.preventDefault(); reset(u) }}>
                    <input type="password" bind:value={resetPassword} placeholder="New password (8+ characters)" autocomplete="new-password" required aria-label="New password for {u.username}" />
                    <button class="primary" disabled={busy}>Set password</button>
                    <button type="button" onclick={() => { resetFor = null; resetPassword = '' }}>Cancel</button>
                  </form>
                {:else}
                  <button disabled={busy} onclick={() => { resetFor = u.id; resetPassword = '' }}>Reset password</button>
                {/if}
                <button class="danger" disabled={busy} onclick={() => remove(u)}>Delete person</button>
              {/if}
            </div>
          </div>
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
  .namebtn { background: none; border: none; padding: 0; font: inherit; font-weight: 700; color: var(--text); cursor: pointer;
    text-decoration: underline; text-decoration-color: transparent; text-underline-offset: 3px; }
  .namebtn:hover, .namebtn:focus-visible { text-decoration-color: var(--accent); color: var(--accent); }
  .badge { font-size: 11.5px; padding: 1px 8px; border-radius: 10px; border: 1px solid var(--border);
    color: var(--muted); }
  .badge.admin { color: var(--accent); border-color: var(--accent); }
  .manage { justify-self: end; }
  .panel { grid-column: 2 / -1; margin-top: 8px; padding: 14px 16px; border: 1px solid var(--border); border-radius: 8px;
    background: var(--bg); display: grid; gap: 14px; }
  .details { display: grid; gap: 12px; max-width: 460px; }
  .details select { width: 100%; }
  .more { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding-top: 12px; border-top: 1px solid var(--border); }
  .actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .actions.left { justify-content: flex-start; }
  button.danger:hover:not(:disabled) { border-color: var(--bad); color: var(--bad); }
  .reset { display: flex; gap: 8px; flex-wrap: wrap; }
  .reset input { width: 260px; max-width: 100%; }
  .add { display: grid; gap: 12px; max-width: 460px; }
  label { display: grid; gap: 4px; font-size: 13px; color: var(--muted); }
  label.check { display: flex; align-items: center; gap: 8px; color: inherit; font-size: 14px; }
  label.check input { width: auto; }
  .okmsg { color: var(--ok); }
  .bad { color: var(--bad); }
  @media (max-width: 720px) {
    .person { grid-template-columns: 40px minmax(0, 1fr); }
    .manage { grid-column: 2; justify-self: start; }
    .panel { grid-column: 1 / -1; }
  }
</style>
