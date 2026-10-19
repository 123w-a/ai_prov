import type { ChefAnswer, SessionMessage } from '../../types.ts'
import type { RunState } from './model.ts'

/**
 * 历史会话的**数据层**（2026-10-19）。
 *
 * 任务只有一个：把后端 `Session.messages` 的一条条落库记录，翻译成前端可以
 * 逐轮渲染的 `HistoryTurn`。翻译规则与旧 App.tsx 的 `sessionToMessages` +
 * `parseHistoryAnswer` **逐条对齐**（那套规则被旧前端的真实历史数据锤过），
 * 两处刻意比旧实现更严（不补造）：
 *   · `image_cancelled` 且无任何文字 = 不编一句「已取消配图……」占位，就没有回答块；
 *   · 空 `answer` = 不推空 assistant 气泡，就没有回答块。
 * 除此之外判据一条不变：JSON 解析成功、根是非数组对象、`recipes` 是数组，
 * 三者同时成立才算结构化答案；JSON 数组 / 空对象 / 坏 JSON 一律按原始纯文本。
 *
 * 为什么在 data 层：check-layers 的 e 规则禁止 blocks/views import 后端类型
 * 与裸读 `answer.*` 字段，所以「翻译」必须沉在这里，视图只消费 HistoryTurn。
 */

export interface HistoryTurn {
  /** React key。落库轮用 `rec-<recordId>`，本地未落点的轮用时间戳+签名兜底。 */
  key: string
  /** 后端记录号（`SessionMessage.id`）。收藏/评分配图轮都靠它定位。 */
  recordId?: number
  /** 用户这一轮的原话。没写字只有图时按旧实现补「上传了一张食材图片 / （图片）」。 */
  request: string
  /** 用户上传图。判据与旧实现一致：`user_image_url !== undefined ? user_image_url : image_url`。 */
  requestImageUrl: string | null
  /** 询问时刻（epoch ms）。缺失或非法统一 null——时间是事实，猜一个才是错的。 */
  askedAt: number | null
  /** 结构化答案包；非结构化轮为 null。 */
  answer: ChefAnswer | null
  /** 纯文本正文（散文轮 / image_cancelled 只剩文字的轮）。结构化轮为空串。 */
  body: string
  answerKind: 'none' | 'pending' | 'structured' | 'prose'
  /** 这一轮被用户取消（后端 `cancelled` / `answer === '__cancelled__'`）：
   *  只留用户问题与用户图，页面上标「已取消」——状态标注不是补造答案。 */
  cancelled?: boolean
  /** 这一轮没有跑完（失败 / 中断），当场如实标「未完成」，免得刷新前消失、
   *  刷新后又从后端 `__pending__` 冒出来，两边对不上。 */
  unfinished?: boolean
}

/** 结构化判据的唯一入口：JSON 成功 + 根是非数组对象 + recipes 是数组。 */
function parseHistoryAnswer(raw: string | undefined | null): ChefAnswer | null {
  if (!raw) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    if (
      parsed &&
      typeof parsed === 'object' &&
      !Array.isArray(parsed) &&
      Array.isArray((parsed as ChefAnswer).recipes)
    ) {
      return normalizeAnswer(parsed as Partial<ChefAnswer>)
    }
  } catch {
    // 坏 JSON / 纯文本：按原始文本处理（与旧 App.tsx 同判据）。
  }
  return null
}

/**
 * 字段兜底（照旧 App.tsx 的 normalizeChefAnswer 收窄）：历史数据出自多版后端，
 * recipes 里混过非对象项、数值字段缺过类型。判据不改，只是不让脏字段打进视图。
 */
function normalizeAnswer(raw: Partial<ChefAnswer>): ChefAnswer {
  const recipes = (Array.isArray(raw.recipes) ? raw.recipes : [])
    .filter((recipe): recipe is ChefAnswer['recipes'][number] => Boolean(recipe && typeof recipe === 'object'))
    .map((recipe) => ({
      ...recipe,
      name: typeof recipe.name === 'string' ? recipe.name : '',
      intro: typeof recipe.intro === 'string' ? recipe.intro : '',
      difficulty: Number.isFinite(Number(recipe.difficulty)) ? Number(recipe.difficulty) : 1,
      nutrition: Number.isFinite(Number(recipe.nutrition)) ? Number(recipe.nutrition) : 1,
      seasonings: (Array.isArray(recipe.seasonings) ? recipe.seasonings : [])
        .filter((item): item is { name: string; amount: string } => Boolean(item && typeof item === 'object'))
        .map((item) => ({
          name: typeof item.name === 'string' ? item.name : '',
          amount: typeof item.amount === 'string' ? item.amount : '',
        })),
      steps: (Array.isArray(recipe.steps) ? recipe.steps : [])
        .map((step) => (typeof step === 'string' ? step : String(step ?? '')))
        .filter(Boolean),
      image_url: typeof recipe.image_url === 'string' ? recipe.image_url : null,
      image_ai_generated: Boolean(recipe.image_ai_generated),
      image_note: typeof recipe.image_note === 'string' ? recipe.image_note : '',
    }))
  return {
    ...raw,
    opening: typeof raw.opening === 'string' ? raw.opening : '',
    recipes,
    image_url: typeof raw.image_url === 'string' ? raw.image_url : null,
    image_ai_generated: Boolean(raw.image_ai_generated),
    image_requested: Boolean(raw.image_requested),
    image_note: typeof raw.image_note === 'string' ? raw.image_note : '',
    chef_tip: typeof raw.chef_tip === 'string' ? raw.chef_tip : '',
    sources: Array.isArray(raw.sources) ? raw.sources : [],
    guardrails: Array.isArray(raw.guardrails) ? raw.guardrails : [],
    health_lights: Array.isArray(raw.health_lights) ? raw.health_lights : [],
    member_adjustments: Array.isArray(raw.member_adjustments)
      ? raw.member_adjustments.filter((item): item is string => typeof item === 'string')
      : [],
    dish_matrix: Array.isArray(raw.dish_matrix) ? raw.dish_matrix : [],
    primary_member: typeof raw.primary_member === 'string' ? raw.primary_member : '',
  } as ChefAnswer
}

/** 只剩文字的兜底：opening || chef_tip（结构化被丢弃后唯一还能说出口的部分）。 */
function answerTextOnly(answer: ChefAnswer | null): string {
  return ((answer?.opening || answer?.chef_tip) ?? '').trim()
}

/**
 * 时间解析：合法 Date 返回 epoch ms；缺失/非法统一 null。
 * 后端落过 `2026-10-19 19:30:00`（空格分隔）与 ISO 两种写法，`new Date` 两者都吃。
 */
export function parseHistoryTime(raw: string | undefined | null): number | null {
  if (!raw) return null
  const t = new Date(raw).getTime()
  return Number.isNaN(t) ? null : t
}

/** 整会话落库记录转历史轮列表（保持后端给的原始顺序，不排序）。 */
export function sessionMessagesToHistoryTurns(messages: SessionMessage[] | undefined): HistoryTurn[] {
  const out: HistoryTurn[] = []
  for (const record of messages ?? []) {
    const answer = parseHistoryAnswer(record.answer)
    const requestImage = record.user_image_url !== undefined ? record.user_image_url : record.image_url
    const base = {
      key: `rec-${record.id}`,
      recordId: record.id,
      request: record.user_text || (requestImage ? '上传了一张食材图片' : '（图片）'),
      requestImageUrl: requestImage ?? null,
      askedAt: parseHistoryTime(record.time),
    }

    // 取消轮：只留用户问题和用户图。问题下面不留空白气泡，给一行状态标注。
    if (record.cancelled || record.answer === '__cancelled__') {
      out.push({ ...base, answer: null, body: '', answerKind: 'none', cancelled: true })
      continue
    }

    // image_cancelled：结构化卡与图全部丢弃，只留 opening || chef_tip 或原纯文本；
    // 没有文字就不补造回答（旧实现会垫一句占位，这里刻意不垫）。
    if (record.image_cancelled) {
      const textOnly =
        answerTextOnly(answer) ||
        (record.answer && !record.answer.startsWith('{') && record.answer !== '__pending__' ? record.answer : '')
      out.push({
        ...base,
        answer: null,
        body: textOnly,
        answerKind: textOnly ? 'prose' : 'none',
      })
      continue
    }

    if (record.answer === '__pending__') {
      out.push({ ...base, answer: null, body: '', answerKind: 'pending' })
      continue
    }

    if (answer) {
      out.push({ ...base, answer, body: '', answerKind: 'structured' })
      continue
    }

    const raw = record.answer ?? ''
    // 纯空白视同空 answer：既不出回答块，也不把空白当正文留着。
    const text = raw.trim() ? raw : ''
    out.push({
      ...base,
      answer: null,
      body: text,
      answerKind: text ? 'prose' : 'none',
    })
  }
  return out
}

/** 未落点轮的兜底 key：时间戳 + 原话签名，保证同一数组里不撞。 */
function liveKey(run: RunState): string {
  const sig = `${run.askedAt ?? ''}-${run.request.slice(0, 24)}-${run.body.length}`
  return `run-${sig}`
}

/**
 * 一轮真实跑出来的 RunState 转历史轮（发送前「归档当前轮」用）。
 *
 * 三件事按事实写，不美化：
 *   · 结构化判据与落库轮同一条（answer 里有 recipes 数组才算卡）；
 *   · 失败/中断轮也归档，标 `unfinished`——它们当场被下一轮顶掉就消失，
 *     但后端还留着 `__pending__`/部分文本，刷新后会再冒出来，两边必须对得上；
 *   · 用户图：新前端实跑轮没有上传图入口，恒为 null（落库轮仍按后端字段取）。
 */
export function runToHistoryTurn(run: RunState): HistoryTurn {
  const structured = run.answer && Array.isArray(run.answer.recipes) ? normalizeAnswer(run.answer) : null
  const done = run.status === 'succeeded' && !run.orphaned
  return {
    key: run.origin ? `rec-${run.origin.recordId}` : liveKey(run),
    recordId: run.origin?.recordId,
    request: run.request,
    requestImageUrl: null,
    askedAt: run.askedAt ?? null,
    answer: structured,
    body: structured ? '' : run.body,
    answerKind: structured ? 'structured' : run.body.trim() ? 'prose' : 'none',
    unfinished: done ? undefined : true,
  }
}

/** 内容签名：规范化后的用户问题 + 正文/答案（无 recordId 时的去重兜底）。 */
function turnSignature(t: HistoryTurn): string {
  const norm = (s: string) => s.replace(/\s+/g, ' ').trim()
  return `${norm(t.request)} ${norm(t.body)} ${norm(JSON.stringify(t.answer ?? null))}`
}

/**
 * 追加历史轮（去重）。
 *
 *   · recordId 命中时**用新轮替换旧轮**（不是跳过）：图片轮把配图补到卡上之后，
 *     旧副本缺图，跳过会让「卡与图配套」在历史里破功；
 *   · 无 recordId 时按规范化内容兜底去重，命中即跳过（两条内容相同就是同一轮）；
 *   · 其余照原序追加到尾部。
 */
export function appendHistoryTurnUnique(turns: HistoryTurn[], turn: HistoryTurn): HistoryTurn[] {
  if (turn.recordId != null) {
    const idx = turns.findIndex((t) => t.recordId === turn.recordId)
    if (idx >= 0) {
      const next = turns.slice()
      next[idx] = turn
      return next
    }
    return [...turns, turn]
  }
  const sig = turnSignature(turn)
  if (turns.some((t) => t.recordId == null && turnSignature(t) === sig)) return turns
  return [...turns, turn]
}

/**
 * 图片轮收尾后按 recordId 把新 answer 回写进历史（否则翻上去看到的还是没图的旧卡）。
 * 没有命中就原样返回——回写是同步，不是追加。
 */
export function updateHistoryTurnAnswer(
  turns: HistoryTurn[],
  recordId: number,
  answer: ChefAnswer,
): HistoryTurn[] {
  let changed = false
  const next = turns.map((t) => {
    if (t.recordId !== recordId) return t
    changed = true
    return { ...t, answer: normalizeAnswer(answer), body: '', answerKind: 'structured' as const }
  })
  return changed ? next : turns
}
