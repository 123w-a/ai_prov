/**
 * 结果页动作条（户型图第 4 步下半场）：★ 收藏 + 赞 / 踩，
 * 让新前端自己能产生收藏（此前只有旧版首页有 ★）。
 *
 * 契约（2026-09-29 与 lave gpt-5.6-sol 商定后落地）：
 *  · 可见性：只对「成功且带 origin」的本轮显示。restored（来自收藏）整条隐藏——
 *    那条记录的编辑入口在收藏房，两个入口同屏只会互相打架；
 *    failed / 中断轮没有 record_id（finish 没到），落点不存在就整体隐藏，
 *    宁可不画，也不能把动作写到错误的记录上。
 *  · 新轮（非 archive）初始态是**确定**的：刚创建的记录必然未收藏、未评分，
 *    直接显示，不发多余请求（避免每次渲染的闪烁与竞态）。
 *  · 存档恢复（archive=true）必须回查 GET /api/sessions 取该消息的真实
 *    starred/feedback——刷新期间可能在收藏房改过；查失败一律「状态未加载」，
 *    绝不把未知画成「未收藏」。
 *  · 写操作全部以**后端返回值**为最终状态；失败保持原状并如实报错。
 */
import { useCallback, useEffect, useState } from 'react'
import { fetchSessions, sendMessageFeedback, starMessage } from '../../api/client.ts'
import type { RunState } from './model.ts'

export type RateValue = 'up' | 'down' | null

const errText = (e: unknown): string => (e instanceof Error ? e.message : String(e))

export interface ResultActions {
  /** 动作条是否该画（可见性判据见文件头）。 */
  visible: boolean
  /** 存档恢复的回查进行中（按钮禁用 + 状态小字）。 */
  loading: boolean
  /** 回查失败：状态未知，按钮整体不可信，只给「重试」。 */
  lookupError: string | null
  /** 写操作失败：状态未变，如实报错。 */
  actionError: string | null
  starred: boolean
  feedback: RateValue
  busy: boolean
  toggleStar: () => Promise<void>
  rate: (value: 'up' | 'down') => Promise<void>
  retry: () => void
}

export function useResultActions(run: RunState): ResultActions {
  const origin = run.origin
  const sid = origin?.sessionId
  const recId = origin?.recordId
  const visible =
    run.status === 'succeeded' && !run.restored && typeof sid === 'string' && typeof recId === 'number'

  const [starred, setStarred] = useState(false)
  const [feedback, setFeedback] = useState<RateValue>(null)
  const [loading, setLoading] = useState(false)
  const [lookupError, setLookupError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [gen, setGen] = useState(0)

  useEffect(() => {
    if (!visible || !run.archive) {
      // 新轮：刚创建的记录必然未收藏/未评分，用初值即可；不可见时顺带归零。
      setStarred(false)
      setFeedback(null)
      setLoading(false)
      setLookupError(null)
      return
    }
    if (typeof sid !== 'string' || typeof recId !== 'number') return
    let alive = true
    setLoading(true)
    setLookupError(null)
    fetchSessions()
      .then((sessions) => {
        if (!alive) return
        let found = false
        for (const s of sessions) {
          if (s.session_id !== sid) continue
          for (const m of s.messages ?? []) {
            if (m.id !== recId) continue
            setStarred(Boolean(m.starred))
            setFeedback(m.feedback ?? null)
            found = true
          }
        }
        // 找不到也是一条事实：会话被清过或编号对不上，如实说没找到，不画「未收藏」。
        setLookupError(
          found ? null : '状态未加载：这条记录在会话里没找到，先不猜它的收藏与评分状态。',
        )
        setLoading(false)
      })
      .catch(() => {
        if (!alive) return
        setLookupError('状态未加载：会话没读出来，先不猜它的收藏与评分状态。')
        setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [visible, run.archive, sid, recId, gen])

  const toggleStar = useCallback(async () => {
    if (!visible || busy || loading || typeof sid !== 'string' || typeof recId !== 'number') return
    setBusy(true)
    setActionError(null)
    try {
      const r = await starMessage(sid, recId, !starred)
      setStarred(r.starred)
    } catch (err) {
      setActionError(`收藏操作失败（状态没有变）：${errText(err)}`)
    } finally {
      setBusy(false)
    }
  }, [visible, busy, loading, sid, recId, starred])

  const rate = useCallback(
    async (value: 'up' | 'down') => {
      if (!visible || busy || loading || typeof sid !== 'string' || typeof recId !== 'number') return
      setBusy(true)
      setActionError(null)
      try {
        // 后端同值再点 = 取消，返回的是最终状态（null = 已取消），直接以它为准。
        const r = await sendMessageFeedback(sid, recId, value)
        setFeedback(r)
      } catch (err) {
        setActionError(`评分失败（状态没有变）：${errText(err)}`)
      } finally {
        setBusy(false)
      }
    },
    [visible, busy, loading, sid, recId],
  )

  const retry = useCallback(() => setGen((g) => g + 1), [])

  return { visible, loading, lookupError, actionError, starred, feedback, busy, toggleStar, rate, retry }
}
