import { unescapeText } from './favorites.ts'

/**
 * 历史会话侧栏的**纯函数与结构类型**（2026-10-01 B 批次③）。
 *
 * 为什么在 data 层：blocks/SessionRail 若直接 import 后端类型 types.ts，
 * check-layers 的 e 规则（blocks/views 不得读后端字段）会亮红——
 * 类型与推导都沉到这里，组件只拿结果渲染（同 waitLines 的拆分理由：
 * 取数与判断是数据的事，排版才是组件的事）。
 *
 * RailSession 是**结构类型**而非 import 自 types.ts：字段全部可选放宽，
 * 这样 api/client 的 Session（messages 必有、字段必填）天然可赋值过来，
 * 同时这个文件不携带任何后端类型引用。
 */

export interface RailSession {
  session_id: string
  title?: string
  created_at?: string
  messages?: { user_text?: string; answer?: string; time?: string }[]
}

/** 最后活跃时间的原始字符串：优先最后一条消息的 time，其次会话 created_at。 */
export function rawTime(s: RailSession): string {
  const msgs = s.messages ?? []
  const last = msgs.length > 0 ? msgs[msgs.length - 1].time : undefined
  return last || s.created_at || ''
}

/** 排序用真实时间戳（解析失败回 0 排到队尾）——不做「假年份」换算，跨年时序必须正确。 */
export function lastMs(s: RailSession): number {
  const raw = rawTime(s)
  if (!raw) return 0
  const t = new Date(raw).getTime()
  return Number.isNaN(t) ? 0 : t
}

/** 显示用 MM-DD HH:mm；没有可解析时间就给空串——时间是事实，猜一个才是错的。 */
export function fmtTime(s: RailSession): string {
  const t = lastMs(s)
  if (t === 0) return ''
  const d = new Date(t)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 标题：后端给什么用什么；没有就不编，退回会话 id 本体（用户认得出）。
 *  转义标题经 favorites 的同一份 unescapeText 兜底（后端双重转义 bug，
 *  实测列表里会出现 `\u7ed9\u6211\u63a8\u83` 这种乱码行）。 */
export function titleOf(s: RailSession): string {
  return unescapeText(s.title?.trim() || '') || s.session_id
}

/** 消息条数（供分组用；返回 number 而不是让人在调用处写 ?.length）。 */
export function msgCount(s: RailSession): number {
  const msgs = s.messages ?? []
  return msgs.length
}
