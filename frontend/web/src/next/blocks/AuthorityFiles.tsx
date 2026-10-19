import { useState } from 'react'
import { srcPage, srcTitle } from '../data/clean.ts'
import { fetchExcerpt, hasLocation, sectionLabel } from '../data/sources.ts'
import type { KbExcerpt, SourceFile } from '../data/sources.ts'
import { Icon } from '../ui/Icon.tsx'

/**
 * 结果页右栏第四段「权威文件」。
 *
 * 2026-10-19 由「本轮依据」升级而来（用户的裁决：结果页右栏不要"本周/冰箱"这类
 * 背景信息，要给**权威文件本身**，而且要能点开看原文、内容必须对应最后选定的那道菜）。
 *
 * 这一段回答的是"这道菜凭什么"：前半是本轮检索命中并真正引用的文件
 * （来源 = 日志里 nutrition_kb_search 的命中，不是模型随手写的书名），
 * 每一条都能点开看那一页的原文；后半是规则库给的确定性出处（健康判断的出处，
 * 只有文件名与页码，点不开——它本来就没有一段可展开的正文）。
 *
 * 三条硬纪律：
 *   1 **不补造**：取不到原文就说取不到，绝不为了"点开有内容"而编一段。
 *   2 **不冒充页码**：`_c12` 是切片序号，只显示"展开"，不写"第 12 页"。
 *   3 空段不渲染：两半都空时返回 null（调用方不额外兜底，右栏不留空框）。
 */

/** 确定性出处（护栏自带 source）：只有文件 + 页码，没有可展开的正文。 */
type Rule = { condition?: string; rule?: string; source: string }

export function AuthorityFiles({ refs, rules }: { refs: SourceFile[]; rules: Rule[] }) {
  if (refs.length === 0 && rules.length === 0) return null
  return (
    <section className="rail-sect" data-sect="evidence">
      <h2 className="rail-title">
        <Icon name="doc" />
        权威文件
      </h2>

      {refs.length > 0 && (
        <ul className="af-list">
          {refs.map((r, i) => (
            <FileRow key={`${r.source}-${r.section ?? ''}-${i}`} file={r} />
          ))}
        </ul>
      )}

      {rules.length > 0 && (
        <ul className="rail-sources">
          {rules.map((g, i) => (
            <li key={i}>
              <span className="src-cond">{g.condition}</span>
              {/* rule 是这一栏的主句：只有"条件 + 出处"时，两条同条件的护栏
                  （实测两条"孕期"）在右栏长得一模一样，读的人分不清哪条是
                  "水产要熟透"、哪条是"钠不超 2000mg"。 */}
              {g.rule && <span className="src-rule">{g.rule}</span>}
              <span className="src-name" title={g.source}>
                {srcTitle(g.source)}
              </span>
              {srcPage(g.source) && <span className="src-page">{srcPage(g.source)}</span>}
            </li>
          ))}
        </ul>
      )}

      <p className="rail-src-note">
        {refs.length > 0 ? '来自本轮检索到的食养指南，点开可核对原文。' : '出自国家食养指南，可逐条核对。'}
      </p>
    </section>
  )
}

/** 一条文件。点开才去取原文——右栏不发无关请求，收起也不留空壳。 */
function FileRow({ file }: { file: SourceFile }) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [excerpt, setExcerpt] = useState<KbExcerpt | null>(null)

  const snippet = (file.snippet ?? '').trim()
  const loc = sectionLabel(file.section)
  // 有片段或能定位到页码/章节才可展开；两者都没有时不留一个点了没反应的按钮。
  const canOpen = snippet !== '' || hasLocation(file)
  // 截断说明要印两个数：这里显示了多少、原文一共多少。先取成变量，
  // 不在 JSX 里写 `?.length`（分层门禁 g_降级契约 会把它判成"用存在性兜底值"）。
  const shownChars = excerpt && excerpt.text ? excerpt.text.length : 0
  const totalChars = excerpt && excerpt.total_chars ? excerpt.total_chars : 0

  const toggle = () => {
    const next = !open
    setOpen(next)
    if (!next || snippet || excerpt || loading) return
    setLoading(true)
    void fetchExcerpt(file).then((data) => {
      setExcerpt(data)
      setLoading(false)
    })
  }

  return (
    <li className="af-item" data-open={open ? '1' : '0'}>
      <button type="button" className="af-head" aria-expanded={open} disabled={!canOpen} onClick={toggle}>
        <span className="af-title">{srcTitle(file.source)}</span>
        {loc && <span className="af-loc">{loc}</span>}
        {file.category && <span className="af-tag">{file.category}</span>}
        {canOpen && <span className="af-caret">{open ? '收起' : '展开'}</span>}
      </button>

      {open && (
        <div className="af-body">
          {snippet && <p className="af-text">{snippet}</p>}
          {!snippet && loading && <p className="af-state">正在取原文…</p>}
          {!snippet && !loading && excerpt?.found && <p className="af-text">{excerpt.text}</p>}
          {!snippet && !loading && excerpt && !excerpt.found && (
            <p className="af-state">
              {excerpt.reason || '取不到这段原文'}。
              {loc ? `可按这份文件的${loc}自行核对。` : '可按文件名自行核对。'}
            </p>
          )}
          {!snippet && excerpt?.found && excerpt.precise === false && (
            <p className="af-state">没有定位到该页，以上是同一份文件的首段。</p>
          )}
          {!snippet && excerpt?.found && excerpt.truncated && (
            <p className="af-state">
              原文较长，这里截取前 {shownChars} 字（共 {totalChars} 字）。
            </p>
          )}
        </div>
      )}
    </li>
  )
}
