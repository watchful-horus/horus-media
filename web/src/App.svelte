<script>
  import { onMount } from 'svelte'
  import { api } from './api.js'
  import Account from './Account.svelte'
  import Activity from './Activity.svelte'
  import Auth from './Auth.svelte'
  import Requests from './Requests.svelte'
  import SettingsPage from './SettingsPage.svelte'
  import { applyMode, nextMode, savedMode } from './theme.js'

  // Graphs (and the chart library it needs) is fetched only when someone opens that tab.
  const loadGraphs = () => import('./Graphs.svelte')

  let mode = $state(savedMode())
  const ICONS = { auto: '◐', light: '☀', dark: '☾' }
  const cycleTheme = () => { mode = nextMode(mode); applyMode(mode) }

  // The address bar (#activity, #graphs, #account, #settings/servers ...) records where you are,
  // so a reload or a bookmark returns to the same page.
  const TABS = ['activity', 'graphs', 'requests', 'account', 'settings']
  const SECTIONS = ['general', 'servers', 'users', 'api', 'integrations', 'logs']
  const SUBS = ['general', 'alerts', 'network', 'maintenance'] // the parts of Settings > General

  function fromHash() {
    const [tab, section, sub] = location.hash.replace(/^#\/?/, '').split('/')
    return {
      tab: TABS.includes(tab) ? tab : 'activity',
      section: SECTIONS.includes(section) ? section : 'general',
      sub: SUBS.includes(sub) ? sub : 'general',
    }
  }

  let where = $state(fromHash())
  let auth = $state(null) // null while checking; then { setup_required, authenticated, auth_disabled, user }

  // Settings are for admins. (With the login turned off there are no accounts, so everything shows.)
  const isAdmin = $derived(!!auth && (auth.auth_disabled || auth.user?.role === 'admin'))

  function go(tab, section = 'general', sub = 'general') {
    where = { tab, section, sub }
    const more = section === 'general' && sub !== 'general' ? `/${sub}` : ''
    history.replaceState(null, '', tab === 'settings' ? `#settings/${section}${more}` : `#${tab}`)
  }

  let seerrOn = $state(false)
  async function checkSeerr() {
    try {
      seerrOn = isAdmin && (await api.seerr()).configured
    } catch {
      seerrOn = false
    }
  }
  $effect(() => {
    if (auth?.authenticated) checkSeerr()
    addEventListener('horus:seerr', checkSeerr)
    return () => removeEventListener('horus:seerr', checkSeerr)
  })

  async function checkAuth() {
    try {
      auth = await api.authStatus()
    } catch {
      auth = { setup_required: false, authenticated: false, auth_disabled: false, user: null }
    }
  }

  async function signOut() {
    await api.logout().catch(() => {})
    go('activity')
    await checkAuth()
  }

  onMount(checkAuth)

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
    <nav>
      <button class:active={where.tab === 'activity'} onclick={() => go('activity')}>Activity</button>
      <button class:active={where.tab === 'graphs'} onclick={() => go('graphs')}>Graphs</button>
      {#if seerrOn}
        <button class:active={where.tab === 'requests'} onclick={() => go('requests')}>Requests</button>
      {/if}
      {#if isAdmin}
        <button class:active={where.tab === 'settings'} onclick={() => go('settings', where.section, where.sub)}>Settings</button>
      {/if}
    </nav>
    <div class="me">
      <button class="theme" onclick={cycleTheme} title="Theme: {mode} (click to change)" aria-label="Theme: {mode}. Click to change.">{ICONS[mode]} {mode === 'auto' ? 'Auto' : mode === 'light' ? 'Light' : 'Dark'}</button>
    </div>
    {#if auth.user}
      <div class="me2">
        <button class:active={where.tab === 'account'} onclick={() => go('account')} title="Your account">{auth.user.username}</button>
        <button class="signout" onclick={signOut}>Sign out</button>
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
    {:else if where.tab === 'requests' && seerrOn}
      <Requests />
    {:else if where.tab === 'account' && auth.user}
      <Account user={auth.user} />
    {:else if where.tab === 'settings' && isAdmin}
      <SettingsPage section={where.section} onsection={(s) => go('settings', s)} sub={where.sub} onsub={(t) => go('settings', 'general', t)} me={auth.user} />
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
  nav, .me { display: flex; gap: 6px; }
  .me { margin-left: auto; }
  .me2 { display: flex; gap: 6px; }
  header button { border-color: transparent; background: none; }
  header button.active { background: var(--bg); border-color: var(--border); font-weight: 600; }
  .signout, .theme { color: var(--muted); }
  main { max-width: 1100px; margin: 0 auto; padding: 24px; }
</style>
