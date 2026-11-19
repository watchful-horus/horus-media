<script>
  import { onMount } from 'svelte'
  import { api } from './api.js'
  import Account from './Account.svelte'
  import Activity from './Activity.svelte'
  import Auth from './Auth.svelte'
  import SettingsPage from './SettingsPage.svelte'
  import { applyMode, nextMode, savedMode } from './theme.js'

  // Graphs (and the chart library it needs) is fetched only when someone opens that tab.
  const loadGraphs = () => import('./Graphs.svelte')

  let mode = $state(savedMode())
  const ICONS = { auto: '◐', light: '☀', dark: '☾' }
  const cycleTheme = () => { mode = nextMode(mode); applyMode(mode) }

  // The address bar (#activity, #graphs, #account, #settings/servers ...) records where you are,
  // so a reload or a bookmark returns to the same page.
  const TABS = ['activity', 'graphs', 'account', 'settings']
  const SECTIONS = ['servers', 'users', 'notifications', 'api', 'general', 'maintenance', 'logs']
  // Older addresses keep working: these pages were merged into others.
  const MOVED = { alerts: 'notifications', integrations: 'notifications', network: 'general' }

  function fromHash() {
    let [tab, section, sub] = location.hash.replace(/^#\/?/, '').split('/')
    // an even older address: #settings/general/network
    if (section === 'general' && (SECTIONS.includes(sub) || sub in MOVED)) section = sub
    section = MOVED[section] ?? section
    return {
      tab: TABS.includes(tab) ? tab : 'activity',
      section: SECTIONS.includes(section) ? section : 'servers',
    }
  }

  let where = $state(fromHash())
  let auth = $state(null) // null while checking; then { setup_required, authenticated, auth_disabled, user }

  // Settings are for admins. (With the login turned off there are no accounts, so everything shows.)
  const isAdmin = $derived(!!auth && (auth.auth_disabled || auth.user?.role === 'admin'))

  function go(tab, section = 'servers') {
    where = { tab, section }
    history.replaceState(null, '', tab === 'settings' ? `#settings/${section}` : `#${tab}`)
  }

  async function checkAuth() {
    try {
      auth = await api.authStatus()
    } catch {
      auth = { setup_required: false, authenticated: false, auth_disabled: false, user: null }
    }
  }

  // The menu under your name: Account, Theme, Sign out.
  let menuOpen = $state(false)
  let menuEl = $state(null)
  const THEME_TEXT = { auto: 'Auto', light: 'Light', dark: 'Dark' }

  async function signOut() {
    menuOpen = false
    await api.logout().catch(() => {})
    go('activity')
    await checkAuth()
  }

  onMount(checkAuth)

  // The running version, shown small beside the name once someone is signed in.
  let version = $state(null)
  $effect(() => {
    if (!auth?.authenticated || version) return
    fetch('/api/health').then((r) => r.json()).then((h) => { version = h.version || null }).catch(() => {})
  })
  $effect(() => {
    addEventListener('horus:me', checkAuth) // your own details were changed on the Users page
    return () => removeEventListener('horus:me', checkAuth)
  })

  $effect(() => {
    if (auth?.authenticated && !isAdmin && where.tab === 'settings') go('activity')
  })

  $effect(() => {
    const onHash = () => (where = fromHash())
    const onLogin = () => checkAuth() // an API call came back 401: the login expired
    addEventListener('hashchange', onHash)
    addEventListener('horus:login', onLogin)
    return () => {
      removeEventListener('hashchange', onHash)
      removeEventListener('horus:login', onLogin)
    }
  })
</script>

<svelte:window
  onpointerdown={(e) => { if (menuOpen && menuEl && !menuEl.contains(e.target)) menuOpen = false }}
  onkeydown={(e) => { if (e.key === 'Escape') menuOpen = false }} />

{#if !auth}
  <p class="loading muted">Loading...</p>
{:else if auth.setup_required}
  <Auth mode="setup" ondone={checkAuth} />
{:else if !auth.authenticated}
  <Auth mode="login" ondone={checkAuth} />
{:else}
  <header>
    <a class="brand" href="#activity" aria-label="Horus home">
      <img src="/favicon.svg" width="30" height="30" alt="" />
      <h1>Horus</h1>
    </a>
    {#if version}
      {#if isAdmin}
        <a class="ver" href="#settings/maintenance" title="Version {version}. Open Backups and updates."
          onclick={(e) => { e.preventDefault(); go('settings', 'maintenance') }}>v{version}</a>
      {:else}
        <span class="ver" title="Version {version}">v{version}</span>
      {/if}
    {/if}
    <nav>
      <button class:active={where.tab === 'activity'} onclick={() => go('activity')}>Activity</button>
      <button class:active={where.tab === 'graphs'} onclick={() => go('graphs')}>Graphs</button>
      {#if isAdmin}
        <button class:active={where.tab === 'settings'} onclick={() => go('settings', where.section)}>Settings</button>
      {/if}
    </nav>
    {#if auth.user}
      <div class="me" bind:this={menuEl}>
        <button class="who" class:active={where.tab === 'account'} aria-haspopup="menu" aria-expanded={menuOpen}
          title="Account, theme, sign out" onclick={() => (menuOpen = !menuOpen)}>
          <span class="name">{auth.user.display_name || auth.user.username}</span> <span aria-hidden="true">&#9662;</span>
        </button>
        {#if menuOpen}
          <div class="menu" role="menu">
            <button role="menuitem" onclick={() => { menuOpen = false; go('account') }}>Account</button>
            <button role="menuitem" onclick={cycleTheme} title="Click to change">Theme: {THEME_TEXT[mode]} <span aria-hidden="true">{ICONS[mode]}</span></button>
            <button role="menuitem" onclick={signOut}>Sign out</button>
          </div>
        {/if}
      </div>
    {:else}
      <!-- The login is turned off: there is no account, so only the theme button is left. -->
      <div class="me">
        <button class="theme" onclick={cycleTheme} title="Theme: {mode} (click to change)" aria-label="Theme: {mode}. Click to change.">{ICONS[mode]} {THEME_TEXT[mode]}</button>
      </div>
    {/if}
  </header>
  <main>
    {#if where.tab === 'activity'}
      <Activity onsetup={() => go('settings', 'servers')} canSetup={isAdmin} isAdmin={isAdmin} />
    {:else if where.tab === 'graphs'}
      {#await loadGraphs()}
        <p class="muted">Loading...</p>
      {:then { default: Graphs }}
        <Graphs />
      {:catch}
        <p class="bad">Could not load the graphs. Reload the page to try again.</p>
      {/await}
    {:else if where.tab === 'account' && auth.user}
      <Account user={auth.user} onchanged={checkAuth} />
    {:else if where.tab === 'settings' && isAdmin}
      <SettingsPage section={where.section} onsection={(s) => go('settings', s)} me={auth.user} />
    {/if}
  </main>
{/if}

<style>
  .loading { padding: 24px; }
  header { display: flex; align-items: center; gap: 24px; padding: 14px 24px;
    background: var(--panel); border-bottom: 1px solid var(--border); }
  .brand { display: flex; align-items: center; gap: 10px; color: inherit; text-decoration: none; }
  .brand img { display: block; border-radius: 7px; }
  h1 { margin: 0; font-size: 20px; letter-spacing: .02em; }
  /* Sits close to the name: pulled back over most of the header's gap. */
  .ver { margin-left: -14px; flex-shrink: 0; align-self: center; padding-top: 4px; font-size: 12px;
    color: var(--muted); text-decoration: none; white-space: nowrap; }
  a.ver:hover { color: inherit; text-decoration: underline; }
  nav, .me { display: flex; gap: 6px; }
  .me { margin-left: auto; position: relative; min-width: 0; }
  header button { border-color: transparent; background: none; }
  header button.active { background: var(--bg); border-color: var(--border); font-weight: 600; }
  .theme { color: var(--muted); }
  .who { display: flex; align-items: center; gap: 6px; max-width: 220px; min-width: 0; }
  .who .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .menu { position: absolute; right: 0; top: calc(100% + 6px); z-index: 20; min-width: 180px;
    display: flex; flex-direction: column; gap: 2px; padding: 6px; background: var(--panel);
    border: 1px solid var(--border); border-radius: 8px; box-shadow: 0 6px 18px rgba(0, 0, 0, .18); }
  .menu button { text-align: left; padding: 8px 12px; white-space: nowrap; }
  .menu button:hover { background: var(--bg); border-color: var(--border); }
  main { max-width: 1100px; margin: 0 auto; padding: 24px; }
  /* Phones only: your name and its menu sit beside the logo, the page buttons on a second row. */
  @media (max-width: 720px) {
    header { flex-wrap: wrap; gap: 8px 12px; padding: 12px 16px; }
    nav { order: 3; flex-basis: 100%; }
    .who { max-width: 190px; }
    .ver { margin-left: -4px; }
    /* Your name takes what is left of the first row and shortens, so it never needs a row of its own. */
    .me { flex: 1 1 0; justify-content: flex-end; }
    main { padding: 16px; }
  }
</style>
