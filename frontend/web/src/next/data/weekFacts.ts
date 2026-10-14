/**
 * 周报族的两个纯格式化函数（2026-10-15 从 `views/WeeklyView.tsx` 抽出）。
 *
 * 为什么要单独一个文件：成员详情抽屉（`blocks/` 层）与周报页（`views/` 层）都要
 * 把同一条 ISO 时间戳写成同一串字、把同一档灯色映射成同一个中文标签。而 blocks
 * 按 check-layers 的分层契约不能 import views——不搬出来就只有复制一份，
 * 复制之后两处的"时间怎么显示、认不出的档位怎么写"会各自漂移。
 *
 * 放 data 层是刻意的：这两个是**纯函数**（无状态、无 I/O、不碰 .tsx），
 * 可以像其它 data 模块一样被 node --test 直接测。放 ui 层反而会给它套上 JSX 邻域的暗示。
 */

/**
 * 逐餐明细的时刻：后端给 ISO（`2026-10-14T20:16:43`）→「10-14 20:16」。
 * 形状不匹配就**原样返回**——不猜、不补零、不改写成别的格式。
 */
export function mealStamp(ts: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(ts)
  return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}` : ts
}

/**
 * 营养灯档位 → 色标 + 中文判据。**同一张表**出两者，色点与文字不会分叉。
 *
 * 必须同时有文字：色点不单独传态（契约 §6 —— hero 点阵下方就挂着「绿 6 · 黄 1」），
 * 否则色盲用户和灰度截图都读不出档位。
 */
export const MEAL_LEVELS: Record<string, { c: string; label: string }> = {
  green: { c: 'g', label: '绿' },
  yellow: { c: 'y', label: '黄' },
  red: { c: 'r', label: '红' },
}

/**
 * 认不出的档位**原样显示后端给的值**，不按最常见的绿顶上——
 * 把未知当绿等于替后端下了一个它没下的结论。
 * 色标回落到 `g` 只是为了让点有个颜色画得出来，文字仍是真的那个值。
 */
export function mealLevel(level: string): { c: string; label: string } {
  return MEAL_LEVELS[level] ?? { c: 'g', label: level }
}
