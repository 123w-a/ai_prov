import type { ReactNode } from 'react'

/**
 * 轻量 markdown 渲染：只处理 LLM 实际会输出的形态——
 *   ## / ### 标题、**加粗**、- 与 1) 列表、普通段落。
 * 返回 React 节点（不用 dangerouslySetInnerHTML），天然免疫 XSS，
 * 零第三方依赖；超出支持的语法按原样显示（不猜测不吞字）。
 */
const BOLD_RE = /\*\*([^*\n]+)\*\*/g

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let last = 0
  let match: RegExpExecArray | null
  BOLD_RE.lastIndex = 0
  while ((match = BOLD_RE.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index))
    nodes.push(
      <strong key={`${keyPrefix}-b-${match.index}`}>{match[1]}</strong>,
    )
    last = match.index + match[0].length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

export function renderRichText(source: string): ReactNode {
  const lines = (source ?? '').split('\n')
  const blocks: ReactNode[] = []
  let list: { ordered: boolean; items: ReactNode[] } | null = null

  const flushList = () => {
    if (!list) return
    const key = `l-${blocks.length}`
    blocks.push(
      list.ordered ? (
        <ol key={key}>{list.items}</ol>
      ) : (
        <ul key={key}>{list.items}</ul>
      ),
    )
    list = null
  }

  lines.forEach((raw, index) => {
    const line = raw.trimEnd()
    const heading = line.match(/^(#{1,3})\s+(.*)$/)
    const bullet = line.match(/^[-*]\s+(.*)$/)
    const ordered = line.match(/^(\d+[).、])\s+(.*)$/)

    if (heading) {
      flushList()
      blocks.push(
        <p key={`h-${index}`} className="rt-heading">
          {renderInline(heading[2], `h-${index}`)}
        </p>,
      )
      return
    }
    if (bullet || ordered) {
      const orderedFlag = Boolean(ordered)
      if (!list || list.ordered !== orderedFlag) {
        flushList()
        list = { ordered: orderedFlag, items: [] }
      }
      const content = ordered ? ordered![2] : bullet![1]
      list.items.push(
        <li key={`li-${index}`}>{renderInline(content, `li-${index}`)}</li>,
      )
      return
    }
    flushList()
    if (line.trim() === '') return
    blocks.push(
      <p key={`p-${index}`}>{renderInline(line, `p-${index}`)}</p>,
    )
  })
  flushList()
  return <>{blocks}</>
}
