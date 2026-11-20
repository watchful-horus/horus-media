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
  let monthly = $state(null) // last 12 calendar months, not tied to the date range
  let problems = $state(null) // playback problems in the same date range

  const PRESETS = [[7, '7 days'], [30, '30 days'], [90, '90 days'], [365, '1 year']]

  async function load() {
    try {
      const [stats, found] = await Promise.all([
        api.stats(start, end, granChoice === 'hour'),
        api.problems(start, end).catch(() => null), // the other graphs still show without it
      ])
      data = stats
      problems = found
      error = null
    } catch (e) {
      error = e.message
    }
  }
  async function loadMonthly() {
    try { monthly = await api.monthly() } catch { monthly = null }
  }
  onMount(() => { load(); loadMonthly() })

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

  // Lines are solid and gently smoothed, with no dots until you hover. The Total line is the
  // same but in neutral ink, thinner and dashed so it never hides the server lines, drawn above
  // the others and without an area fill.
  const SMOOTH = { smooth: 0.25, smoothMonotone: 'x' }
  const totalLine = (name, data) => ({
    name, type: 'line', data, showSymbol: false, z: 5, lineStyle: { width: 1.5, type: 'dashed' },
    ...SMOOTH,
    emphasis: { focus: 'series' },
  })
  const withTotal = (series) =>
    series.length < 2 ? series : [...series, {
      name: 'Total', total: true,
      data: series[0].data.map((_, i) => series.reduce((n, s) => n + s.data[i], 0)),
    }]

  const timeLines = (keys, series, g, extra = {}) => ({
    xAxis: { type: 'time' },
    yAxis: { type: 'value', minInterval: 1, min: 0 },
    tooltip: tip((p) => nice(p.value[0], g), (p) => p.value[1]),
    dataZoom: [{ type: 'inside' }], // scroll to zoom, drag to pan; no slider bar
    series: series.map((s) => (s.total ? totalLine(s.name, keys.map((k, i) => [k, s.data[i]])) : {
      name: s.name, type: 'line', data: keys.map((k, i) => [k, s.data[i]]),
      showSymbol: false, symbolSize: 8, lineStyle: { width: 2 }, ...SMOOTH,
      areaStyle: { opacity: 0.08 }, emphasis: { focus: 'series' }, ...extra,
    })),
  })

  const timeTable = (keys, series, g) => ({
    head: ['Date', ...series.map((s) => label(s.name))],
    rows: keys.map((k, i) => [nice(k, g), ...series.map((s) => s.data[i])]),
  })

  // Phones only: a long name beside a bar ends in "..." so the bars keep their room. The whole
  // name still shows when the bar is tapped, and in "View as table".
  const PHONE = '(max-width: 720px)'
  let narrow = $state(matchMedia(PHONE).matches)
  $effect(() => {
    const mq = matchMedia(PHONE)
    const on = () => (narrow = mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  })
  const NAME_MAX = 16
  const shortName = (s) => (String(s).length > NAME_MAX ? `${String(s).slice(0, NAME_MAX - 1)}\u2026` : s)

  const hbar = (items, unit = 'Plays') => {
    const top = items.slice(0, 10)
    return {
      option: {
        legend: { show: false },
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
          ...(narrow ? { formatter: (params) => `${esc(top[params[0].dataIndex].name)}<br/>` +
            params.map((p) => `${p.marker} ${esc(p.seriesName)} <b>${p.value}</b>`).join('<br/>') } : {}) },
        grid: { top: 8, bottom: 8 },
        xAxis: { type: 'value', minInterval: 1 },
        yAxis: { type: 'category', inverse: true, data: top.map((i) => (narrow ? shortName(i.name) : i.name)) },
        series: [{ name: unit, type: 'bar', data: top.map((i) => i.count), barMaxWidth: 16,
          itemStyle: { borderRadius: [0, 4, 4, 0] } }],
      },
      height: Math.max(110, top.length * 30 + 20),
      table: { head: ['Name', unit], rows: items.map((i) => [i.name, i.count]) },
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
          yAxis: { type: 'value', min: 0, axisLabel: { formatter: (v) => kbps(v) } },
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
            name: s.server, type: 'line', data: s.points, showSymbol: false, symbolSize: 8,
            lineStyle: { width: 2 }, ...SMOOTH, areaStyle: { opacity: 0.08 }, emphasis: { focus: 'series' },
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
        // Each bar is the day's plays stacked by media type; the tooltip adds up the total.
        option: {
          tooltip: {
            trigger: 'axis', axisPointer: { type: 'shadow' },
            formatter: (params) => `${esc(params[0].axisValueLabel)}<br/>` +
              params.map((p) => `${p.marker} ${esc(p.seriesName)} <b>${p.value}</b>`).join('<br/>') +
              (params.length > 1 ? `<br/>Total <b>${params.reduce((n, p) => n + p.value, 0)}</b>` : ''),
          },
          xAxis: { type: 'category', data: data.by_weekday.labels },
          yAxis: { type: 'value', minInterval: 1 },
          series: data.by_weekday.by_type.map((t) => ({
            name: label(t.name), type: 'bar', stack: 'plays', data: t.data, barMaxWidth: 36,
            itemStyle: { borderColor: 'transparent', borderWidth: 1 }, emphasis: { focus: 'series' },
          })),
        },
        table: {
          head: ['Day', ...data.by_weekday.by_type.map((t) => label(t.name)), 'Total'],
          rows: data.by_weekday.labels.map((d, i) =>
            [d, ...data.by_weekday.by_type.map((t) => t.data[i]), data.by_weekday.data[i]]),
        },
      },
    }
  })

  // Playback problems: stacked bars per day/week/month by kind, who and what had the most,
  // and the latest ones as a list.
  const lasted = (sec) => (sec < 90 ? `${sec} s` : `${Math.round(sec / 60)} min`)
  const problemCharts = $derived.by(() => {
    if (!problems || problems.total === 0) return null
    const g = gran === 'hour' ? 'day' : gran
    const rolled = rollup(problems.per_day.days, problems.per_day.series, g)
    const series = rolled.series.filter((s) => s.data.some((v) => v))
    return {
      time: {
        option: {
          tooltip: {
            trigger: 'axis', axisPointer: { type: 'shadow' },
            formatter: (params) => `${esc(nice(params[0].axisValue, g))}<br/>` +
              params.map((p) => `${p.marker} ${esc(p.seriesName)} <b>${p.value}</b>`).join('<br/>'),
          },
          xAxis: { type: 'category', data: rolled.keys,
            axisLabel: { formatter: (k) => parse(k).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) } },
          yAxis: { type: 'value', minInterval: 1 },
          series: series.map((s) => ({
            name: s.name, type: 'bar', stack: 'problems', data: s.data, barMaxWidth: 36,
            itemStyle: { borderColor: 'transparent', borderWidth: 1 }, emphasis: { focus: 'series' },
          })),
        },
        table: timeTable(rolled.keys, series, g),
        // Each kind keeps its colour, whichever of them happened in this range.
        slots: series.map((s) => [3, 4, 2, 5][rolled.series.indexOf(s)]),
      },
      user: hbar(problems.by_user, 'Problems'),
      device: hbar(problems.by_device, 'Problems'),
    }
  })
  const problemsNote = $derived(
    !problems ? null
      : !problems.since ? 'Nothing recorded yet. Horus notes buffering, stalled playback and slow transcodes while it is running.'
        : problems.total === 0 ? 'None in this range.'
          : problems.since.slice(0, 10) > problems.range.start ? `Recorded since ${nice(problems.since, 'day')}.` : null,
  )

  const monthLabel = (key) => parse(key).toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
  const monthlyChart = $derived.by(() => {
    if (!monthly) return null
    const series = withTotal(monthly.series.map((s) => ({ name: s.server, data: s.data })))
    const servers = series.filter((s) => !s.total)
    return {
      option: {
        tooltip: {
          trigger: 'axis', axisPointer: { type: 'shadow' },
          formatter: (params) => `${esc(params[0].axisValueLabel)}<br/>` +
            params.map((p) => `${p.marker} ${esc(p.seriesName)} <b>${p.value}</b>`).join('<br/>') +
            (params.length > 1 ? `<br/>Total <b>${params.reduce((n, p) => n + p.value, 0)}</b>` : ''),
        },
        xAxis: { type: 'category', data: monthly.months.map(monthLabel) },
        yAxis: { type: 'value', minInterval: 1 },
        series: servers.map((s) => ({
          name: s.name, type: 'bar', stack: 'plays', data: s.data, barMaxWidth: 36,
          itemStyle: { borderColor: 'transparent', borderWidth: 1 }, emphasis: { focus: 'series' },
        })),
      },
      table: {
        head: ['Month', ...series.map((s) => label(s.name))],
        rows: monthly.months.map((k, i) => [monthLabel(k), ...series.map((s) => s.data[i])]),
      },
    }
  })

  const oneDecimal = (n) => (Math.round(n * 10) / 10).toLocaleString()
  const monthlyHoursChart = $derived.by(() => {
    if (!monthly?.hours) return null
    const series = withTotal(monthly.hours.map((s) => ({ name: s.server, data: s.data })))
    const servers = series.filter((s) => !s.total)
    return {
      option: {
        tooltip: {
          trigger: 'axis', axisPointer: { type: 'shadow' },
          formatter: (params) => `${esc(params[0].axisValueLabel)}<br/>` +
            params.map((p) => `${p.marker} ${esc(p.seriesName)} <b>${oneDecimal(p.value)} h</b>`).join('<br/>') +
            (params.length > 1 ? `<br/>Total <b>${oneDecimal(params.reduce((n, p) => n + p.value, 0))} h</b>` : ''),
        },
        xAxis: { type: 'category', data: monthly.months.map(monthLabel) },
        yAxis: { type: 'value', min: 0, axisLabel: { formatter: '{value} h' } },
        series: servers.map((s) => ({
          name: s.name, type: 'bar', stack: 'hours', data: s.data, barMaxWidth: 36,
          itemStyle: { borderColor: 'transparent', borderWidth: 1 }, emphasis: { focus: 'series' },
        })),
      },
      table: {
        head: ['Month', ...series.map((s) => label(s.name))],
        rows: monthly.months.map((k, i) => [monthLabel(k), ...series.map((s) => oneDecimal(s.data[i]))]),
      },
    }
  })
  const monthlyHoursNote = $derived(
    !monthly?.hours ? null
      : 'From the start of each play to the last time it was seen, so a pause counts.' +
        (monthly.imported
          ? ` ${monthly.imported.toLocaleString()} of the ${monthly.total.toLocaleString()} plays were imported from the server's own history; those have no length and add no hours.`
          : ''),
  )

  // A note under the bandwidth chart when recording started part-way through the range.
  const bandwidthNote = $derived(
    data?.bandwidth_since && data.bandwidth_since.slice(0, 10) > data.range.start
      ? `Bandwidth is only recorded while Horus is running. The first sample is from ${nice(data.bandwidth_since, 'day')}.`
      : data && !data.bandwidth_since && data.servers.length ? 'No bandwidth has been recorded yet.' : null,
  )
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
    {#if data.total_plays === 0 && !data.earliest}Nothing recorded yet. Horus records plays while it is running, so this fills up as people watch. To bring in older plays, press Import history under Settings &gt; Servers.
    {:else if data.total_plays === 0}Nothing recorded in this range. Use the arrows to look further back.{/if}
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
      {#if bandwidthNote}<p class="muted note">{bandwidthNote}</p>{/if}
    </section>
    {#if monthlyChart}
      <section class="card wide">
        <h2>Plays per month, last 12 months</h2>
        <Chart option={monthlyChart.option} table={monthlyChart.table} slots={serverSlots} />
      </section>
      {#if monthlyHoursChart}
        <section class="card wide">
          <h2>Hours watched per month, last 12 months</h2>
          <Chart option={monthlyHoursChart.option} table={monthlyHoursChart.table} slots={serverSlots} />
          {#if monthlyHoursNote}<p class="muted note">{monthlyHoursNote}</p>{/if}
        </section>
      {/if}
    {/if}
    <section class="card"><h2>Plays by user</h2><Chart option={built.user.option} table={built.user.table} slots={[0]} height={built.user.height} /></section>
    <section class="card"><h2>Plays by platform</h2><Chart option={built.platform.option} table={built.platform.table} slots={[0]} height={built.platform.height} /></section>
    <section class="card"><h2>Plays by hour of day</h2><Chart option={built.hour.option} table={built.hour.table} slots={[0]} /></section>
    <section class="card"><h2>Plays by weekday</h2><Chart option={built.weekday.option} table={built.weekday.table} slots={[0, 1, 2, 3, 4, 5]} /></section>
    <section class="card"><h2>Media type</h2><Chart option={built.types.option} table={built.types.table} slots={[0]} height={built.types.height} /></section>
    {#if problems}
      <section class="card wide">
        <h2>Playback problems over time</h2>
        {#if problemCharts}
          <Chart option={problemCharts.time.option} table={problemCharts.time.table} slots={problemCharts.time.slots} />
        {/if}
        {#if problemsNote}<p class="muted note">{problemsNote}</p>{/if}
      </section>
      {#if problemCharts}
        <section class="card"><h2>Problems by user</h2><Chart option={problemCharts.user.option} table={problemCharts.user.table} slots={[3]} height={problemCharts.user.height} /></section>
        <section class="card"><h2>Problems by player</h2><Chart option={problemCharts.device.option} table={problemCharts.device.table} slots={[3]} height={problemCharts.device.height} /></section>
        <section class="card wide">
          <h2>Latest playback problems</h2>
          <div class="scroll">
            <table class="problems">
              <thead><tr><th>When</th><th>Problem</th><th>User</th><th>Title</th><th>Player</th><th>Server</th><th>Lasted</th><th>Detail</th></tr></thead>
              <tbody>
                {#each problems.recent as p (p.id)}
                  <!-- data-label is shown on phones, where each row becomes a small block. -->
                  <tr>
                    <td class="nowrap when">{new Date(p.started_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}</td>
                    <td class="nowrap kind">{problems.kinds[p.kind] ?? p.kind}</td>
                    <td data-label="User">{p.user}</td>
                    <td class="long" data-label="Title">{p.title}</td>
                    <td data-label="Player"><span>{p.device}{#if p.location}{' '}<span class="muted">({p.location})</span>{/if}</span></td>
                    <td data-label="Server">{p.server}</td>
                    <td class="nowrap" data-label="Lasted">{p.ended_at ? lasted(p.duration_seconds) : 'Now'}</td>
                    <td class="long muted" class:none={!p.detail} data-label="Detail">{p.detail ?? ''}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
          {#if problems.total > problems.recent.length}
            <p class="muted note">Showing the latest {problems.recent.length} of {problems.total}.</p>
          {/if}
        </section>
      {/if}
    {/if}
  </div>
{/if}

<style>
  .toolbar { display: flex; flex-wrap: wrap; gap: 12px 24px; align-items: center;
    justify-content: space-between; margin-bottom: 16px; }
  .presets, form, .seg { display: flex; gap: 6px; align-items: center; }
  form input { width: auto; }
  button.on { border-color: var(--accent); color: var(--accent); font-weight: 600; }
  .summary { margin: 0 0 12px; }
  .note { margin: 6px 0 0; font-size: 12.5px; }
  .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 18px; min-width: 0; }
  .wide { grid-column: 1 / -1; }
  .head { display: flex; justify-content: space-between; align-items: center; }
  h2 { margin: 0 0 6px; font-size: 15px; }
  .seg button { padding: 3px 10px; font-size: 13px; }
  .scroll { overflow-x: auto; }
  .problems { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  .problems th { text-align: left; color: var(--muted); font-weight: 500; }
  .problems th, .problems td { padding: 5px 12px 5px 0; border-bottom: 1px solid var(--border);
    vertical-align: top; }
  .problems td.long { overflow-wrap: anywhere; min-width: 140px; }
  .nowrap { white-space: nowrap; }
  .bad { color: var(--bad); }
  /* Phones only. The range buttons share one row; the two dates share the next and the arrows
     and Apply the one after; the latest problems become one small block each instead of a
     table that is wider than the screen. */
  @media (max-width: 720px) {
    .grid { grid-template-columns: 1fr; }
    .toolbar { gap: 8px; }
    .presets, form { width: 100%; }
    .presets button { flex: 1 1 0; padding: 6px 2px; white-space: nowrap; }
    form { flex-wrap: wrap; }
    form input, form span { order: 1; }
    form input { flex: 1 1 40%; min-width: 0; }
    form button { order: 2; flex: 1 1 25%; }
    .card { padding: 12px 14px; }
    .head { flex-wrap: wrap; gap: 4px 12px; margin-bottom: 6px; }
    .head h2 { margin: 0; }
    .seg button { padding: 3px 12px; }
    .scroll { overflow-x: visible; }
    .problems thead { display: none; }
    .problems, .problems tbody, .problems tr, .problems td { display: block; }
    .problems tr { padding: 10px 0; border-bottom: 1px solid var(--border); }
    .problems tr:last-child { border-bottom: 0; }
    .problems td, .problems td.long { border: 0; padding: 1px 0; min-width: 0; white-space: normal;
      overflow-wrap: anywhere; }
    .problems td[data-label] { display: grid; grid-template-columns: 58px minmax(0, 1fr); gap: 10px; }
    .problems td[data-label]::before { content: attr(data-label); color: var(--muted); }
    .problems td.when { font-size: 12.5px; color: var(--muted); }
    .problems td.kind { font-size: 14.5px; font-weight: 600; padding-bottom: 3px; }
    .problems td.none { display: none; }
  }
</style>
