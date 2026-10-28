<script>
  import { onMount } from 'svelte'
  import { api } from './api.js'
  import Chart from './Chart.svelte'

  // ---- date helpers (all local calendar dates as YYYY-MM-DD) ----
  const iso = (d) => {
    const z = new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    return z.toISOString().slice(0, 10)
  }
  const parse = (s) => new Date(`${s}T00:00:00`)
  const addDays = (s, n) => { const d = parse(s); d.setDate(d.getDate() + n); return iso(d) }
  const daysBetween = (a, b) => Math.round((parse(b) - parse(a)) / 86400000) + 1
  const today = () => iso(new Date())

  let start = $state(addDays(today(), -29))
  let end = $state(today())
  let data = $state(null)
  let error = $state(null)
  let granChoice = $state(null) // null = pick automatically from the range length

  const PRESETS = [[7, '7 days'], [30, '30 days'], [90, '90 days'], [365, '1 year']]

  async function load() {
    try {
      data = await api.stats(start, end, granChoice === 'hour')
      error = null
    } catch (e) {
      error = e.message
    }
  }
  onMount(load)

  const span = $derived(data ? daysBetween(data.range.start, data.range.end) : 30)
  const autoGran = (n) => (n <= 45 ? 'day' : n <= 200 ? 'week' : 'month')
  const gran = $derived(granChoice ?? autoGran(span))
  const atPresent = $derived(end >= today())

  const HOURLY_MAX_DAYS = 90
  const inputSpan = $derived(daysBetween(start, end))
  const hourOk = $derived(inputSpan <= HOURLY_MAX_DAYS)
  // Pick Day/Week/Month/Hour. Hour needs its own data, so it is fetched when chosen.
  function pickGran(g) {
    granChoice = g
    if (g === 'hour' && !data?.plays_per_hour) load()
  }
  function setRange(s, e) {
    start = s
    end = e
    if (granChoice === 'hour' && daysBetween(s, e) > HOURLY_MAX_DAYS) granChoice = null // too long for hours
    load()
  }
  const preset = (n) => setRange(addDays(today(), -(n - 1)), today())
  const allTime = () => data?.earliest && setRange(data.earliest, today())
  function step(direction) {
    const n = daysBetween(start, end)
    if (direction < 0) setRange(addDays(start, -n), addDays(start, -1))
    else setRange(addDays(end, 1), (e => (e > today() ? today() : e))(addDays(end, n)))
  }
  const activePreset = (n) => end === today() && start === addDays(today(), -(n - 1))

  // ---- formatting ----
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
  const label = (s) => s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
  const kbps = (v) => (v >= 1000 ? `${(v / 1000).toFixed(1)} Mbps` : `${Math.round(v)} kbps`)
  const nice = (key, g) => {
    if (g === 'hour' && key.length > 10) {
      return new Date(key).toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
    }
    const d = parse(key.slice(0, 10))
    if (g === 'month') return d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
    const text = d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
    return g === 'week' ? `Week of ${text}` : text
  }

  // ---- rolling daily counts up to weeks / months ----
  function bucket(day, g) {
    if (g === 'day') return day
    if (g === 'month') return `${day.slice(0, 8)}01`
    const d = parse(day)
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7)) // back to Monday
    return iso(d)
  }
  function rollup(days, list, g) {
    const keys = []
    const index = new Map()
    for (const day of days) {
      const k = bucket(day, g)
      if (!index.has(k)) { index.set(k, keys.length); keys.push(k) }
    }
    const series = list.map((s) => {
      const out = new Array(keys.length).fill(0)
      s.data.forEach((v, i) => { out[index.get(bucket(days[i], g))] += v })
      return { name: s.name ?? s.server, data: out }
    })
    return { keys, series }
  }

  // ---- chart builders ----
  const tip = (header, value) => (params) => {
    const rows = params.map((p) => `${p.marker} ${esc(p.seriesName)} <b>${value(p)}</b>`)
    return `${esc(header(params[0]))}<br/>${rows.join('<br/>')}`
  }

  // The Total line: dashed, in neutral ink, drawn above the others and without an area fill.
  const totalLine = (name, data) => ({
    name, type: 'line', data, showSymbol: false, z: 5, lineStyle: { width: 2, type: 'dashed' },
    emphasis: { focus: 'series' },
  })
  const withTotal = (series) =>
    series.length < 2 ? series : [...series, {
      name: 'Total', total: true,
      data: series[0].data.map((_, i) => series.reduce((n, s) => n + s.data[i], 0)),
    }]

  const timeLines = (keys, series, g, extra = {}) => ({
    xAxis: { type: 'time' },
    yAxis: { type: 'value', minInterval: 1 },
    tooltip: tip((p) => nice(p.value[0], g), (p) => p.value[1]),
    dataZoom: [{ type: 'inside' }], // scroll to zoom, drag to pan; no slider bar
    series: series.map((s) => (s.total ? totalLine(s.name, keys.map((k, i) => [k, s.data[i]])) : {
      name: s.name, type: 'line', data: keys.map((k, i) => [k, s.data[i]]),
      showSymbol: keys.length <= 45, symbolSize: 8, lineStyle: { width: 2 },
      itemStyle: { borderWidth: 2, borderColor: 'transparent' }, areaStyle: { opacity: 0.08 },
      emphasis: { focus: 'series' }, ...extra,
    })),
  })

  const timeTable = (keys, series, g) => ({
    head: ['Date', ...series.map((s) => label(s.name))],
    rows: keys.map((k, i) => [nice(k, g), ...series.map((s) => s.data[i])]),
  })

  const hbar = (items) => {
    const top = items.slice(0, 10)
    return {
      option: {
        legend: { show: false },
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        grid: { top: 8, bottom: 8 },
        xAxis: { type: 'value', minInterval: 1 },
        yAxis: { type: 'category', inverse: true, data: top.map((i) => i.name) },
        series: [{ name: 'Plays', type: 'bar', data: top.map((i) => i.count), barMaxWidth: 16,
          itemStyle: { borderRadius: [0, 4, 4, 0] } }],
      },
      height: Math.max(110, top.length * 30 + 20),
      table: { head: ['Name', 'Plays'], rows: items.map((i) => [i.name, i.count]) },
    }
  }

  const hours = Array.from({ length: 24 }, (_, h) => `${h}:00`)

  const built = $derived.by(() => {
    if (!data) return null
    const days = data.plays_per_day.days
    const hourly = gran === 'hour' && data.plays_per_hour
    const plays = hourly
      ? { keys: data.plays_per_hour.hours, series: data.plays_per_hour.series.map((s) => ({ name: s.server, data: s.data })) }
      : rollup(days, data.plays_per_day.series, gran)
    const methods = hourly
      ? { keys: data.plays_per_hour.hours, series: data.methods_per_hour }
      : rollup(days, data.methods_per_day, gran)
    const playSeries = withTotal(plays.series)
    const methodSeries = withTotal(methods.series.map((s) => ({ ...s, name: label(s.name) })))
    return {
      plays: { option: timeLines(plays.keys, playSeries, gran), table: timeTable(plays.keys, playSeries, gran) },
      methods: {
        option: timeLines(methods.keys, methodSeries, gran),
        table: timeTable(methods.keys, methodSeries, gran),
      },
      bandwidth: {
        option: {
          xAxis: { type: 'time' },
          yAxis: { type: 'value', axisLabel: { formatter: (v) => kbps(v) } },
          tooltip: {
            trigger: 'axis',
            formatter: (params) => {
              const when = new Date(params[0].value[0]).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
              return `${esc(when)}<br/>` + params.map((p) =>
                `${p.marker} ${esc(p.seriesName)} <b>${kbps(p.value[1])}</b> <span style="opacity:.7">(peak ${kbps(p.value[2])})</span>`).join('<br/>')
            },
          },
          dataZoom: [{ type: 'inside' }],
          series: data.bandwidth.map((s) => ({
            name: s.server, type: 'line', data: s.points, showSymbol: s.points.length <= 45, symbolSize: 8,
            lineStyle: { width: 2 }, itemStyle: { borderWidth: 2, borderColor: 'transparent' },
            areaStyle: { opacity: 0.08 }, emphasis: { focus: 'series' },
          })).concat(
            data.bandwidth.length > 1 ? [totalLine('Total', data.bandwidth_total)] : [],
          ),
        },
        table: {
          head: ['Time', 'Server', 'Average', 'Peak'],
          rows: (data.bandwidth.length > 1
            ? [...data.bandwidth, { server: 'Total', points: data.bandwidth_total }]
            : data.bandwidth
          ).flatMap((s) => s.points.map((p) => [new Date(p[0]).toLocaleString(), s.server, kbps(p[1]), kbps(p[2])])),
        },
      },
      user: hbar(data.by_user),
      platform: hbar(data.by_platform),
      types: hbar(data.media_types.map((m) => ({ ...m, name: label(m.name) }))),
      hour: {
        option: {
          legend: { show: false },
          xAxis: { type: 'category', data: hours, boundaryGap: false },
          yAxis: { type: 'value', minInterval: 1 },
          series: [{ name: 'Plays', type: 'line', data: data.by_hour, smooth: 0.3, showSymbol: false,
            lineStyle: { width: 2 }, areaStyle: { opacity: 0.12 } }],
        },
        table: { head: ['Hour', 'Plays'], rows: hours.map((h, i) => [h, data.by_hour[i]]) },
      },
      weekday: {
        option: {
          legend: { show: false },
          tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
          xAxis: { type: 'category', data: data.by_weekday.labels },
          yAxis: { type: 'value', minInterval: 1 },
          series: [{ name: 'Plays', type: 'bar', data: data.by_weekday.data, barMaxWidth: 36,
            itemStyle: { borderRadius: [4, 4, 0, 0] } }],
        },
        table: { head: ['Day', 'Plays'], rows: data.by_weekday.labels.map((d, i) => [d, data.by_weekday.data[i]]) },
      },
    }
  })

  const serverSlots = $derived(data ? data.servers.map((_, i) => i) : [])
  // With two or more servers every time chart also gets a neutral 'ink' slot for its Total line.
  const serverSlotsWithTotal = $derived(serverSlots.length > 1 ? [...serverSlots, 'ink'] : serverSlots)
  const METHOD_SLOTS = [0, 2, 1, 'ink'] // direct play, direct stream, transcode keep their colours
</script>

<div class="toolbar">
  <div class="presets">
    {#each PRESETS as [n, text]}
      <button class:on={activePreset(n)} onclick={() => preset(n)}>{text}</button>
    {/each}
    <button onclick={allTime} disabled={!data?.earliest}>All time</button>
  </div>
  <form onsubmit={(e) => { e.preventDefault(); load() }}>
    <button type="button" onclick={() => step(-1)} aria-label="Earlier">&larr;</button>
    <input type="date" bind:value={start} max={end} aria-label="Start date" />
    <span class="muted">to</span>
    <input type="date" bind:value={end} min={start} max={today()} aria-label="End date" />
    <button type="button" onclick={() => step(1)} disabled={atPresent} aria-label="Later">&rarr;</button>
    <button>Apply</button>
  </form>
</div>

{#if error}
  <p class="bad">Could not load stats: {error}</p>
{:else if !data}
  <p class="muted">Loading...</p>
{:else if data.servers.length === 0}
  <div class="card"><p class="muted">Add a server first. Plays are recorded while Horus is running.</p></div>
{:else}
  <p class="muted summary">
    {data.total_plays} plays, {data.range.start} to {data.range.end}.
    {#if data.total_plays === 0}Nothing recorded in this range. Use the arrows to look further back.{/if}
  </p>
  <div class="grid">
    <section class="card wide">
      <div class="head">
        <h2>Plays over time</h2>
        <div class="seg" role="group" aria-label="Group by">
          {#each ['hour', 'day', 'week', 'month'] as g}
            <button class:on={gran === g} disabled={g === 'hour' && !hourOk}
              title={g === 'hour' && !hourOk ? `Hour view needs a range of ${HOURLY_MAX_DAYS} days or less` : undefined}
              onclick={() => pickGran(g)}>{label(g)}</button>
          {/each}
        </div>
      </div>
      <Chart option={built.plays.option} table={built.plays.table} slots={serverSlotsWithTotal} height={300} />
    </section>
    <section class="card wide">
      <h2>Direct play vs transcode over time</h2>
      <Chart option={built.methods.option} table={built.methods.table} slots={METHOD_SLOTS} />
    </section>
    <section class="card wide">
      <h2>Bandwidth over time</h2>
      <Chart option={built.bandwidth.option} table={built.bandwidth.table} slots={serverSlotsWithTotal} height={300} />
    </section>
    <section class="card"><h2>Plays by user</h2><Chart option={built.user.option} table={built.user.table} slots={[0]} height={built.user.height} /></section>
    <section class="card"><h2>Plays by platform</h2><Chart option={built.platform.option} table={built.platform.table} slots={[0]} height={built.platform.height} /></section>
    <section class="card"><h2>Plays by hour of day</h2><Chart option={built.hour.option} table={built.hour.table} slots={[0]} /></section>
    <section class="card"><h2>Plays by weekday</h2><Chart option={built.weekday.option} table={built.weekday.table} slots={[0]} /></section>
    <section class="card"><h2>Media type</h2><Chart option={built.types.option} table={built.types.table} slots={[0]} height={built.types.height} /></section>
  </div>
{/if}

<style>
  .toolbar { display: flex; flex-wrap: wrap; gap: 12px 24px; align-items: center;
    justify-content: space-between; margin-bottom: 16px; }
  .presets, form, .seg { display: flex; gap: 6px; align-items: center; }
  form input { width: auto; }
  button.on { border-color: var(--accent); color: var(--accent); font-weight: 600; }
  .summary { margin: 0 0 12px; }
  .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 18px; min-width: 0; }
  .wide { grid-column: 1 / -1; }
  .head { display: flex; justify-content: space-between; align-items: center; }
  h2 { margin: 0 0 6px; font-size: 15px; }
  .seg button { padding: 3px 10px; font-size: 13px; }
  .bad { color: var(--bad); }
  @media (max-width: 720px) { .grid { grid-template-columns: 1fr; } }
</style>
