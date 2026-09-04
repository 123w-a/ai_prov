import { useEffect, useState } from 'react'
import { fetchFeedbackWeekly, fetchWeeklyReport, fetchWeeklySummary } from '../api/client'
import type { WeeklySummaryResponse } from '../api/client'
import type { FeedbackWeekly } from '../api/client'
import type { WeeklyReport } from '../types'
import { Icon } from './Icon'

const LIGHT_LABELS = ['钠', '糖', '脂肪'] as const
const LIGHT_LEVELS: Record<string, { text: string; cls: string }> = {
  green: { text: '绿灯', cls: 'ok' },
  yellow: { text: '黄灯', cls: 'warn' },
  red: { text: '红灯', cls: 'risk' },
}
const TREND_COPY: Record<string, { text: string; cls: string }> = {
  improving: { text: '在好转', cls: 'ok' },
  worsening: { text: '在抬头', cls: 'risk' },
  stable: { text: '保持平稳', cls: 'flat' },
  insufficient: { text: '样本还少', cls: 'flat' },
}

export function WeeklyReportPage() {
  const [report, setReport] = useState<WeeklyReport | null>(null)
  const [feedback, setFeedback] = useState<FeedbackWeekly | null>(null)
  const [summary, setSummary] = useState<WeeklySummaryResponse | null>(null)
  const [summaryBusy, setSummaryBusy] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    Promise.all([fetchWeeklyReport(), fetchFeedbackWeekly().catch(() => null)])
      .then(([r, f]) => {
        if (!alive) return
        setReport(r)
        setFeedback(f)
      })
      .catch(() => {})
      .finally(() => {
        if (alive) setLoading(false)
      })
    // AI 小结异步补渲染：LLM 首次生成需数秒，不阻塞周报主体
    if (report?.has_data) {
      fetchWeeklySummary()
        .then((s) => {
          if (alive) setSummary(s)
        })
        .catch(() => {})
    }
    return () => {
      alive = false
    }
  }, [report?.has_data])

  const regenerate = () => {
    if (summaryBusy) return
    setSummaryBusy(true)
    setSummary(null)
    fetchWeeklySummary(true)
      .then((s) => setSummary(s))
      .catch(() => setSummary({ ai_summary: null, reason: 'regenerate_failed' }))
      .finally(() => setSummaryBusy(false))
  }

  if (loading) {
    return (
      <div className="weekly-page">
        <p className="weekly-loading">正在汇总近 7 天记录…</p>
      </div>
    )
  }

  if (!report || !report.has_data) {
    return (
      <div className="weekly-page">
        <section className="weekly-empty">
          <Icon name="leaf" size={30} />
          <h3>近 7 天还没有饮食决策记录</h3>
          <p>{report?.message || '去聊一道菜吧，吃完自动记一笔。'}</p>
        </section>
      </div>
    )
  }

  const lights = report.lights ?? {}
  const trends = report.light_trends ?? {}

  return (
    <div className="weekly-page">
      <section className="weekly-stats">
        <div className="weekly-stat">
          <strong>{report.meals}</strong>
          <span>本周记录餐数</span>
        </div>
        <div className="weekly-stat">
          <strong>{report.guardrail_triggers}</strong>
          <span>健康护栏介入次数</span>
        </div>
        <div className="weekly-stat">
          <strong>{feedback ? feedback.up : '—'}</strong>
          <span>回答获赞</span>
        </div>
        <div className="weekly-stat">
          <strong>{feedback ? feedback.down : '—'}</strong>
          <span>回答被踩</span>
        </div>
      </section>

      <section className="weekly-card summary">
        <div className="weekly-summary-head">
          <h3>AI 小结</h3>
          <button
            type="button"
            className="weekly-refresh-btn"
            disabled={summaryBusy}
            onClick={regenerate}
          >
            {summaryBusy ? '重新生成中…' : '重新生成'}
          </button>
        </div>
        {summary?.ai_summary ? (
          <p className="weekly-summary-text">{summary.ai_summary}</p>
        ) : summary && !summary.ai_summary && summary.reason === 'no_data' ? null : (
          <p className="weekly-summary-loading">正在生成本周小结…</p>
        )}
      </section>

      <section className="weekly-card">
        <h3>营养红绿灯</h3>
        {LIGHT_LABELS.map((label) => {
          const green = lights[`${label}:green`] ?? 0
          const yellow = lights[`${label}:yellow`] ?? 0
          const red = lights[`${label}:red`] ?? 0
          const total = green + yellow + red
          if (total === 0) return null
          const trend = trends[label] ? TREND_COPY[trends[label]] : null
          return (
            <div key={label} className="light-row">
              <strong>{label}</strong>
              <span className="light-counts">
                {(['green', 'yellow', 'red'] as const).map((lv) => {
                  const n = lv === 'green' ? green : lv === 'yellow' ? yellow : red
                  if (n === 0) return null
                  const meta = LIGHT_LEVELS[lv]
                  return (
                    <em key={lv} className={meta.cls}>
                      {meta.text} ×{n}
                    </em>
                  )
                })}
              </span>
              {trend && <span className={`light-trend ${trend.cls}`}>{trend.text}</span>}
            </div>
          )
        })}
      </section>

      {(report.top_dishes?.length ?? 0) > 0 && (
        <section className="weekly-card">
          <h3>常吃菜品 Top</h3>
          <div className="weekly-chips">
            {report.top_dishes!.map(([dish, n]) => (
              <span key={dish} className="weekly-chip">
                {dish}
                {n > 1 && <i>×{n}</i>}
              </span>
            ))}
          </div>
        </section>
      )}

      {(report.recommendations?.length ?? 0) > 0 && (
        <section className="weekly-card">
          <h3>下周建议</h3>
          <ul className="weekly-tips">
            {report.recommendations!.map((tip) => (
              <li key={tip}>{tip}</li>
            ))}
          </ul>
        </section>
      )}

      {feedback && feedback.down > 0 && feedback.down_dishes.length > 0 && (
        <section className="weekly-card warn">
          <h3>被踩的回答</h3>
          <p className="weekly-warn-text">
            这几道菜的建议你不满意（{feedback.down_dishes.join('、')}），已加入推荐避让，
            之后同类问题会换做法或给替代。
          </p>
        </section>
      )}

      <p className="weekly-range">
        统计区间：{report.range?.[0]} ~ {report.range?.[1]} · 数据来自每次决策的自动记账
      </p>
    </div>
  )
}
