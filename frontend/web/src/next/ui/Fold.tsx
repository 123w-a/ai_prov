import type { ReactNode } from 'react'

/**
 * 折叠区：默认收起的内容统一走这里，标题行不抢版面。
 *
 * 从 `Result.tsx` 升到 `ui/`（2026-09-27 第 5 步）：三个块
 * （Sources / ExtraRecipes / OpeningFold）都要用它，而分层规矩是
 * `app → views → blocks → ui`——`blocks/` 不得反向 import `views/`，
 * 所以共享容器只能住在更内层。
 *
 * 用原生 `<details>`：默认收起、可达、零 JS。
 */
export function Fold({
  summary,
  count,
  children,
  anchor,
}: {
  summary: string
  count?: number
  children: ReactNode
  anchor?: string
}) {
  return (
    <details className="fold" id={anchor}>
      <summary>
        {summary}
        {typeof count === 'number' && <span className="fold-count">{count}</span>}
      </summary>
      <div className="fold-body">{children}</div>
    </details>
  )
}
