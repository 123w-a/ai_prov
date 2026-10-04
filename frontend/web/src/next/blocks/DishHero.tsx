import type { ReactNode } from 'react'
import type { Recipe } from '../data/viewModel.ts'
import { Icon, type IconName } from '../ui/Icon.tsx'
import { usableNote } from '../data/clean.ts'
import { isShown, type ResultVM } from '../data/viewModel.ts'

/**
 * 首屏两栏：左图右文。
 *
 * 为什么不再居中竖排（2026-09-23 用户「图片放最上面，感觉留白太多了」）：
 * 菜品图受 .dish-figure img 的 max-height 限制，一张 1:1 方图只能渲染到 440px 宽，
 * 而正文栏是 706px —— 两侧各空 131px，一张居中的小图被 260px 空白包着，看着就是空。
 *
 * 但**不能简单把图放大**：22 张真实菜品图里 13 张是 1:1，撑满 706px 就是 706px 高，
 * 菜名会被推出首屏——违反「真实结果不能掉出首屏」这条对抗红线。
 * 所以唯一的解法是改构图：图占左栏（定宽、不裁切、高度天然有界），
 * 菜名/回声/指标/一句话介绍填右栏，侧向空白由文字吃掉。
 *
 * 顺带把首屏文字改成左对齐：两栏之后居中文字会失去阅读方向（像海报）。
 * 窄屏由 @media 落回竖排。
 */
export function DishHero({ vm }: { vm: ResultVM }) {
  return (
    <div className="dish-top">
      {vm.lead && <DishFigure recipe={vm.lead} note={vm.imageNote} />}

      <header className="dish-hero">
        <div className="dish-title">
          <h1 className="dish-name">{vm.lead?.name ?? '今晚这一顿'}</h1>
          {/* 口味标签（第 6 项）：紧挨菜名右侧。
              数据来自整理阶段对正文的提取，不是前端按菜名猜的——正文没说风格时整行不渲染，
              所以这里不做"兜底标签"。 */}
          {isShown(vm.dishTags) && (
            <ul className="dish-tags">
              {vm.dishTags.data.map((tag) => (
                <li key={tag} className="dish-tag">
                  {tag}
                </li>
              ))}
            </ul>
          )}
        </div>
        {/* 原话不再在这里回声。它已经升格成页首那个 AskEcho 框（第 4 项）：
            同一个要求在一屏里说两遍，第二遍只会显得像系统在自我解释。 */}
        <div className="dish-metrics">
          {isShown(vm.difficulty) && (
            <Metric icon="hat" label="难度">
              <b className="metric-value">{wordOf(vm.difficulty.data, '简单', '中等', '偏难')}</b>
            </Metric>
          )}
          {isShown(vm.nutrition) && (
            <Metric icon="leaf" label="营养">
              <b className="metric-value">{wordOf(vm.nutrition.data, '偏低', '适中', '高')}</b>
            </Metric>
          )}
          {/* 用时为空串 = 收藏恢复态（收藏没存真实用时）⇒ 这一格整块不画，
              绝不画「0 秒」冒充。难度/营养照旧来自 answer 里的真实字段。 */}
          {vm.elapsed && (
            <Metric icon="clock" label="用时">
              <b className="metric-value">{vm.elapsed}</b>
            </Metric>
          )}
        </div>
        {isShown(vm.intro) && <p className="dish-intro">{vm.intro.data}</p>}
      </header>
    </div>
  )
}

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
function DishFigure({ recipe, note }: { recipe: Recipe; note?: string }) {
  const image = recipe.image_url
  if (!image) return null
  const text = usableNote(note) || usableNote(recipe.image_note)
  const fallback = recipe.image_ai_generated || note != null
  if (!text && !fallback) return null
  return (
    <figure className="dish-figure">
      <img src={image} alt={recipe.name} />
      <figcaption>{text || 'AI 生成示意图'}</figcaption>
    </figure>
  )
}

/** 1–5 的数值 → 一个词。
 *
 * 2026-09-26 照设计稿改。设计稿那一行实测是
 * `[厨师帽] 难度 中等   [叶子] 营养 高   [时钟] 用时 25 分钟`——
 * **它写的本来就不是 3/5，是词。**
 *
 * 为什么这个改法站得住，而不是"为了像设计稿而丢数据"：
 * 后端给的 difficulty / nutrition 是 1–5 的字段，它**从未说明这 5 档的含义**。
 * 画成刻度条等于替后端声称一个它没说过的精度；写成词反而更贴近用户真正要判断的事
 * （这顿难不难做、够不够营养）。数值没丢：映射单调，1–2 一档、3 一档、4–5 一档，
 * 不新增档位、也不改后端给的值。
 *
 * 原先那版（5 段细线 + 数字）在 CSS 注释里的理由是"排印刻度旁边给数字才成立"，
 * 那个理由的前提是"刻度条要留"。设计稿给的是另一种做法，且更贴决策：改成词。 */
function wordOf(value: number, low: string, mid: string, high: string): string {
  const v = Math.max(1, Math.min(5, Math.round(value)))
  if (v <= 2) return low
  if (v === 3) return mid
  return high
}

/** 一条指标：图标 + 标签 + 值。
 *
 * 设计稿三项各配一个细线图标（厨师帽=难度、叶子=营养、时钟=用时），
 * 三项之间**没有分隔线**，只有间距——所以原来那根发丝竖线撤掉了。
 * 图标在这里不是装饰：它让"难度／营养／用时"不用读字就能分辨。 */
function Metric({ icon, label, children }: { icon: IconName; label: string; children: ReactNode }) {
  return (
    <span className="metric">
      <Icon name={icon} />
      <span className="metric-label">{label}</span>
      {children}
    </span>
  )
}

