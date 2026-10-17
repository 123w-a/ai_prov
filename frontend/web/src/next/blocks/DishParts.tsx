import type { ReactNode } from 'react'
import type { Recipe } from '../data/viewModel.ts'
import { Icon, type IconName } from '../ui/Icon.tsx'
import { usableNote } from '../data/clean.ts'

/**
 * DishHero 的三枚零件（2026-10-10 从 DishHero.tsx 拆出：该文件 161 行，越过
 * blocks 单文件 150 行上限）。拆的位置按"判据自成一体"选：主图 / 档位词 /
 * 单条指标三者都不碰 vm，只吃 props，与报头部分的 vm 判据不共享状态。
 * 全文注释与实现逐字随迁，行为零变化。
 */

/**
 * 主菜图。
 *
 * 实测：23 条结构化答案里只有 9 条配了真实照片，14 条是 AI 补的示意图，
 * 且 22/23 的 image_note 自己就在承认这件事（「没找到合适的成品图；这道菜应是…」）。
 * 所以这里不做"显示 / 不显示"的分档——那等于把后端的不确定性做成产品等级；
 * 改为统一 16:9 裁切收进横幅（六宫格拼图会被裁成正常一条），
 * 而 note 一律如实落在图下方，不上图、不加浮层、不重复前缀。
 *
 * note 优先取顶层的：菜品级那个 20/23 是空或 "None"。
 */
export function DishFigure({ recipe, note }: { recipe: Recipe; note?: string }) {
  const image = recipe.image_url
  if (!image) return null
  const text = usableNote(note) || usableNote(recipe.image_note)
  // 有图就画：缺的是「图注这句话」，不该连图一起扣下。后端真图的 note 本来就是
  // 空串，旧规则（两个事实都缺 → 整图不渲染，图注还兜底写「AI 生成示意图」）
  // 会让真图要么消失、要么被标成 AI。AI 图没有自己的图注时才用这句默认说明。
  const caption = text || (recipe.image_ai_generated ? 'AI 生成示意图' : '')
  return (
    <figure className="dish-figure">
      <img src={image} alt={recipe.name} />
      {caption && <figcaption>{caption}</figcaption>}
    </figure>
  )
}

/** 一条指标：图标 + 标签 + 值。
 *
 * 设计稿三项各配一个细线图标（厨师帽=难度、叶子=营养、时钟=用时），
 * 三项之间**没有分隔线**，只有间距——所以原来那根发丝竖线撤掉了。
 * 图标在这里不是装饰：它让"难度／营养／用时"不用读字就能分辨。 */
export function Metric({ icon, label, children }: { icon: IconName; label: string; children: ReactNode }) {
  return (
    <span className="metric">
      <Icon name={icon} />
      <span className="metric-label">{label}</span>
      {children}
    </span>
  )
}
