/**
 * 「出去吃」入口条的纯逻辑：查什么、以及**怎么如实标注这次查的是关键词还是坐标**。
 *
 * 抽出来的理由同 voiceText.ts：这几句话是这一块唯一的语义判断，而它们全都是
 * "不许假装"的具体落点——没定位却写"已按你的位置"就是编造。组件里只剩调用。
 *
 * 导航链接走 src/utils/nearbyNavigation.ts 的既有实现（导出方向不违规，
 * check-layers 的 a 条只判定 src/next/<layer>/ 之间的相对导入；
 * 实测 `node .tmp-probe-layers.mjs` 报 0 条），不在这里另抄一份坐标转换。
 */
import { buildAmapNavigationUrl } from '../../utils/nearbyNavigation.ts'
import type { NearbyRestaurant } from '../../types.ts'

/** 无结果时的原话。服务房有它自己的文案，这里不改那一处。 */
export const NEARBY_EMPTY_TEXT = '这一带没查到，换个词试试。'

/** 拿不到位置时的原话——**不假装已定位**。 */
export const GEO_UNAVAILABLE_NOTE = '未获取到位置，仍按关键词查询。'
export const GEO_UNSUPPORTED_NOTE = '这个浏览器没有定位能力，仍按关键词查询。'
/** 这次定位失败、但上次的位置还记得：说清楚，不装作刚刚定位成功。 */
export const GEO_KEEP_LAST_NOTE = '这次没拿到新位置，沿用上次定位。'

/**
 * 第一次展开用的关键词：输入框里已有的话。没有就给空串——
 * 空串的含义是"不带 query 查"，不是"用一个我编的中性词"。
 * （后端 /api/nearby 不带 query 时本就有默认结果，服务房一直这么用。）
 */
export function nearbyKeyword(draft: string): string {
  return draft.trim().replace(/\s+/g, ' ')
}

/**
 * 关键词查询的自述。
 *
 * 2026-10-21 用户反馈「每次查减脂餐都不记得我的位置」后改口径：**定位过就记住**，
 * 后续关键词查询沿用那组坐标（见 useNearby 的 origin 持久化）。
 * 因此这里必须区分两种情形，不能一律说"没有请求系统定位"：
 *   · 有坐标 → 「沿用上次定位」——这次确实没再弹定位，但查的是你上次的位置；
 *   · 没坐标 → 保持原话「没有请求系统定位」，绝不暗示已经定位。
 */
export function keywordNote(keyword: string, hasOrigin = false): string {
  const base =
    keyword.length > 0
      ? `按输入框里的关键词「${keyword}」查询`
      : '输入框还没有内容，按默认查询'
  return hasOrigin ? `${base}（沿用上次定位）` : `${base}（没有请求系统定位）`
}

/**
 * 定位拿到之后的自述。**只有真解析出地名才说话**：
 * 解析失败时返回空串，页面上该处不留任何句子。
 *
 * 2026-10-12 用户裁决：解析失败的原因文案（无论后端 warning 原话、
 * info/infocode、错误码，还是前端自己编的「请手动选择城市」）一律不上页面——
 * 那些是给排障的人看的，摆在用餐的人面前只有噪音，而界面上并没有"选城市"的入口。
 *
 * 注意：**能力没有被一起删掉**——没解析出地名时仍然带着坐标查附近
 * （useNearby 的 `load(keyword, gcj)` 在这条分支上照旧执行），
 * 只是这件事不再写在页面上。
 *
 * `_warning` 保留在签名里：调用点照旧把后端原因递进来，将来若要落到排障面板
 * （而不是用餐界面）不必改调用方；这里刻意不读它。
 */
export function geoSelfNote(resolved: boolean, label: string, _warning?: string): string {
  if (resolved) return `已按你的位置查询：${label}`
  return ''
}

/** 高德导航链接。坐标不全时返回 null（`buildAmapNavigationUrl` 的既有判据）。 */
export function navUrlFor(restaurant: NearbyRestaurant, origin: string): string | null {
  return buildAmapNavigationUrl(restaurant, origin, undefined, 'gcj02')
}
