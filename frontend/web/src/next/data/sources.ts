/**
 * 权威文件的「可核对」这一层。
 *
 * 结果页右栏原来只印一串文件名（有时带 `p28`），用户能看到的只有"系统说它引用过
 * 某某指南"。**文件名不是依据**——依据是那一页写了什么。所以这一层回答两件事：
 *   1 这一条到底是哪份文件的哪一页（section 是入库锚点 `文件名_p28`，不是人写的章节名）；
 *   2 点开时把**入库的那一段原文**取回来，取不到就如实说取不到。
 *
 * 三条不许破的规矩：
 *   · 页码只认 `/ _p(\d+)$ /`。`_c12` 是切片序号，把它当页码等于伪造页码。
 *   · 原文只来自后端 `/api/kb/excerpt`（按 source+anchor 精确取回），前端不拼、不猜、
 *     不拿相邻片段凑一段。
 *   · 取不到就说取不到（含"知识库里没有这份文件"与"锚点没对上，只能退到同文件首段"），
 *     绝不因为"这一栏空着不好看"而编一段话。
 */

/**
 * 一条权威来源。字段与后端 SourceRef 同形，但**在前端自己声明**：
 * blocks 层不许 import `../../types.ts`（分层门禁 e_读后端字段 实测会拦），
 * 而 viewModel 传下来的 SourceRef[] 在结构上正好满足这个形状。
 */
export type SourceFile = {
  source: string
  section?: string
  snippet?: string
  category?: string
}

/** 后端 `/api/kb/excerpt` 的返回。字段与 api/routes/kb_route.py 一一对应。 */
export type KbExcerpt = {
  found: boolean
  /** true = 文件名与锚点都对上（这一页的原文）；false = 只退到同文件首段。 */
  precise?: boolean
  source?: string
  section?: string
  page_start?: number | null
  page_end?: number | null
  chunk_id?: string
  text?: string
  truncated?: boolean
  total_chars?: number
  /** found=false 时的原因（后端原话，不加工）。 */
  reason?: string
}

/**
 * 从入库锚点里取页码：`成人高血压食养指南（2023年版）.pdf_p6` → 6。
 * 取不到（含 `_c12` 切片锚点、纯章节名）返回 null——绝不拿切片序号冒充页码。
 */
export function sectionPage(section?: string): number | null {
  const m = /_p(\d{1,4})\s*$/.exec((section ?? '').trim())
  if (!m) return null
  const n = Number(m[1])
  return Number.isFinite(n) && n > 0 ? n : null
}

/**
 * 这一条来源的「位置」文案。
 *   有页码 → `第 6 页`（用户翻开那份文件直接跳过去）；
 *   是其它章节名（不是入库锚点）→ 原样显示，那本来就是它给出的章节；
 *   只有切片序号（`_c12`）或空 → 空串，调用方整段不渲染。
 */
export function sectionLabel(section?: string): string {
  const raw = (section ?? '').trim()
  const page = sectionPage(raw)
  if (page !== null) return `第 ${page} 页`
  if (!raw || /_c\d+$/.test(raw)) return ''
  return raw
}

/** 这一条有没有「可展开」的位置线索：有页码、或有不是文件名的章节名。 */
export function hasLocation(ref: SourceFile): boolean {
  const label = sectionLabel(ref.section)
  return label !== '' && label !== ref.source
}

/** 按 (文件名, 章节锚点) 取回入库原文。失败一律返回 found=false + 原因，不抛给 UI。 */
export async function fetchExcerpt(ref: SourceFile, signal?: AbortSignal): Promise<KbExcerpt> {
  try {
    const resp = await fetch('/api/kb/excerpt', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ source: ref.source, section: ref.section ?? '' }),
      signal,
    })
    if (!resp.ok) {
      return { found: false, reason: `取原文失败（HTTP ${resp.status}）` }
    }
    return (await resp.json()) as KbExcerpt
  } catch (err) {
    // 中断是用户自己收起了这一条，不是错误——由调用方按 aborted 处理。
    if (err instanceof DOMException && err.name === 'AbortError') return { found: false, reason: '已取消' }
    return { found: false, reason: '取原文失败（网络不可用）' }
  }
}
