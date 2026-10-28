<script>
  import { onMount } from 'svelte'
  import { api } from './api.js'

  let info = $state(null)
  let form = $state(null) // editable copy of the settings
  let saved = $state(null) // last values from the server, to know if anything changed
  let message = $state(null)
  let messageOk = $state(true)
  let busy = $state(false)
  let backups = $state([])
  let update = $state(null)

  let dirty = $derived(form && saved && JSON.stringify(form) !== JSON.stringify(saved))

  function apply(r) {
    info = r.info
    form = { ...r.settings }
    saved = { ...r.settings }
  }

  async function guard(fn, okText) {
    busy = true
    message = null
    try {
      await fn()
      if (okText) { message = okText; messageOk = true }
    } catch (e) {
      message = e.message
      messageOk = false
    } finally {
      busy = false
    }
  }

  async function load() {
    apply(await api.settings())
    backups = (await api.backups()).items
  }
  onMount(() => guard(load))

  const listen = $derived(info?.listen)

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

  let stopped = $state(false)

  // How long Horus has been running, in words.
  function upFor(iso) {
    const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
    if (mins < 60) return `${mins} min`
    const hours = Math.floor(mins / 60)
    if (hours < 48) return `${hours} h ${mins % 60} min`
    return `${Math.floor(hours / 24)} days`
  }

  // Restart Horus, then follow it to the port it comes back on (the saved one, unless the
  // HORUS_PORT environment variable fixes it).
  const restart = () =>
    guard(async () => {
      if (!confirm('Restart Horus now? The page will reload when it is back.')) return
      const port = listen.port_source === 'environment' ? listen.port_running : saved.http_port
      await api.restart()
      message = 'Restarting... this page will reload when Horus is back.'
      messageOk = true
      const target = `${location.protocol}//${location.hostname}:${port}/`
      await sleep(3000)
      for (let i = 0; i < 40; i++) {
        try {
          await fetch(`${target}api/health`, { mode: 'no-cors', cache: 'no-store' })
          location.href = target + location.hash
          return
        } catch {
          await sleep(1000)
        }
      }
      message = `Horus did not come back at ${target}. Check the console where it is running.`
      messageOk = false
    })

  const shutdown = () =>
    guard(async () => {
      if (!confirm('Shut down Horus?\n\nIt will stop recording plays and this page will stop working. ' +
        'It stays off until you start it again yourself.')) return
      await api.shutdown()
      stopped = true
      message = null
    })

  const save = () => guard(async () => apply(await api.saveSettings(form)), 'Settings saved.')

  const backUpNow = () =>
    guard(async () => {
      await api.createBackup()
      apply(await api.settings())
      backups = (await api.backups()).items
    }, 'Backup saved to the backups folder.')

  const restore = (b) => {
    const when = new Date(b.created_at).toLocaleString()
    if (!confirm([
      `Restore the backup from ${when}?`,
      '',
      'This replaces your servers, settings and history with what is in that backup. ' +
        'Anything recorded since then will be gone from Horus (not from your servers).',
      '',
      'The current state is backed up first, so you can undo this by restoring that backup.',
    ].join('\n'))) return
    guard(async () => {
      const r = await api.restoreBackup(b.name)
      apply(await api.settings())
      backups = (await api.backups()).items
      message = `Restored. ${r.servers} server${r.servers === 1 ? '' : 's'} back. Your previous state was saved as ${r.safety_backup}.`
      messageOk = true
    })
  }

  const removeBackup = (b) => {
    if (!confirm(`Delete the backup ${b.name}? This cannot be undone.`)) return
    guard(async () => {
      await api.deleteBackup(b.name)
      backups = (await api.backups()).items
    }, 'Backup deleted.')
  }

  const uploadBackup = (e) => {
    const file = e.currentTarget.files?.[0]
    e.currentTarget.value = ''
    if (!file) return
    if (!confirm([
      `Restore from "${file.name}"?`,
      '',
      'This replaces your servers, settings, accounts and history with what is in that file. ' +
        'The current state is backed up first, so you can undo it. You will need to sign in again.',
    ].join('\n'))) return
    guard(async () => {
      const r = await api.uploadBackup(file)
      message = `Restored. ${r.servers} server${r.servers === 1 ? '' : 's'} back. Reloading...`
      messageOk = true
      setTimeout(() => location.reload(), 1500)
    })
  }

  const checkUpdates = () =>
    guard(async () => {
      update = await api.updateCheck()
      apply(await api.settings())
    })

  const when = (iso) => (iso ? new Date(iso).toLocaleString() : 'never')
  const size = (b) => (b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`)
</script>

{#if !form}
  <p class={message ? 'bad' : 'muted'}>{message ?? 'Loading...'}</p>
{:else}
  <form onsubmit={(e) => { e.preventDefault(); save() }}>
    <section class="card">
      <h2>General</h2>
      <label>
        Time zone
        <input list="zones" bind:value={form.timezone} placeholder="Blank = this machine ({info.effective_timezone})" />
        <datalist id="zones">{#each info.timezones as z}<option value={z}></option>{/each}</datalist>
        <span class="hint">Decides which day and hour a play counts toward in the graphs.</span>
      </label>
      <label>
        Check servers every (seconds)
        <input type="number" min="0" max="3600" step="1" bind:value={form.poll_seconds} />
        <span class="hint">How often Horus records plays and bandwidth. 0 turns recording off. Minimum 5.</span>
      </label>
      <label>
        Keep history for (days)
        <input type="number" min="0" max="3650" step="1" bind:value={form.retention_days} />
        <span class="hint">0 keeps everything. Older plays and bandwidth samples are deleted automatically.</span>
      </label>
      <label class="check">
        <input type="checkbox" bind:checked={form.hide_others_activity} />
        Normal users see only their own activity
      </label>
      <span class="hint">When on, a normal user sees only plays by the Plex/Emby name you link to their account under Users (and no server-wide bandwidth). With no name linked they see nothing. Admins always see everything. Their API keys follow the same rule.</span>
    </section>

    <section class="card">
      <h2>Alerts</h2>
      <p class="hint">All off by default. An alert only reaches you through a notification channel that has it ticked (Integrations &gt; Notifications). Each one is sent once when the limit is crossed, and again only after it has dropped back below.</p>
      <label>
        Transcodes at once, per server
        <input type="number" min="0" max="100" step="1" bind:value={form.alert_transcodes_over} />
        <span class="hint">Alert when more than this many streams are being transcoded. 0 turns it off.</span>
      </label>
      <label>
        Bandwidth, per server (Mbps)
        <input type="number" min="0" max="100000" step="1" bind:value={form.alert_bandwidth_over_mbps} />
        <span class="hint">Alert when one server's streams add up to more than this. 0 turns it off.</span>
      </label>
      <label class="check">
        <input type="checkbox" bind:checked={form.alert_new_user} />
        Alert when someone Horus has never seen starts playing
      </label>
    </section>

    <section class="card">
      <h2>Network</h2>
      {#if listen.port_editable}
        <label>
          HTTP port
          <input type="number" min="1024" max="65535" step="1" bind:value={form.http_port} />
          <span class="hint">Running on {listen.port_running} now. A new port applies after a restart. Use 1024 to 65535.</span>
        </label>
        {#if listen.port_source === 'command'}
          <p class="hint">
            Horus was started with a port on the command line (for example <code>uvicorn --port</code>).
            Saving a different port here and restarting relaunches it as <code>python -m horus_media</code>,
            which uses the port saved here.
          </p>
        {/if}
      {:else}
        <label>
          HTTP port
          <input type="number" value={listen.port_running ?? info.port} disabled />
          <span class="hint">Locked: the <code>HORUS_PORT</code> environment variable sets it. Remove that variable to change it here.</span>
        </label>
      {/if}

      {#if listen.host_editable}
        <label class="check">
          <input type="checkbox" bind:checked={form.listen_all} disabled={!info.admin_exists && !info.auth_disabled} />
          Allow other computers to connect
        </label>
        <p class="hint">
          Off: only this computer can open Horus. On: other computers on your network can reach it
          (needed for Grafana running elsewhere); they still need to sign in or use an API key.
          Applies after a restart.
        </p>
      {:else}
        <label class="check">
          <input type="checkbox" checked={listen.host_running !== '127.0.0.1'} disabled />
          Allow other computers to connect
        </label>
        <p class="hint">Locked: the <code>HORUS_HOST</code> environment variable sets it.</p>
      {/if}
      {#if listen.in_container}
        <p class="hint">Horus is running in a container, so the port people use is set by the container's port mapping. Change it there.</p>
      {/if}

      {#if listen.restart_needed}
        <div class="notice" role="status">
          <span>{dirty ? 'Save, then restart' : 'Saved. Restart'} Horus to use the new network settings.</span>
          <button type="button" class="primary" disabled={busy || dirty || !listen.can_restart} onclick={restart}>Restart now</button>
        </div>
      {/if}
    </section>

    <section class="card">
      <h2>Backups</h2>
      <p class="hint">
        A backup is the database plus its encryption key, saved as a zip in
        <code>{info.backup_dir}</code>. Download it to keep a copy somewhere safe. It contains your
        server tokens, so treat it as a secret.
      </p>
      <label class="check">
        <input type="checkbox" bind:checked={form.backup_enabled} /> Back up automatically
      </label>
      {#if form.backup_enabled}
        <div class="two">
          <label>Every (hours)<input type="number" min="1" max="720" bind:value={form.backup_interval_hours} /></label>
          <label>Keep the last<input type="number" min="1" max="100" bind:value={form.backup_keep} /></label>
        </div>
      {/if}
      <div class="actions">
        <button type="button" disabled={busy} onclick={backUpNow}>Back up now</button>
        <label class="button upload">
          Restore from a file...
          <input type="file" accept=".zip,.db" disabled={busy} onchange={uploadBackup} hidden />
        </label>
      </div>
      <p class="hint">Last backup: {when(info.last_backup_at)}.</p>
      {#if backups.length}
        <ul class="files">
          {#each backups as b}
            <li>
              <code>{b.name}</code>
              <span class="muted">{when(b.created_at)} &middot; {size(b.size)}</span>
              <a class="button small-btn" href="/api/backups/{b.name}/download" download>Download</a>
              <button type="button" class="small-btn" disabled={busy} onclick={() => restore(b)}>Restore</button>
              <button type="button" class="small-btn" disabled={busy} onclick={() => removeBackup(b)}>Delete</button>
            </li>
          {/each}
        </ul>
      {/if}
      <p class="hint">
        Restore replaces your servers, settings and history with a backup. Horus saves the current
        state first, so a restore can be undone. "Restore from a file" takes a backup zip, for
        example one you downloaded from another Horus. You can also restore by hand: stop Horus,
        unzip a backup over <code>horus.db</code> and <code>horus.key</code>, and start it again.
      </p>
    </section>

    <section class="card">
      <h2>System</h2>
      <dl>
        <dt>Version</dt><dd>{info.version}</dd>
        <dt>Running since</dt><dd>{when(info.started_at)} <span class="hint">({upFor(info.started_at)})</span></dd>
        <dt>Data folder</dt><dd><code>{info.data_dir}</code></dd>
        {#if info.update_latest}<dt>Latest known</dt><dd>{info.update_latest}</dd>{/if}
      </dl>

      <h3>Updates</h3>
      {#if info.update_url_from_env}
        <p class="hint">The release address is set by the <code>HORUS_UPDATE_URL</code> environment variable.</p>
      {:else}
        <label>
          Release address
          <input type="url" bind:value={form.update_url} placeholder="https://api.github.com/repos/owner/repo/releases/latest" />
          <span class="hint">A web address that returns the latest release, such as a GitHub "latest release" link. Horus contacts only this address, sends nothing about you or your servers, and never installs anything. Leave it empty until Horus is published.</span>
        </label>
      {/if}
      <label class="check">
        <input type="checkbox" bind:checked={form.update_check} disabled={!form.update_url && !info.update_url_from_env} />
        Check for updates once a day
      </label>
      <div class="actions">
        <button type="button" disabled={busy || dirty} onclick={checkUpdates}>Check for updates</button>
        {#if dirty}<span class="muted small">Save your changes first.</span>{/if}
      </div>
      {#if update}
        {#if !update.configured}
          <p class="muted">No release address is set yet, so there is nothing to check. Add one above once Horus is published.</p>
        {:else}
          <p class={update.newer ? 'ok' : 'muted'}>
            {update.newer ? `Version ${update.latest} is available.` : `You are up to date (latest is ${update.latest}).`}
            {#if update.newer && update.release_url}
              <a href={update.release_url} target="_blank" rel="noopener noreferrer">Release notes</a>
            {/if}
          </p>
        {/if}
      {/if}

      <h3>Power</h3>
      {#if stopped}
        <div class="notice" role="status">
          <span>Horus has stopped. Start it again with <code>python -m horus_media</code> (or however you normally start it).</span>
        </div>
      {:else}
        <div class="actions">
          <button type="button" disabled={busy || dirty || !listen.can_restart} onclick={restart}>Restart</button>
          <button type="button" disabled={busy || !listen.can_shutdown} onclick={shutdown}>Shut down</button>
        </div>
        <p class="hint">
          {#if listen.in_container}
            Horus is running in a container: restart or stop the container instead.
          {:else}
            Restart applies the port and network settings above. Shut down stops Horus; it stays
            off until you start it again yourself.
            {#if dirty}<strong>Restart is off because you have unsaved changes above. Save them (or reload the page) first.</strong>{/if}
          {/if}
        </p>
      {/if}
    </section>

    <div class="savebar">
      <button class="primary" disabled={busy || !dirty}>Save settings</button>
      {#if dirty}<span class="muted small">Unsaved changes</span>{/if}
      {#if message}<span class={messageOk ? 'ok' : 'bad'}>{message}</span>{/if}
    </div>
  </form>
{/if}

<style>
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 20px; margin-bottom: 16px; }
  h2 { margin: 0 0 12px; font-size: 17px; }
  label { display: grid; gap: 4px; margin-bottom: 14px; max-width: 460px; font-size: 14px; }
  label.check { display: flex; align-items: center; gap: 8px; }
  label.check input { width: auto; }
  .two { display: flex; gap: 16px; max-width: 460px; }
  .two label { flex: 1; }
  .hint { color: var(--muted); font-size: 12.5px; }
  p.hint { margin: 6px 0 12px; max-width: 620px; }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; margin: 0 0 8px; }
  dt { color: var(--muted); }
  dd { margin: 0; }
  code { font-size: 12.5px; background: var(--bg); padding: 1px 5px; border-radius: 4px;
    overflow-wrap: anywhere; }
  .actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 6px 0; }
  a.button, label.button { background: var(--panel); border: 1px solid var(--border); border-radius: 6px;
    padding: 6px 12px; color: var(--text); text-decoration: none; cursor: pointer; display: inline-block; }
  a.button:hover, label.button:hover { border-color: var(--accent); }  .files { margin: 8px 0; padding-left: 0; list-style: none; font-size: 13px; }
  .files li { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; padding: 4px 0; }
  .small-btn { padding: 2px 10px; font-size: 12.5px; }
  h3 { margin: 18px 0 6px; font-size: 14px; }
  .notice { display: flex; align-items: center; justify-content: space-between; gap: 12px;
    border: 1px solid var(--accent); border-radius: 8px; padding: 10px 14px; margin-top: 8px; }
  .pw { display: grid; gap: 4px; max-width: 460px; }
  .savebar { display: flex; align-items: center; gap: 14px; position: sticky; bottom: 0;
    padding: 12px 0; background: var(--bg); }
  .ok { color: var(--ok); }
  .bad { color: var(--bad); }
  .small { font-size: 12px; }
</style>
