import type { ResultVM } from '../data/viewModel.ts'
import { Fold } from '../ui/Fold.tsx'

/**
 * 本轮依据（方案第二节块清单里的 Sources）：正文里那份可展开的来源列表。
 *
 * 右栏只放简称（278px 塞不下文件名）。完整名称、章节页码与原文引用
 * 仍然可达——收进这个折叠里，否则把来源搬进右栏时就等于把 snippet 弄丢了。
 *
 * 降级在调用方（`sources.length > 0`）：块只收已就绪的列表。
 * `count` 只是标题上的「本轮依据 2」——它说的是"有几个来源"，
 * 不是"有多少字"，所以这种计数保留，而字数计数被刻意去掉了。
 *
 * 类型用索引访问 `ResultVM['sources']`（= `SourceRef[]`），
 * 避免为一个字段去追 `SourceRef` 的导出来源。
 */
export function Sources({ sources }: { sources: ResultVM['sources'] }) {
  return (
    <Fold summary="本轮依据" count={sources.length}>
      <ul className="sources">
        {sources.map((s, i) => (
          <li key={i}>
            <span className="src-name">{s.source}</span>
            {s.section && <span className="src-section">{s.section}</span>}
            {s.snippet && <q className="src-snippet">{s.snippet}</q>}
          </li>
        ))}
      </ul>
    </Fold>
  )
}
