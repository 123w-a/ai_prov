import { useCallback, useEffect, useState } from 'react'
import {
  fetchFavorites,
  fetchSessions,
  forgetDish,
  sendMessageFeedback,
  starMessage,
} from '../../api/client.ts'
import type { ChefAnswer, FavoriteItem } from '../../types.ts'

/**
 * 收藏房间的数据与动作（2026-09-29 户型图第 4 步）。
 *
 * ══ 三条数据纪律（与 firstScreen.ts 同源）════════════════════════════════
 *  1 只读优先：列表只来自 GET /api/favorites；写操作只碰后端已有的四个端点
 *    （star / feedback / forget-dish），不新造接口、不改后端。
 *  2 失败要说话：读失败 ≠ 空列表。error 与「空」是两个状态，页面各画各的。
 *  3 不知道就说不知道：赞/踩的初始状态收藏接口**不带**（实测 FavoriteItem
 *    无 feedback 字段），必须另拉 GET /api/sessions 建真实映射；拉失败时
 *    ratingsReady=false，页面写明「状态未加载」——**绝不把未知画成没点过**。
 *
 * ══ 为什么恢复详情只认 answer ═══════════════════════════════════════════
 * FavoriteItem 只有当时那轮的 answer（结构化 JSON），没有 body / elapsed /
 * events——所以「回今晚结果详情」只能是**有限恢复**：有 answer 就展示它真的
 * 有的字段，用时与过程账目一律不列（列出来只能是 0 或编的）；answer=null
 * （纯散文那轮）连内容都没存，退回「把原话带回今晚输入框」。
 */

export type FavRating = 'up' | 'down'

/** 条目键 `${sid}:${rec}`，与后端 star / feedback 两个端点的定位一致。 */
export type FavKey = string

/** 收藏条目的视图模型。views 层只读它，不碰 FavoriteItem 原始字段。 */
export interface FavEntry {
  key: FavKey
  sid: string
  recId: number
  dish: string
  /** 来源会话标题；后端为空时回落到用户原话（与旧 FavoritesPanel 同规则）。 */
  from: string
  /** 用户这一轮的原话。恢复详情时它就是结果页的回声，也是无详情时的预填话头。 */
  ask: string
  imageUrl: string | null
  /** 当时那轮的结构化答案；null = 收藏接口没存下内容（多为纯散文轮）。 */
  detail: ChefAnswer | null
}

export function favKey(sid: string, recId: number): FavKey {
  return `${sid}:${recId}`
}

function toEntry(item: FavoriteItem): FavEntry {
  const ask = item.user_text ?? ''
  return {
    key: favKey(item.sid, item.rec_id),
    sid: item.sid,
    recId: item.rec_id,
    // 纯散文轮后端把 dish 退化成原话截断——照它给的显示，不替它发明菜名。
    dish: item.dish || '这顿没有菜名',
    from: item.session_title || ask || '来源会话',
    ask,
    imageUrl: item.image_url ?? null,
    detail: item.answer ?? null,
  }
}

export interface FavoritesState {
  /** null = 还在读；[] = 读到了、确实没有收藏。两者不许合并。 */
  entries: FavEntry[] | null
  error: string | null
  ratings: Record<FavKey, FavRating>
  /** 赞/踩初始态是否读到。false 时页面必须写明「状态未加载」。 */
  ratingsReady: boolean
  reload: () => void
  /** 乐观移除（取消收藏时先从列表摘掉，失败再 reload 回滚）。 */
  drop: (key: FavKey) => void
  /** 用后端返回的真实终态更新某条的赞/踩（null = 已取消）。 */
  setRating: (key: FavKey, value: FavRating | null) => void
}

export function useFavorites(): FavoritesState {
  const [entries, setEntries] = useState<FavEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [ratings, setRatings] = useState<Record<FavKey, FavRating>>({})
  const [ratingsReady, setRatingsReady] = useState(false)
  const [gen, setGen] = useState(0)

  useEffect(() => {
    let alive = true
    setEntries(null)
    setError(null)
    fetchFavorites()
      .then(async (list) => {
        const own = list.map(toEntry)
        if (!alive) return
        setEntries(own)
        if (own.length === 0) {
          setRatings({})
          setRatingsReady(true)
          return
        }
        // 赞/踩初始态：收藏接口不带，只能从会话记录里取（老前端同款做法）。
        try {
          const sessions = await fetchSessions()
          if (!alive) return
          const map: Record<FavKey, FavRating> = {}
          for (const s of sessions) {
            for (const m of s.messages ?? []) {
              if (m.feedback) map[favKey(s.session_id, m.id)] = m.feedback
            }
          }
          setRatings(map)
          setRatingsReady(true)
        } catch {
          // 读不到就如实标未加载：拿空表冒充「没点过赞」是把未知画成事实。
          if (!alive) return
          setRatings({})
          setRatingsReady(false)
        }
      })
      .catch((e: unknown) => {
        if (!alive) return
        setError(e instanceof Error ? e.message : String(e))
        setEntries(null)
      })
    return () => {
      alive = false
    }
  }, [gen])

  const reload = useCallback(() => setGen((g) => g + 1), [])
  const drop = useCallback((key: FavKey) => {
    setEntries((list) => (list ? list.filter((e) => e.key !== key) : list))
  }, [])
  const setRating = useCallback((key: FavKey, value: FavRating | null) => {
    setRatings((prev) => {
      const next = { ...prev }
      if (value === null) delete next[key]
      else next[key] = value
      return next
    })
    setRatingsReady(true)
  }, [])

  return { entries, error, ratings, ratingsReady, reload, drop, setRating }
}

/* ── 四个动作（全部打后端既有端点，本文件不新造接口） ─────────────────── */

export async function unstarFav(entry: FavEntry): Promise<{ starred: boolean }> {
  return starMessage(entry.sid, entry.recId, false)
}

/** 撤销「取消收藏」：重新打星（可撤销比二次确认少打断一次操作）。 */
export async function restarFav(entry: FavEntry): Promise<{ starred: boolean }> {
  return starMessage(entry.sid, entry.recId, true)
}

/** 赞/踩。后端同值再点 = 取消，返回的是**最终状态**（null = 已取消）。 */
export async function rateFav(
  entry: FavEntry,
  rating: FavRating,
): Promise<'up' | 'down' | null> {
  return sendMessageFeedback(entry.sid, entry.recId, rating)
}

/**
 * 忘掉这道菜：后端只清该菜的 down 事件与口味信号。
 * removed=0 = 这道菜根本没有踩记录（成功但无变化），页面必须说「偏好没改变」。
 */
export async function forgetFav(entry: FavEntry): Promise<{ removed: number; dish: string }> {
  return forgetDish(entry.dish)
}
