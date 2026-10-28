<script>
  import { onMount } from 'svelte'
  import * as echarts from 'echarts/core'
  import { LineChart, BarChart } from 'echarts/charts'
  import { GridComponent, TooltipComponent, LegendComponent, DataZoomComponent } from 'echarts/components'
  import { CanvasRenderer } from 'echarts/renderers'

  // Only the pieces the graphs use, so the bundle stays small.
  echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent,
    DataZoomComponent, CanvasRenderer])

  // option: ECharts option (series/axes only; theming is applied here).
  // slots: which categorical colour each series uses, so a colour follows its entity.
  // table: { head: [...], rows: [[...]] } offers an accessible "View as table" alternative.
  let { option, height = 260, slots = null, table = null } = $props()
  let el
  let chart
  let showTable = $state(false)

  const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()

  function styleAxis(axis, t) {
    if (!axis) return axis
    const one = (a) => ({
      axisTick: { show: false },
      axisLine: { lineStyle: { color: t.axis } },
      axisLabel: { color: t.tick, hideOverlap: true },
      splitLine: { show: a.type === 'value', lineStyle: { color: t.grid } },
      ...a,
    })
    return Array.isArray(axis) ? axis.map(one) : one(axis)
  }

  function render() {
    if (!chart || !option || showTable) return
    const t = {
      text: token('--text'), tick: token('--tick'), grid: token('--grid'),
      axis: token('--axis'), panel: token('--panel'),
      series: Array.from({ length: 8 }, (_, i) => token(`--series-${i + 1}`)),
    }
    const count = option.series?.length ?? 0
    // A slot is a categorical colour number, or 'ink' for the neutral Total line.
    const colors = slots ? slots.map((s) => (s === 'ink' ? t.text : t.series[s])) : t.series
    chart.setOption(
      {
        color: colors,
        textStyle: { color: t.tick },
        ...option,
        // A legend only when there is more than one series to tell apart.
        legend: {
          show: count >= 2, top: 0, icon: 'roundRect', itemWidth: 14, itemHeight: 4,
          textStyle: { color: t.text }, ...option.legend,
        },
        tooltip: {
          trigger: 'axis', confine: true, backgroundColor: t.panel, borderColor: t.grid,
          textStyle: { color: t.text },
          axisPointer: { type: 'line', lineStyle: { color: t.axis, width: 1 } },
          ...option.tooltip,
        },
        grid: { left: 8, right: 16, top: count >= 2 ? 36 : 12, bottom: 8, containLabel: true, ...option.grid },
        xAxis: styleAxis(option.xAxis, t),
        yAxis: styleAxis(option.yAxis, t),
        dataZoom: option.dataZoom?.map((z) =>
          z.type === 'slider'
            ? { borderColor: t.grid, textStyle: { color: t.tick }, dataBackground: { lineStyle: { color: t.axis }, areaStyle: { color: t.grid } },
                fillerColor: 'rgba(120,120,120,0.15)', handleStyle: { color: t.axis, borderColor: t.axis }, ...z }
            : z,
        ),
      },
      true,
    )
  }

  onMount(() => {
    chart = echarts.init(el)
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(el)
    const mq = matchMedia('(prefers-color-scheme: dark)')
    mq.addEventListener('change', render)
    addEventListener('horus:theme', render)
    render()
    return () => {
      ro.disconnect()
      mq.removeEventListener('change', render)
      removeEventListener('horus:theme', render)
      chart.dispose()
    }
  })

  $effect(() => {
    option
    slots
    showTable
    render()
  })
</script>

<div class="wrap">
  <div bind:this={el} style="height:{height}px;width:100%;{showTable ? 'display:none' : ''}"></div>
  {#if showTable && table}
    <div class="table" style="max-height:{height}px">
      <table>
        <thead><tr>{#each table.head as h}<th>{h}</th>{/each}</tr></thead>
        <tbody>
          {#each table.rows as row}
            <tr>{#each row as cell}<td>{cell}</td>{/each}</tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
  {#if table}
    <button class="link" onclick={() => (showTable = !showTable)}>
      {showTable ? 'View chart' : 'View as table'}
    </button>
  {/if}
</div>

<style>
  .wrap { position: relative; }
  .table { overflow: auto; border: 1px solid var(--border); border-radius: 6px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
  th { text-align: left; position: sticky; top: 0; background: var(--panel); color: var(--muted);
    font-weight: 500; padding: 6px 10px; }
  td { padding: 4px 10px; border-top: 1px solid var(--border); }
  .link { border: 0; background: none; padding: 4px 0; color: var(--muted); font-size: 12px;
    text-decoration: underline; }
</style>
