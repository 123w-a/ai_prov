// sourceFormat：把知识库 chunk 元数据的 section 字段转成人类可读的出处后缀。
// 元数据形如 "《xxx指南》.pdf_p28"（文件名+页码），与 source 文件名重复冗长；
// 这里去重前缀，渲染成「第 28 页」；非该形态的 section 原样返回。
export function formatSourceSection(source: string, section?: string): string {
  if (!section) return ''
  const stem = source.replace(/\.[^.]+$/, '')
  const m = section.match(/^(.+?)_p(\d+)$/) || section.match(/^(.+?)_p(\d+)\b/)
  if (m && (m[1] === stem || m[1] === source)) return `第 ${m[2]} 页`
  return section
}