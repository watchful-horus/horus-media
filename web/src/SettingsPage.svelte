<script>
  import Api from './Api.svelte'
  import General from './Settings.svelte'
  import Logs from './Logs.svelte'
  import Notifications from './Notifications.svelte'
  import Servers from './Servers.svelte'
  import Users from './Users.svelte'

  let { section, onsection, me } = $props()

  // Keep in step with SECTIONS in App.svelte (which also maps the older addresses onto these).
  const SECTIONS = [['servers', 'Servers'], ['users', 'Users'], ['notifications', 'Notifications'], ['api', 'API'], ['general', 'General'], ['maintenance', 'Backups and updates'], ['logs', 'Logs']]
</script>

<div class="layout">
  <nav class="side" aria-label="Settings sections">
    {#each SECTIONS as [id, text]}
      <button class:active={section === id} aria-current={section === id ? 'page' : undefined} onclick={() => onsection(id)}>
        {text}
      </button>
    {/each}
  </nav>
  <!-- Phones: one drop-down instead of the list, so every entry is reachable without scrolling sideways. -->
  <label class="pick">
    <span>Settings</span>
    <select value={section} onchange={(e) => onsection(e.currentTarget.value)} aria-label="Settings section">
      {#each SECTIONS as [id, text]}<option value={id}>{text}</option>{/each}
    </select>
  </label>
  <div class="content">
    {#if section === 'servers'}
      <Servers />
    {:else if section === 'users'}
      {#if me}<Users {me} />{:else}<p class="muted">Accounts are not used while the login is turned off.</p>{/if}
    {:else if section === 'notifications'}
      <!-- Channels first, then the limits and schedule (part of the shared settings form), then the report. -->
      <Notifications><General part="alerts" /></Notifications>
    {:else if section === 'api'}
      <Api />
    {:else if section === 'logs'}
      <Logs />
    {:else}
      <General part={section} />
    {/if}
  </div>
</div>

<style>
  .layout { display: grid; grid-template-columns: 200px minmax(0, 1fr); gap: 28px; align-items: start; }
  .side { display: flex; flex-direction: column; gap: 4px; position: sticky; top: 16px; }
  .side button { text-align: left; border-color: transparent; background: none; padding: 8px 12px; }
  .side button.active { background: var(--panel); border-color: var(--border); font-weight: 600; }
  .content { min-width: 0; }
  .pick { display: none; }
  @media (max-width: 720px) {
    .layout { grid-template-columns: 1fr; gap: 12px; }
    .side { display: none; }
    .pick { display: flex; align-items: center; gap: 10px; font-weight: 600; }
    .pick select { flex: 1; min-width: 0; background: var(--panel); font-weight: 600; padding: 8px 10px; }
  }
</style>
