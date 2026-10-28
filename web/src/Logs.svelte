<script>
  import { onMount, untrack } from 'svelte'
  import { api } from './api.js'

  let entries = $state([])
  let file = $state('')
  let level = $state('INFO') // minimum level to show
  let search = $state('')
  let auto = $state(true)
  let error = $state(null)
  let loading = $state(false)
  let updatedAt = $state(null)
  let recording = $state('INFO') // how much Horus writes to the log (a setting)
  let logFile = $state('')
  let recordingMsg = $state(null)

  const LEVELS = [['DEBUG', 'Everything'], ['INFO', 'Info and up'], ['WARNING', 'Warnings and errors'], ['ERROR', 'Errors only']]

  async function load() {
    if (loading) return
    loading = true
    try {
      const r = await api.logs({ level, q: search, limit: 500 })
      entries = r.entries.slice().reverse() // newest first
      file = r.file
      error = null
      updatedAt = new Date()
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  }

  onMount(async () => {
    load()
    try {
      const r = await api.settings()
      recording = r.settings.log_level
      logFile = r.info.log_file
    } catch { /* the viewer still works without it */ }
  })

  async function clearLog() {
    if (!confirm('Clear the whole log? This deletes the saved log file and cannot be undone. Download it first if you want a copy.')) return
    try {
      await api.clearLogs()
      await load()
    } catch (e) {
      error = e.message
    }
  }

  async function setRecording(value) {
    recordingMsg = null
    try {
      await api.saveSettings({ log_level: value })
      recording = value
      recordingMsg = 'Saved.'
    } catch (e) {
      recordingMsg = e.message
    }
  }

  // Reload when the level changes. untrack: load() reads `loading`, and without it this effect
  // would re-run every time a load finishes, requesting the log in an endless loop.
  $effect(() => {
    level
    untrack(load)
  })

  let searchTimer
  function onSearch() {
    clearTimeout(searchTimer)
    searchTimer = setTimeout(load, 300)
  }

  $effect(() => {
    if (!auto) return
    const timer = setInterval(load, 5000)
    return () => clearInterval(timer)
  })

  const time = (iso) =>
    new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  const day = (iso) => new Date(iso).toLocaleDateString()
  const short = (name) => name.replace(/^horus_media\./, '')
</script>

<div class="toolbar">
  <label>
    Show
    <select bind:value={level} title="Only affects what is listed below">
      {#each LEVELS as [v, text]}<option value={v}>{text}</option>{/each}
    </select>
  </label>
  <input class="search" type="search" placeholder="Search the log" bind:value={search} oninput={onSearch} aria-label="Search the log" />
  <label class="check"><input type="checkbox" bind:checked={auto} /> Auto-refresh</label>
  <button onclick={load} disabled={loading}>Refresh</button>
  <a class="button" href="/api/logs/download" download>Download log</a>
  <button onclick={clearLog} disabled={loading}>Clear log</button>
  {#if updatedAt}<span class="muted small">Updated {updatedAt.toLocaleTimeString()}</span>{/if}
</div>

<div class="record">
  <label>
    Recording detail
    <select value={recording} onchange={(e) => setRecording(e.currentTarget.value)}>
      <option value="ERROR">Errors only</option>
      <option value="WARNING">Warnings and errors</option>
      <option value="INFO">Normal (recommended)</option>
      <option value="DEBUG">Everything, for troubleshooting</option>
    </select>
  </label>
  <span class="muted small">How much Horus writes to the log from now on. {recordingMsg ?? ''}</span>
</div>

{#if error}
  <p class="bad">Could not load the log: {error}</p>
{:else if entries.length === 0}
  <div class="card"><p class="muted">
    {search || level !== 'DEBUG' ? 'Nothing matches. Try a lower level or a different search.' : 'The log is empty so far.'}
  </p></div>
{:else}
  <div class="card log" role="log" aria-label="Application log">
    {#each entries as e}
      <div class="line {e.level.toLowerCase()}">
        <span class="when" title={day(e.ts)}>{time(e.ts)}</span>
        <span class="lvl">{e.level === 'WARNING' ? 'WARN' : e.level}</span>
        <span class="src">{short(e.logger)}</span>
        <span class="msg">{e.message}</span>
      </div>
    {/each}
  </div>
  <p class="muted small">
    Showing the newest {entries.length} event{entries.length === 1 ? '' : 's'}. Passwords, tokens and
    API keys are removed before anything is written.
    {#if logFile}The file is <code>{logFile}</code> and rotates automatically (about 10 MB at most).{/if}
  </p>
{/if}

<style>
  .toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; margin-bottom: 14px; }
  .toolbar label { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; }
  .toolbar select { width: auto; }
  .search { flex: 1 1 160px; width: auto; max-width: 260px; min-height: 36px; }
  .toolbar select { min-height: 36px; }
  /* The toolbar's buttons match each other in height and type, like the rows elsewhere */
  .toolbar > button, .toolbar > a.button {
    display: inline-flex; align-items: center; justify-content: center; box-sizing: border-box;
    min-height: 36px; padding: 0 16px; font-size: 14px; line-height: 1.2; white-space: nowrap; }
  .record { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; margin: -4px 0 14px;
    padding: 10px 14px; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; }
  .record label { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; }
  .record select { width: auto; }
  code { font-size: 12px; background: var(--bg); padding: 1px 5px; border-radius: 4px; overflow-wrap: anywhere; }
  .check input { width: auto; }
  a.button { background: var(--panel); border: 1px solid var(--border); border-radius: 6px;
    padding: 6px 12px; color: var(--text); text-decoration: none; }
  a.button:hover { border-color: var(--accent); }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; }
  .log { padding: 6px 0; font: 12.5px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace; }
  .line { display: grid; grid-template-columns: 76px 52px 150px 1fr; gap: 0 10px; padding: 2px 16px; }
  .line:hover { background: var(--bg); }
  .when { color: var(--muted); }
  .lvl { font-weight: 600; }
  .src { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .msg { white-space: pre-wrap; overflow-wrap: anywhere; }
  .warning .lvl { color: var(--warn); }
  .error .lvl { color: var(--bad); }
  .debug .lvl, .debug .msg { color: var(--muted); }
  .small { font-size: 12px; }
  .bad { color: var(--bad); }
  @media (max-width: 720px) { .line { grid-template-columns: 70px 48px 1fr; } .src { display: none; } }
</style>
