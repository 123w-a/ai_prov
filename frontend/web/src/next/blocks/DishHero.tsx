import type { ReactNode } from 'react'
import { DishFigure, Metric } from './DishParts.tsx'
import { isShown, type ResultVM } from '../data/viewModel.ts'
import { wordOf } from '../data/model.ts'

/**
 * 报头（全宽）+ 两列（图 ｜ 简介与营养表）。
 *
 * 2026-10-08 照预期图 result-expect.png 重排：
 *   旧结构是「图 285px ｜ 菜名+指标+简介」一格互挤，无图时第二列仍被预留，
 *   菜名栏实测塌到 371px、右侧留一条空槽——这是"不好看"的直接来源之一。
 *   新结构：报头（菜名+标签+指标）全宽，下方两列 grid；图缺席时 `.dish-cols.is-single`
 *   落回单列，简介与营养表吃满整宽，骨架与有图时对齐。
 *   营养表经 children 落在右列（预期图里它在图右侧、简介之下），不再通栏。
 *
 * 窄屏由 CSS 的 @media 落回竖排。菜品图的 16:9 裁切、图注如实等纪律不变（见下）。
 */
/** 配图入口（紧挨菜名右侧）：落点可见性上层已闸，这里只管画与回调。 */
export function DishHero({
  vm,
  onRequestImage,
  children,
}: {
  vm: ResultVM
  onRequestImage?: (dishName: string, revision: boolean) => void
  /** 右列下半段（预期图的营养表）。空着时右列只剩简介，不出空框。 */
  children?: ReactNode
}) {
  const lead = vm.lead
  // 与 DishFigure 的渲染条件同判据：它在无 image_url 时返回 null，
  // 若这里用「lead 在」当单列判据，缺图的轮次会把右列钉进 300px 的图位里。
  const hasFigure = Boolean(lead?.image_url)
  return (
    <div className="dish-top">
      <header className="dish-hero">
        <div className="dish-title">
          <h1 className="dish-name">{vm.lead?.name ?? '今天这一顿'}</h1>
          {/* 位置用户拍板（紧挨菜名右侧）；文案必须短：文字栏实测371，菜名306+长文案68+gap12=386会换行到菜名下方。请求句仍发"给「X」换一张图"（后端按它判换图）。 */}
          {onRequestImage && lead && (
            <button
              type="button"
              className="dish-imgbtn"
              onClick={() => onRequestImage(lead.name, Boolean(lead.image_url))}
            >
              {lead.image_url ? '换图' : '配图'}
            </button>
          )}
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
          {/* 这一格是**这一轮的生成耗时**（ResultView 传的是 run.elapsed），不是做菜用时：
              answer 里根本没有烹饪用时字段，原标签「用时」会被读成"这道菜要 1 分 24 秒"。
              改标「生成耗时」——数是真的，标签得说清它是谁的时间。
              空串 = 收藏恢复态（收藏没存那一刻的用时）⇒ 整格不画，绝不画「0 秒」冒充。 */}
          {vm.elapsed && (
            <Metric icon="clock" label="生成耗时">
              <b className="metric-value">{vm.elapsed}</b>
            </Metric>
          )}
        </div>
      </header>

      <div className={`dish-cols${hasFigure ? '' : ' is-single'}`}>
        {lead && <DishFigure recipe={lead} note={vm.imageNote} />}
        <div className="dish-side">
          {isShown(vm.intro) && <p className="dish-intro">{vm.intro.data}</p>}
          {children}
        </div>
      </div>
    </div>
  )
}

// 主菜图 / 档位词 / 单条指标三枚零件已拆到 ./DishParts.tsx（2026-10-10，规模上限）。


