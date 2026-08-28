import { useEffect, useState } from 'react'
import { fetchFeedbackWeekly } from '../api/client'
import type { FeedbackWeekly } from '../api/client'
import { Icon } from './Icon'

export function FeedbackSummary() {
  const [data, setData] = useState<FeedbackWeekly | null>(null)

  useEffect(() => {
    let alive = true
    const load = () => {
      fetchFeedbackWeekly()
        .then((d) => {
          if (alive) setData(d)
        })
        .catch(() => {})
    }
    load()
    const timer = window.setInterval(load, 60_000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  if (!data || data.total === 0) {
    return (
      <section className="feedback-summary" aria-label="本周回答反馈">
        <strong>本周反馈</strong>
        <p className="feedback-empty">还没有评价，用卡片上的 👍/👎 告诉我哪些回答有用</p>
      </section>
    )
  }

  return (
    <section className="feedback-summary" aria-label="本周回答反馈">
      <strong>本周反馈</strong>
      <div className="feedback-counts">
        <span className="fb-up">
          <Icon name="thumb-up" size={13} /> {data.up}
        </span>
        <span className="fb-down">
          <Icon name="thumb-down" size={13} /> {data.down}
        </span>
      </div>
      {data.down_dishes.length > 0 && (
        <p className="feedback-down-list">不满意：{data.down_dishes.join('、')}</p>
      )}
    </section>
  )
}
