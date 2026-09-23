import { useEffect, useMemo, useState } from 'react'

type Condition = {
  field: string
  operator: string
  value: number | boolean
  label: string
}

type ScreenRule = {
  markets: string[]
  exclude_st: boolean
  exclude_suspended: boolean
  min_listed_days: number
  conditions: Condition[]
  sort: { field: string; direction: 'asc' | 'desc' }
  limit: number
}

type ParsedRule = {
  summary: string
  rule: ScreenRule
  warnings: string[]
}

type Stock = {
  code: string
  name: string
  industry: string
  close: number
  pe: number
  pb: number
  market_cap_yi: number
  amount_yi: number
  change_20d: number
  volume_ratio_5d: number
  volatility_20d: number
  ma_bullish: boolean
  reasons: string[]
}

type ScreenResult = {
  trade_date: string
  total_universe: number
  matched_count: number
  stocks: Stock[]
}

type BacktestResult = {
  id: number
  metrics: {
    total_return_pct: number
    annual_return_pct: number
    max_drawdown_pct: number
    benchmark_return_pct: number
    win_rate_pct: number
    periods: number
  }
  curve: { date: string; equity: number; benchmark: number }[]
  periods: { buy_date: string; sell_date: string; stocks: string[]; return_pct: number; ending_equity: number }[]
  notes: string[]
}

type Preset = { name: string; text: string }
type Strategy = { id: number; name: string; description: string; created_at: string }

const API_BASE = import.meta.env.VITE_API_BASE || '/stock/api'

const fallbackPresets: Preset[] = [
  { name: '低估值', text: '排除ST、停牌和上市不足一年的股票，市盈率低于20，市净率低于3，按市盈率从低到高取前10只。' },
  { name: '放量突破', text: '排除ST和新股，选择近20日上涨且近5日成交量放大1.2倍的股票，按近20日涨幅排序取前10只。' },
  { name: '均线多头', text: '排除ST、停牌和新股，选择均线多头排列的股票，按近20日涨幅从高到低取前10只。' },
  { name: '近期强势', text: '排除ST和新股，选择近20日涨幅大于5%的股票，按近20日涨幅从高到低取前10只。' },
  { name: '低波动', text: '排除ST、停牌和新股，选择近20日年化波动低于25%的股票，按波动率从低到高取前10只。' },
]

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `请求失败（${response.status}）`)
  }
  return response.json() as Promise<T>
}

function formatPct(value: number) {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function EquityChart({ result }: { result: BacktestResult }) {
  const width = 760
  const height = 230
  const padding = 24
  const values = result.curve.flatMap((point) => [point.equity, point.benchmark])
  const min = Math.min(...values)
  const max = Math.max(...values)
  const spread = max - min || 1
  const points = (key: 'equity' | 'benchmark') => result.curve.map((point, index) => {
    const x = padding + index / Math.max(1, result.curve.length - 1) * (width - padding * 2)
    const y = height - padding - (point[key] - min) / spread * (height - padding * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')

  return <div className="chart-wrap">
    <div className="chart-legend"><span><i className="strategy-line" />策略</span><span><i className="benchmark-line" />基准</span></div>
    <svg aria-label="策略与基准收益曲线" role="img" viewBox={`0 0 ${width} ${height}`}>
      <line className="chart-axis" x1={padding} x2={width - padding} y1={height - padding} y2={height - padding} />
      <polyline className="chart-strategy" fill="none" points={points('equity')} />
      <polyline className="chart-benchmark" fill="none" points={points('benchmark')} />
    </svg>
    <div className="chart-dates"><span>{result.curve[0]?.date}</span><span>{result.curve.at(-1)?.date}</span></div>
  </div>
}

export default function App() {
  const [prompt, setPrompt] = useState(fallbackPresets[1].text)
  const [presets, setPresets] = useState(fallbackPresets)
  const [parsed, setParsed] = useState<ParsedRule | null>(null)
  const [screen, setScreen] = useState<ScreenResult | null>(null)
  const [backtest, setBacktest] = useState<BacktestResult | null>(null)
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [strategyName, setStrategyName] = useState('我的第一套策略')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    request<{ items: Preset[] }>('/presets').then((value) => setPresets(value.items)).catch(() => undefined)
    request<Strategy[]>('/strategies').then(setStrategies).catch(() => undefined)
  }, [])

  const analyze = async () => {
    setBusy('正在理解条件并筛选…')
    setError('')
    setBacktest(null)
    try {
      const parsedRule = await request<ParsedRule>('/skill/parse', {
        method: 'POST',
        body: JSON.stringify({ text: prompt }),
      })
      const screened = await request<ScreenResult>('/screen', {
        method: 'POST',
        body: JSON.stringify({ rule: parsedRule.rule }),
      })
      setParsed(parsedRule)
      setScreen(screened)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '筛选失败')
    } finally {
      setBusy('')
    }
  }

  const saveStrategy = async () => {
    if (!parsed) return
    setBusy('正在保存策略…')
    setError('')
    try {
      const saved = await request<Strategy>('/strategies', {
        method: 'POST',
        body: JSON.stringify({ name: strategyName, description: prompt, rule: parsed.rule }),
      })
      setStrategies((current) => [saved, ...current])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存失败')
    } finally {
      setBusy('')
    }
  }

  const runBacktest = async () => {
    if (!parsed) return
    setBusy('正在逐日执行回测…')
    setError('')
    try {
      const result = await request<BacktestResult>('/backtests', {
        method: 'POST',
        body: JSON.stringify({
          rule: parsed.rule,
          initial_cash: 100000,
          rebalance_days: 20,
          position_count: 5,
          commission_rate: 0.0003,
          slippage_rate: 0.0005,
        }),
      })
      setBacktest(result)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '回测失败')
    } finally {
      setBusy('')
    }
  }

  const conditionLabels = useMemo(() => parsed?.rule.conditions.map((condition) =>
    `${condition.label || condition.field} ${condition.operator} ${typeof condition.value === 'boolean' ? (condition.value ? '是' : '否') : condition.value}`
  ) || [], [parsed])

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand-mark">S</div>
      <div><strong>Stock Lab</strong><span>个人选股与回测实验室</span></div>
      <div className="demo-badge">测试数据</div>
    </header>

    <main>
      <section className="hero">
        <div><span className="eyebrow">RESEARCH WORKBENCH</span><h1>把选股想法变成<br /><em>可验证的策略</em></h1></div>
        <p>用自然语言描述条件，由固定程序筛选并回测。当前只使用模拟行情，不连接券商、不产生真实交易。</p>
      </section>

      <section className="workspace-grid">
        <article className="panel query-panel">
          <div className="panel-title"><div><span>01</span><h2>描述选股条件</h2></div><small>AI Skill 入口</small></div>
          <div className="preset-list">
            {presets.map((preset) => <button key={preset.name} onClick={() => setPrompt(preset.text)} type="button">{preset.name}</button>)}
          </div>
          <textarea onChange={(event) => setPrompt(event.target.value)} value={prompt} />
          <button className="primary-button" disabled={Boolean(busy)} onClick={analyze} type="button">{busy || '解析条件并选股'}</button>
          {error && <div className="error-box">{error}</div>}
        </article>

        <aside className="panel rule-panel">
          <div className="panel-title"><div><span>02</span><h2>结构化规则</h2></div></div>
          {!parsed ? <div className="empty-state">提交左侧条件后，这里会显示程序实际执行的规则。</div> : <>
            <p className="rule-summary">{parsed.summary}</p>
            <div className="rule-tags">
              {parsed.rule.exclude_st && <span>排除 ST</span>}
              {parsed.rule.exclude_suspended && <span>排除停牌</span>}
              {parsed.rule.min_listed_days > 0 && <span>上市 ≥ {parsed.rule.min_listed_days} 天</span>}
              {conditionLabels.map((label) => <span key={label}>{label}</span>)}
            </div>
            <dl className="rule-meta"><div><dt>排序</dt><dd>{parsed.rule.sort.field} · {parsed.rule.sort.direction === 'desc' ? '降序' : '升序'}</dd></div><div><dt>数量</dt><dd>最多 {parsed.rule.limit} 只</dd></div></dl>
            {parsed.warnings.map((warning) => <p className="warning" key={warning}>{warning}</p>)}
          </>}
        </aside>
      </section>

      {screen && <section className="panel result-panel">
        <div className="panel-title"><div><span>03</span><h2>筛选结果</h2></div><small>{screen.trade_date} · {screen.matched_count}/{screen.total_universe} 只</small></div>
        <div className="strategy-toolbar">
          <input aria-label="策略名称" onChange={(event) => setStrategyName(event.target.value)} value={strategyName} />
          <button disabled={Boolean(busy)} onClick={saveStrategy} type="button">保存策略</button>
          <button className="accent-button" disabled={Boolean(busy)} onClick={runBacktest} type="button">使用测试行情回测</button>
        </div>
        <div className="stock-table">
          <div className="stock-head"><span>股票</span><span>行业</span><span>收盘价</span><span>PE / PB</span><span>近20日</span><span>量比</span><span>入选原因</span></div>
          {screen.stocks.length === 0 ? <div className="table-empty">当前测试数据中没有满足全部条件的股票，请适当放宽条件。</div> : screen.stocks.map((stock) => <div className="stock-row" key={stock.code}>
            <span><strong>{stock.name}</strong><small>{stock.code}</small></span>
            <span>{stock.industry}</span>
            <span>{stock.close.toFixed(2)}</span>
            <span>{stock.pe.toFixed(1)} / {stock.pb.toFixed(1)}</span>
            <span className={stock.change_20d >= 0 ? 'up' : 'down'}>{formatPct(stock.change_20d)}</span>
            <span>{stock.volume_ratio_5d.toFixed(2)}</span>
            <span className="reason-list">{stock.reasons.join(' · ') || '基础排序入选'}</span>
          </div>)}
        </div>
      </section>}

      {backtest && <section className="panel backtest-panel">
        <div className="panel-title"><div><span>04</span><h2>回测报告</h2></div><small>编号 #{backtest.id}</small></div>
        <div className="metric-grid">
          <div><span>策略总收益</span><strong className={backtest.metrics.total_return_pct >= 0 ? 'up' : 'down'}>{formatPct(backtest.metrics.total_return_pct)}</strong></div>
          <div><span>年化收益</span><strong>{formatPct(backtest.metrics.annual_return_pct)}</strong></div>
          <div><span>最大回撤</span><strong className="down">{formatPct(backtest.metrics.max_drawdown_pct)}</strong></div>
          <div><span>模拟基准</span><strong>{formatPct(backtest.metrics.benchmark_return_pct)}</strong></div>
          <div><span>周期胜率</span><strong>{backtest.metrics.win_rate_pct.toFixed(1)}%</strong></div>
          <div><span>调仓周期数</span><strong>{backtest.metrics.periods}</strong></div>
        </div>
        <EquityChart result={backtest} />
        <div className="backtest-notes">{backtest.notes.map((note) => <p key={note}>• {note}</p>)}</div>
      </section>}

      <section className="panel saved-panel">
        <div className="panel-title"><div><span>05</span><h2>已保存策略</h2></div><small>{strategies.length} 套</small></div>
        {strategies.length === 0 ? <div className="empty-state">暂时没有保存的策略。</div> : <div className="strategy-list">{strategies.map((strategy) => <div key={strategy.id}><strong>{strategy.name}</strong><span>{strategy.description || '无说明'}</span><small>{new Date(strategy.created_at).toLocaleString('zh-CN')}</small></div>)}</div>}
      </section>
    </main>

    <footer>Stock Lab 使用测试数据，仅用于个人学习和策略研究，不构成任何投资建议。</footer>
  </div>
}

