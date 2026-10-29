<script>
  import Api from './Api.svelte'
  import General from './Settings.svelte'
  import Integrations from './Integrations.svelte'
  import Logs from './Logs.svelte'
  import Servers from './Servers.svelte'
  import Users from './Users.svelte'

  let { section, onsection, sub = 'general', onsub = () => {}, me } = $props()

  const SECTIONS = [['general', 'General'], ['servers', 'Servers'], ['users', 'Users'], ['api', 'API'], ['integrations', 'Integrations'], ['logs', 'Logs']]
</script>

<div class="layout">
  <nav class="side" aria-label="Settings sections">
    {#each SECTIONS as [id, text]}
      <button class:active={section === id} aria-current={section === id ? 'page' : undefined} onclick={() => onsection(id)}>
        {text}
      </button>
    {/each}
  </nav>
  <div class="content">
    {#if section === 'servers'}
      <Servers />
    {:else if section === 'users'}
      {#if me}<Users {me} />{:else}<p class="muted">Accounts are not used while the login is turned off.</p>{/if}
    {:else if section === 'api'}
      <Api />
    {:else if section === 'integrations'}
      <Integrations />
    {:else if section === 'logs'}
      <Logs />
    {:else}
      <General tab={sub} ontab={onsub} />
    {/if}
  </div>
</div>

<style>
  .layout { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 28px; align-items: start; }
  .side { display: flex; flex-direction: column; gap: 4px; position: sticky; top: 16px; }
  .side button { text-align: left; border-color: transparent; background: none; padding: 8px 12px; }
  .side button.active { background: var(--panel); border-color: var(--border); font-weight: 600; }
  .content { min-width: 0; }
  @media (max-width: 720px) {
    .layout { grid-template-columns: 1fr; gap: 12px; }
    .side { flex-direction: row; position: static; }
  }
</style>
