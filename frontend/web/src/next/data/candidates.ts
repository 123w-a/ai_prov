/**
 * 候选清单解析：把「1. 菜名 —— 理由」形式的正文还原成结构化候选。
 *
 * ── 为什么在前端解析，而不是让后端推结构化候选 ──────────────────────
 * 后端**确实**产出过结构化候选（agent/graph.py 的 _candidate_list_payload），
 * 但 api/routes/chat_route.py:1561-1580 在推送前把它丢掉了：菜名落库
 * （set_message_candidates），payload 置 None，理由是"候选清单已经通过正文
 * token 流式展示，再推 answer 会显示原始 JSON 或触发错误的结构化卡片"。
 * 所以前端手上只有正文——那就按正文解析。
 *
 * ── 规则必须与后端逐字同源 ────────────────────────────────────────────
 * 下面每个正则都照抄 agent/graph.py:_extract_candidate_names（含菜名长度
 * 2..14、去重、上限 8）。这不是"参考"，是**契约**：后端拿同一份正文解析出的
 * 序号，就是它理解「第 2 个」时的依据。两边规则一旦分叉，用户点第 2 张卡，
 * 后端却去做了第 3 道菜——而且是静默的，没有任何一处会报错。
 *
 * 解析不出来就返回空数组，调用方退回纯文本（绝不猜、绝不补菜名）。
 */

export interface Candidate {
  /** 与正文一致的序号（用户点卡片时回给后端的就是它）。 */
  index: number
  name: string
  /** 推荐理由（2026-10-05 捞回）。
   *
   *  它**一直在正文里**：后端的候选规则明确要求「菜名后接「 —— 」再写一句约 30 字的
   *  推荐理由」，而这里的解析此前把分隔符之后的部分直接丢掉了（`split(...)[0]`）。
   *  于是预期图 cand-expect-v2.png 卡片上第二行那句（"原汁原味，鲜嫩不腻"）本来
   *  就送来了，却没被显示。
   *
   *  捞它不碰后端、不碰 prompt、不碰流式链路：正文一个字没改，只是不再把已经
   *  收到的半行信息丢掉。所以它是"候选卡补内容"里代价最小、且**真实性最高**的一项
   *  ——这句话是后端按规则要求模型针对**这道菜**写的，不是我们拿别的字段拼的。
   *
   *  空串表示这一行没写理由（后端允许"长短随性"，也允许个别行不写）：
   *  那就**不显示这一行**，不拿菜名或别的字段凑一句话出来。 */
  reason: string
}

/** 与后端 _extract_candidate_names 同一套切分：菜名与推荐理由之间的分隔符。 */
const REASON_SPLIT = /\s*(?:——|—|--|–|:|：|\||｜)\s*/

/** 与后端同一套编号行正则。 */
const NUMBERED_LINE = /^(?:[-*•]\s*)?(\d{1,2})\s*[.、)）．:：]\s*(.+)$/

/** 与后端同一套两端杂符清理。 */
const TRIM_CHARS = /^[*`「」『』"'\u201c\u201d]+|[*`「」『』"'\u201c\u201d]+$/g

export function parseCandidateList(body: string): Candidate[] {
  const out: Candidate[] = []
  const seen = new Set<string>()
  for (const raw of String(body ?? '').split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue
    const m = NUMBERED_LINE.exec(line)
    if (!m) continue
    const index = Number.parseInt(m[1], 10)
    if (!Number.isFinite(index)) continue
    // 分隔符**只切第一刀**：菜名在左、理由在右，理由整体保留。
    // 不能用 split 再拼回去——理由里自己带冒号或破折号是常事
    //（"清蒸 —— 汤锅一次搞定：蒸鱼同时烧水"），拼回去会把中间那个冒号吃掉。
    const cut = REASON_SPLIT.exec(m[2])
    let name = (cut ? m[2].slice(0, cut.index) : m[2]).trim()
    name = name.replace(TRIM_CHARS, '').trim()
    name = name.replace(/[，。！？；;,!.?;]+$/, '').trim()
    if (name.length < 2 || name.length > 14) continue
    if (seen.has(name)) continue
    seen.add(name)
    // 理由只做两端清理，**不改写、不截断**：它是要给用户读的原话。
    // 菜名长度那道闸门（2..14）不适用于理由——那是为了挡住"整句话被当成菜名"，
    // 理由本来就该是一句话。
    const reason = (cut ? m[2].slice(cut.index + cut[0].length) : '')
      .trim()
      .replace(TRIM_CHARS, '')
      .trim()
    out.push({ index, name, reason })
    if (out.length >= 8) break
  }
  return out
}

/**
 * 把编号候选行从正文里摘掉：卡片已经把这几行结构化显示了，
 * 正文里再印一遍就是同一份内容出现两次。
 *
 * 只摘**能被 parseCandidateList 认出来的那些行**（同一套判据），
 * 其余正文（开场白、说明、追问）一律原样保留——不用"看起来像列表"之类的
 * 宽松规则去删，那会把正文里正经的编号步骤也一起删掉。
 */
export function stripCandidateLines(body: string): string {
  const text = String(body ?? '')
  if (!text) return ''
  const keep: string[] = []
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    const m = line ? NUMBERED_LINE.exec(line) : null
    if (m) {
      // 与解析同判据：只有真能被当成候选（长度合法）的行才摘。
      const name = m[2]
        .split(REASON_SPLIT)[0]
        .trim()
        .replace(TRIM_CHARS, '')
        .replace(/[，。！？；;,!.?;]+$/, '')
        .trim()
      if (name.length >= 2 && name.length <= 14) continue
    }
    keep.push(raw)
  }
  return keep.join('\n').replace(/\n{3,}/g, '\n\n').trim()
}
