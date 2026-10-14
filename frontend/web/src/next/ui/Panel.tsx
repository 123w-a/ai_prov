import type { ReactNode } from 'react'

/**
 * 原语：面板容器。
 *
 * 契约来自《前端架构方案》第四节，只有三个入口：
 *   variant  outline（底色抬一档 + 发丝线）| filled（填充面板色）
 *   title    可选；不给就**不渲染标题元素**，而不是渲染一个空标题
 *   children
 *
 * DOM 固定为 `section.p-panel[data-variant]`，状态一律走 data-* 属性（方案第三节）。
 *
 * 这里**不许**出现业务参数：不接 `health`、不接 `isWait`、不接 `level`/`color`。
 * 一旦原语知道"这是营养面板"，它就从原语退化成结果页的私有件，
 * 下一个想复用它的人只能抄一份——那正是这次重构要消掉的东西。
 */
export type PanelVariant = 'outline' | 'filled'

export function Panel({
  variant,
  title,
  children,
}: {
  variant: PanelVariant
  title?: string
  children: ReactNode
}) {
  return (
    <section className="p-panel" data-variant={variant}>
      {title !== undefined && <h3 className="p-panel-title">{title}</h3>}
      {children}
    </section>
  )
}
