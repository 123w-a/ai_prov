import { useCallback, useEffect, useState } from 'react'
import {
  fetchFavorites,
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
 *  3 不知道就说不知道：赞/踩的初始状态**跟着收藏列表一起来**了（后端
 *    `list_starred` 自 2026-10-14 起带 feedback 字段，见 storage/sessions.py），
 *    不再另拉整表会话建映射；字段为 null 就是「从没评过」。
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
  /** 那一轮的时间（后端 turned_at）。**不是**收藏动作的时间。空串 = 后端没给。 */
  turnedAt: string
  /** 收藏动作的时间（后端 favorited_at，2026-10-11 新增）。null = 后端没有这个值
   *  （旧收藏是在 star 还不落时间的那版收藏的）——页面必须显示「待补」，
   *  绝不许拿 turnedAt 或文件时间顶上：那会把"什么时候收的"说成假的。 */
  favoritedAt: string | null
  /** 当时那轮的结构化答案；null = 收藏接口没存下内容（多为纯散文轮）。 */
  detail: ChefAnswer | null
}

export function favKey(sid: string, recId: number): FavKey {
  return `${sid}:${recId}`
}

/* ── 卡片上那一行胶囊（2026-10-11 照 fav-expect-v4 稿补齐） ────────────────
 * 稿上是「高蛋白 / 低脂 / 晚餐」三枚。这里只放**能落地的真值**，两类：
 *   · 餐次   —— 由那一轮的真实时刻（turnedAt）推；
 *   · 营养灯 —— detail.health_lights 的原生 label（后端产出的是「钠/糖/脂肪」
 *               这类营养素名 + green/yellow/red 灯，见 types.ts）。
 * **不改写成语义等价的正向词**：把「脂肪·绿」写成「低脂」是替后端下结论，
 * SideRail.tsx 顶部有同款纪律（"绝不拿营养灯改写成成员判断"）。
 * 稿上的「高蛋白」在后端没有任何结构化字段（只在 opening 的散文里出现过），
 * 真实数据已够撑满这一行，所以**不补占位文案**——占位是"填不满"时才用的手段，
 * 这里填得满，补一个就成了编造。 */
export interface FavTag {
  text: string
  /** 营养灯档位。样式只认这个字段，不认文案（文案是后端的话，颜色才是语义）。 */
  level: 'green' | 'yellow' | 'red' | null
}

/** 餐次分段（闭区间，按分钟）。界限取常见三餐作息的中点，不替用户改作息。 */
const MEAL_BANDS: Array<[number, number, string]> = [
  [5 * 60, 10 * 60 + 29, '早餐'],
  [10 * 60 + 30, 14 * 60 + 59, '午餐'],
  [15 * 60, 17 * 60 + 29, '加餐'],
  [17 * 60 + 30, 21 * 60 + 29, '晚餐'],
]

/** 由那一轮的时刻推餐次；turnedAt 形如 `2026-08-26 13:35:50`。
 *  取不到时刻、或格式不是这个形状，就返回 null —— 宁可不显示这枚胶囊，
 *  也不猜一个餐次（与「不许把未知画成事实」同一条纪律）。 */
export function mealOf(turnedAt: string): string | null {
  const m = /^\d{4}-\d{2}-\d{2} (\d{2}):(\d{2})/.exec(turnedAt)
  if (!m) return null
  const min = Number(m[1]) * 60 + Number(m[2])
  const band = MEAL_BANDS.find(([a, b]) => min >= a && min <= b)
  return band ? band[2] : '夜宵'
}

/** 一条收藏的胶囊行。顺序照稿：餐次在前，营养灯按后端给的顺序在后。 */
export function tagsOf(entry: FavEntry): FavTag[] {
  const tags: FavTag[] = []
  const meal = mealOf(entry.turnedAt)
  if (meal) tags.push({ text: meal, level: null })
  for (const l of entry.detail?.health_lights ?? []) {
    const text = (l.label ?? '').trim()
    if (text) tags.push({ text, level: l.level })
  }
  return tags
}

/**
 * 后端 user_text / session_title / dish 实测存的是**双重转义**字符串：
 * HTTP 响应里写的是 `"\\u7ed9\\u6211"`，JSON.parse 解出来是字面量
 * `\u7ed9\u6211`（六个字符的序列），不是「给我」两个字——直接显示就是
 * 一串反斜杠乱码（2026-10-01 在收藏恢复详情的回声上实测到）。
 *
 * 只在文本里**确实出现形如 \uXXXX 的合法转义**时才逐个解码；普通中文、
 * 英文、含反斜杠的路径（\url 不是 4 位 hex，不匹配）都原样返回。
 * 解码失败也原样返回——宁可显示乱码，也不能把用户的话改坏。
 * 这是**前端兜底**：根因在后端写入时多转义了一次，不动 api/ 是红线。
 *
 * （2026-10-01 导出：历史会话侧栏 railTime.titleOf 是第二调用方——同一个
 * 后端 bug 的兜底只许有一份实现，不许在别处再抄一遍正则。）
 */
export function unescapeText(s: string): string {
  if (!s.includes('\\u')) return s
  // ① 合法的 4 位转义 → 解成字符
  const out = s.replace(/\\u([0-9a-fA-F]{4})/g, (_m, hex: string) =>
    String.fromCharCode(parseInt(hex, 16)),
  )
  // ② 后端按字符数截断标题会留下**半个序列**（实测 session_title 存成了
  //    …\u63a8\u83——原词「推荐」的 \u8350 被切成 \u83，只剩两位非法 hex）。
  //    不猜它原本是什么（猜 = 编造用户的话），用省略号标出「这里断了」。
  //    只匹配 1~3 位**真 hex**：\url 这种普通英文里的 \u 不受影响。
  return out.replace(/\\u[0-9a-fA-F]{1,3}(?![0-9a-fA-F])/g, '…')
}

function toEntry(item: FavoriteItem): FavEntry {
  const ask = unescapeText(item.user_text ?? '')
  return {
    key: favKey(item.sid, item.rec_id),
    sid: item.sid,
    recId: item.rec_id,
    // 纯散文轮后端把 dish 退化成原话截断——照它给的显示，不替它发明菜名。
    dish: unescapeText(item.dish) || '这顿没有菜名',
    from: unescapeText(item.session_title) || ask || '来源会话',
    ask,
    imageUrl: item.image_url ?? null,
    turnedAt: item.turned_at ?? '',
    // 收藏动作时间：后端给了字符串就用它；null / undefined 一律落 null（不兜成空串——
    // 空串会让"后端没给"和"给了空"看起来一样，页面只能一律写「待补」）。
    favoritedAt: item.favorited_at ?? null,
    // answer 是结构化对象（实测 favorites 接口给对象、sessions 接口给字符串，
    // 两者形态不同，以实测为准），内部字段实测是正常中文，不参与解码。
    detail: item.answer ?? null,
  }
}

export interface FavoritesState {
  /** null = 还在读；[] = 读到了、确实没有收藏。两者不许合并。 */
  entries: FavEntry[] | null
  error: string | null
  ratings: Record<FavKey, FavRating>
  /** 赞/踩初始态是否读到。false 时页面必须写明「状态未加载」。
   *  2026-10-14 起它与收藏列表**同一次响应**到达，正常路径恒为 true；
   *  字段保留是因为「有没有读到这个状态」这个区别本身仍然成立。 */
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
      .then((list) => {
        const own = list.map(toEntry)
        if (!alive) return
        setEntries(own)
        /* 赞/踩初始态（2026-10-14 改）：后端 list_starred 现在直接带 feedback，
         * 所以这里不再另拉一遍 GET /api/sessions —— 那一次往返要把**所有会话的
         * 全部消息**拉下来，只为查几个布尔值；收藏越多、会话越多越亏。
         *
         * ratingsReady 因此恒为 true：这份状态与收藏列表**同一次响应**到达，
         * 不存在"列表有了、状态还没来"的中间态。原先的 false 分支只挂在
         * 额外那次会话请求的失败上，现在没有那一次请求了。 */
        const map: Record<FavKey, FavRating> = {}
        for (const it of list) {
          if (it.feedback) map[favKey(it.sid, it.rec_id)] = it.feedback
        }
        setRatings(map)
        setRatingsReady(true)
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
