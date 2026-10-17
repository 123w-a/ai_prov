import type { ChefAnswer, StreamStage } from '../../types.ts'

/**
 * 「今晚这一顿」这一条闭环的数据模型。
 *
 * 设计前提全部来自实测：
 *   - 后端整轮只发 5 个阶段事件，取值只有 searching / auditing / structuring
 *   - 阶段会重复：searching → auditing → searching → auditing → structuring（循环 Agent）
 *   - 存在 106s 的完全沉默区，其间只有心跳
 *   - 结构化结果时有时无：历史 35 条里 22 条有卡片、13 条是纯散文
 *
 * 耗时不要引用单一数字：2026-09-21 那次是 354.7s，之后复测过若干轮落在 52–111s。
 * 这个区间差了三倍多，说明**整轮时长不稳定**——正因为不稳定，
 * 页面上任何"还剩多少"的说法都必然是编的，这才是"不许出现进度"的真正理由。
 *
 * 因此本模型只承认三样真实信号：**阶段事件、心跳、正文字符**。
 * 任何不在这个集合里的"进度"都不许出现在界面上。
 */

/** 后端 stage 枚举的中文说法。只翻译，不发明。七种一个都不能漏——用 Record 逼穷尽。 */
const STAGE_LABEL: Record<StreamStage, string> = {
  thinking: '思考',
  writing: '撰写',
  searching: '查证',
  auditing: '核对',
  generating_image: '配图',
  structuring: '整理',
  switching_model: '切换模型',
}

export function stageLabel(stage: StreamStage): string {
  return STAGE_LABEL[stage]
}

/**
 * 阶段的人话说法 —— 给等待页「当前工位」那一行用。
 *
 * 纪律与 STAGE_LABEL 完全一致：**只把后端真的发过来的阶段名展开成一句人话，不发明步骤**。
 * 具体边界（lave 2026-09-23 两席都点名）：
 *   · 不许写"正在切洋葱""正在煎第一面"——后端从未报告过任何具体动作；
 *   · 不许写"还差两道菜""查证 2/2"——后端从不给总数，我们也不知道；
 *   · 每一句的主语都只能是"该阶段的字面含义 + 这个产品本来就只处理的那个对象
 *     （这顿饭／食材／家庭偏好／做法）"，后者是用户输入时就已知的，不是推断出来的。
 *
 * 这里存的是**名词短语**（不含时态），时态由下面两个访问器加：
 *   · stageSentence → 「正在核对食材与家庭偏好」（新鲜的时候用现在时）
 *   · stagePhrase   → 「核对食材与家庭偏好」（陈了之后要写成「上一步是…」，
 *                      直接拿 stageSentence 拼会造出「上一步是**正在**核对…」这种病句）
 *
 * 七种一个都不能漏 —— 用 Record 逼穷尽，将来后端加阶段会直接编译不过。
 */
const STAGE_PHRASE: Record<StreamStage, string> = {
  thinking: '想这顿饭怎么安排',
  writing: '把做法写清楚',
  searching: '查证这顿饭的做法',
  auditing: '核对食材与家庭偏好',
  generating_image: '准备菜品呈现',
  structuring: '整理成一份能照着做的方案',
  switching_model: '切换处理方式',
}

export function stageSentence(stage: StreamStage): string {
  return `正在${STAGE_PHRASE[stage]}`
}

export function stagePhrase(stage: StreamStage): string {
  return STAGE_PHRASE[stage]
}

/** 到达过的真实阶段事件。`at` 是距发送的毫秒数。 */
export interface TraceEvent {
  at: number
  stage: StreamStage
  /** 该阶段在整轮中的第几次出现（从 1 开始）。重复出现是 Agent 的工作方式，不是故障。 */
  nth: number
}

export type RunStatus = 'idle' | 'running' | 'succeeded' | 'failed'

/** 结果的两种真实形态。它们必须都被当作正常结果，不许把后者画成降级。 */
export type ResultForm = 'structured' | 'prose'

export interface RunState {
  status: RunStatus
  /**
   * true = 这一轮没有跑完（页面离开，或用户主动取消）。
   * 已经记录下来的过程全都是真的，所以照常显示；但必须说清它没有跑完，
   * 绝不能把它恢复成"已完成"——那比丢掉它更糟。
   */
  orphaned?: boolean
  /**
   * 中断的原因。这两个必须分开说，因为对用户的意思完全不同：
   *   left      = 页面被刷新/关掉，连接被动断开，用户可能根本不知道
   *   cancelled = 用户自己按了取消，他知道发生了什么，界面不该反过来说"你离开了"
   * 把这两件事写成同一句话，就是在替用户编造他的行为。
   */
  orphanCause?: 'left' | 'cancelled'
  /** 距发送的真实毫秒数，由本地时钟推进；心跳只用来确认后端还活着。 */
  elapsed: number
  /**
   * 用户这一轮的原话。
   * 结果页要拿它做"回声"（按你「…」来配）——不回声，这页就跟"今晚"没关系，
   * 读起来像系统随便生成的一篇文档。它必须来自用户本人，不许由正文反推。
   */
  request: string
  /**
   * 用户按下发送的时刻（epoch ms）。结果页用它标「记录于 今天 10:23」。
   *
   * 为什么必须单独存一个绝对时刻：elapsed 说的是"距发送过了多久"，它不锚定时间。
   * 页面停留十分钟后 `Date.now() - elapsed` 已经不再是发送时刻了。
   *
   * 为什么允许 null：从后端会话或收藏恢复的那些轮，本地没有当时的钟点。
   * 那时**不显示时间**（时间是事实，猜一个才是错的），但原话照旧显示。
   */
  askedAt?: number | null
  events: TraceEvent[]
  /** 每个心跳到达的时刻，用来判断连接是否可疑。它不再驱动任何动效。 */
  heartbeats: number[]
  currentStage: StreamStage | null
  body: string
  answer: ChefAnswer | null
  /**
   * true = 这不是一轮真实跑出来的结果，而是从收藏恢复出来的**只读详情**。
   * 收藏接口只存了当时的 answer，没有 elapsed / events / body ——
   * 恢复态必须藏掉「真实过程」的账目与用时：列出来只能是 0 秒或编的数字。
   */
  restored?: boolean
  /**
   * 这一轮在后端会话里的落点，由 finish 事件权威给出；★收藏 / 赞 / 踩都靠它定位记录。
   * 只有收到过 finish 的轮才有它——失败/中断轮天然没有，动作条据此整体隐藏（宁可不画，
   * 也不能把动作写到错误的记录上）。
   */
  origin?: { sessionId: string; recordId: number }
  /**
   * true = 这份 run 是刷新后从 localStorage 存档恢复的，不是刚跑完的：
   * 此时★与赞踩的真实状态未知（期间可能在收藏房改过），必须回查会话才敢显示。
   * 刚跑完的新轮不带它——新记录必然是未收藏/未评分，不用回查。
   */
  archive?: boolean
  /** 正文第一个字符到达的时刻；用来把"开头那段沉默"标出来。 */
  firstTokenAt: number | null
  error: string | null
  httpStatus?: number
}

export const EMPTY_RUN: RunState = {
  status: 'idle',
  elapsed: 0,
  request: '',
  askedAt: null,
  events: [],
  heartbeats: [],
  currentStage: null,
  body: '',
  answer: null,
  firstTokenAt: null,
  error: null,
}

/** 后端心跳实测约每 5s 一次。超过这个倍数没心跳，就必须怀疑连接，不能假装一切正常。 */
export const HEARTBEAT_INTERVAL_MS = 5000
export const STALE_AFTER_MS = HEARTBEAT_INTERVAL_MS * 4

export function isStale(run: RunState): boolean {
  if (run.status !== 'running') return false
  // 已经确认中断的轮次不算"可疑"——它的事实是确定的，由中断横幅负责说明。
  if (run.orphaned) return false
  const last = run.heartbeats.length > 0 ? run.heartbeats[run.heartbeats.length - 1] : 0
  return run.elapsed - last > STALE_AFTER_MS
}

/** 距上一次真实心跳过了多久；没有过心跳就从发送算起。 */
export function sinceLastHeartbeat(run: RunState): number {
  const last = run.heartbeats.length > 0 ? run.heartbeats[run.heartbeats.length - 1] : 0
  return Math.max(0, run.elapsed - last)
}

/** mm:ss */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

/** 秒表用的大字号读法：1 分 46 秒 */
export function formatSpoken(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  if (m === 0) return `${s} 秒`
  return `${m} 分 ${s} 秒`
}

/**
 * 需求记录时刻的读法：「今天 10:23」/「昨天 21:05」/「10-03 08:12」。
 *
 * `now` 由调用方传入、不在这里读时钟：这样它是**纯函数**，node --test 能直接
 * 喂跨天/跨月/跨年三种样本——这三种边界靠肉眼看页面是测不出来的。
 *
 * 只比到「日」一级，判据是**本地日历日相同**而不是"24 小时以内"：
 * 凌晨 1 点看昨晚 23 点的记录，该说「昨天」而不是「今天」。
 */
export function formatStamp(ms: number, now: number): string {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`
  const dayOf = (t: number) => {
    const x = new Date(t)
    return `${x.getFullYear()}-${x.getMonth()}-${x.getDate()}`
  }
  const y = new Date(now)
  y.setDate(y.getDate() - 1)
  if (dayOf(ms) === dayOf(now)) return `今天 ${hm}`
  if (dayOf(ms) === dayOf(y.getTime())) return `昨天 ${hm}`
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${hm}`
}

/**
 * 本轮已经进行了几轮。用真实出现过的 searching 次数计——这是唯一可数的循环证据。
 */
export function rounds(run: RunState): number {
  return Math.max(1, run.events.filter((e) => e.stage === 'searching').length)
}

/**
 * 从结果里判断这一轮的真实形态。判据只有一条：后端有没有送来带菜品的结构化包。
 * 没有就如实说是文字方案——**不许为了好看去伪造结构化内容**。
 */
export function resultForm(answer: ChefAnswer | null): ResultForm {
  const recipes = answer?.recipes ?? []
  return recipes.length > 0 ? 'structured' : 'prose'
}



