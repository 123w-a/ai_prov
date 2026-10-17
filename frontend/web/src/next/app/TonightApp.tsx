import { useCallback, useEffect, useRef, useState } from 'react'
import { createSession, fetchSessions } from '../../api/client.ts'
import { useHousehold } from '../data/household.ts'
import {
  candidateView,
  familyRows,
  fridgeView,
  pendingCandidate,
  useFirstScreen,
  weekView,
} from '../data/firstScreen.ts'
import { streamChat, ChatStreamError } from '../data/chatStream.ts'
import type { ChefAnswer, Session, StreamStage } from '../../types.ts'
import type { RunState, TraceEvent } from '../data/model.ts'
import { EMPTY_RUN } from '../data/model.ts'
import { WaitCard } from '../blocks/WaitCard.tsx'
import { SessionRail } from '../blocks/SessionRail.tsx'
import { RunResult } from '../views/ResultView.tsx'
import { AskView } from '../views/AskView.tsx'

/**
 * 「今晚这一顿」——新前端的唯一主面。
 *
 * 这一版只重建这一条纵向闭环，其余能力（周报、收藏、服务、家人档案）本轮不进主面。
 *
 * 与后端真实行为对齐的三条：
 *   1. 整轮真实耗时约 6 分钟，其中前 106 秒没有任何正文，阶段事件整轮只有 5 个 → 等待态如实呈现
 *      （不要"进度条"，也不要时间卷带：5 个事件画不成连续进展，用进展叙事就是编造）
 *   2. 阶段名会重复（循环 Agent）→ 追加记录，不覆盖、不重置
 *   3. 结构化卡片时有时无（历史 22/35）→ 两种结局平级呈现，不把散文画成失败
 */

const SESSION_KEY = 'xiaoshan.sessionId'
const RUN_KEY = 'xiaoshan.lastRun'

/* 2026-10-12 用户裁决：**不再预填任何固定问句**。
 *
 * 被删掉的那句固定问句（原话不再于本仓任何位置复述，避免 grep 误判为未删）
 * 自身就是上一版留下的同一个错误的下一形态：上一版编的是不存在的家人（爸爸/高血压），
 * 这一版改为把**真实家人的姓名与身体状况**（小美 / 怀孕 / 我 / 增肌）硬编码进输入框默认值——
 * 名字来自档案、句子却来自代码，两者一旦分叉，页面就会替用户说出他没说过、系统也不知道的话。
 * 用户要求这处固定问句不再出现，故整条默认话术链路一起退场：输入框一律从空开始，
 * 恢复存档 / 切换会话 / 从收藏房回来时，**只用真实存下来的那句原话**；没有就留空。
 * 「试试这样问」的四张示例卡不受影响——它们仍是用户点击后才填进去的引导（四个不同起点，
 * 不含人名、不含病名），不是任何人的默认话术。
 */

/**
 * 会话只建一次：刷新要接回原来那条。
 * 模块级 promise 兜住 React StrictMode 在开发模式下的双调用——
 * 否则一次加载会建出两个会话（实测留下过 3 个空会话）。
 */
let sessionPromise: Promise<string> | null = null

function ensureSession(): Promise<string> {
  const cached = localStorage.getItem(SESSION_KEY)
  if (cached) return Promise.resolve(cached)
  if (sessionPromise === null) {
    sessionPromise = createSession()
      .then((s) => {
        localStorage.setItem(SESSION_KEY, s.session_id)
        return s.session_id
      })
      .catch((err: unknown) => {
        sessionPromise = null
        throw err
      })
  }
  return sessionPromise
}

/** 存档放 localStorage：刷新、关标签页、重启浏览器都不该丢。 */
function saveRun(run: RunState, text: string) {
  try {
    localStorage.setItem(RUN_KEY, JSON.stringify({ run, text }))
  } catch {
    // 容量或隐私模式失败不影响主流程
  }
}

function loadRun(): { run: RunState; text: string } | null {
  try {
    const raw = localStorage.getItem(RUN_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { run?: RunState; text?: string }
    const r = parsed.run
    if (!r) return null
    if (r.status === 'succeeded') {
      // 打 archive 标记：从存档恢复的结果页不知道★/赞踩期间有没有被改过（比如
      // 刷新前刚在收藏房取消了收藏），必须回查会话才敢显示真实状态。
      return { run: { ...r, archive: true }, text: parsed.text ?? '' }
    }
    if (r.status === 'running') {
      // 页面在跑的过程中被关掉或刷新了：流已经断了，但已经记下来的过程全都是真的。
      // 照常显示，只是必须标明它没有跑完——把它恢复成"已完成"比丢掉它更糟。
      // 用 localstorage 里真实存下来的 orphanCause，别在这里猜用户做过什么。
      return {
        run: { ...r, orphaned: true, orphanCause: r.orphanCause ?? 'left' },
        text: parsed.text ?? '',
      }
    }
    return null
  } catch {
    return null
  }
}

/** 上一顿选了谁（2026-10-15）：只存"选了谁"，不存"确认过没有"。 */
const CREW_KEY = 'dsh.tonight.crew'

/**
 * 读上次的用餐人，作为这一顿的**默认勾选**。
 *
 * 这条是为了不折磨常见情况：家里两个人吃、或者还是昨天那几位，不该每次进来
 * 都重新点一遍。注意它只是**预勾选**，不是"已确认"——用户仍然要按确认键，
 * 那一步是「先定这一顿谁吃，再写想吃什么」这个顺序的落点，不能被省掉。
 *
 * 读不出来（首次进来 / 存储被禁 / 手改过）一律返回空数组：默认勾错人比默认
 * 不勾更糟——前者会让用户在没看清的情况下把饭记到别人头上。
 */
function loadCrew(): string[] {
  try {
    const raw = window.localStorage.getItem(CREW_KEY)
    const arr: unknown = raw ? JSON.parse(raw) : null
    if (!Array.isArray(arr)) return []
    return arr.filter((x): x is string => typeof x === 'string' && x.trim().length > 0)
  } catch {
    return []
  }
}

/** 记下这一顿的用餐人，供下一顿预勾选。存不进去只影响下次的默认值，不影响这一顿。 */
function saveCrew(names: string[]): void {
  try {
    window.localStorage.setItem(CREW_KEY, JSON.stringify(names))
  } catch {
    /* 隐私模式 / 配额满：静默。这里没有任何需要告诉用户的事，
       因为这一顿的归属已经随请求发出去了，没丢。 */
  }
}

export default function TonightApp() {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [sessionError, setSessionError] = useState<string | null>(null)
  // 输入框一律从空开始（2026-10-12 用户裁决，理由见本文件顶部那段注释）：
  // 有真实存档就用存档里那句话，没有就留空——不再有代码自带的默认问句。
  const [text, setText] = useState(() => loadRun()?.text ?? '')
  const [run, setRun] = useState<RunState>(() => loadRun()?.run ?? EMPTY_RUN)
  // 历史会话侧栏（B 批次③，lave 方案 A）：列表只读 fetchSessions()；
  // 面板形态见 blocks/SessionRail.tsx 的头注释（常驻按钮 + 左浮层，不推挤主区）。
  const [railOpen, setRailOpen] = useState(false)
  const [railSessions, setRailSessions] = useState<Session[]>([])
  const [railLoading, setRailLoading] = useState(false)
  const [railError, setRailError] = useState<string | null>(null)
  const [railNotice, setRailNotice] = useState<string | null>(null)
  /* 本餐用餐人（2026-10-15）。刻意拆成两个 state，它们的生命周期不一样：
     · `crewPicked` = 选了谁 → 持久化，下一顿默认沿用（loadCrew/saveCrew）；
     · `crewConfirmed` = 这一步确认过没有 → **只在内存里**，每次进首屏都重来。
     用户要的是「确认好这一顿是谁吃才开始写」，所以"已确认"不该跨刷新活着：
     刷新之后又是一顿新的饭，该重新定一次。 */
  const [crewPicked, setCrewPicked] = useState<string[]>(() => loadCrew())
  const [crewConfirmed, setCrewConfirmed] = useState(false)
  const startedAt = useRef(0)
  const abortRef = useRef<AbortController | null>(null)
  /** 定时存档用的最新值引用，避免把存档 effect 挂到每一批 token 上。 */
  const runRef = useRef(run)
  const textRef = useRef(text)
  const crewRef = useRef(crewPicked)
  runRef.current = run
  textRef.current = text
  crewRef.current = crewPicked

  const { family, fridge } = useHousehold()
  // 首屏四块的另两块（本周 + 待确认）。家庭与冰箱仍走 useHousehold，
  // 与等待页共用同一份读取；推导（纯函数）在下面一次算好。
  // readError：这两块读失败的原因（useFirstScreen 会退避重读直到读到），
  // 往右栏透传——不能让"后端在重启"和"本周真没有记录"在页面上长得一样。
  const { weekly, candidates, readError: firstScreenError } = useFirstScreen()
  const fam = familyRows(family)
  const week = weekView(weekly)
  const cand = candidateView(pendingCandidate(candidates))
  const fridgeBox = fridgeView(fridge)

  useEffect(() => {
    let alive = true
    ensureSession()
      .then((id) => {
        if (alive) setSessionId(id)
      })
      .catch((err: unknown) => {
        if (alive) setSessionError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      alive = false
    }
  }, [])

  /** 会话列表只读拉取（打开面板时也会再拉一次，保证列表不是进站时的旧快照）。 */
  const loadSessions = useCallback(() => {
    setRailLoading(true)
    setRailError(null)
    fetchSessions()
      .then((list) => setRailSessions(list))
      .catch((err: unknown) =>
        setRailError(`会话列表读不到（不影响正在做的事）：${err instanceof Error ? err.message : String(err)}`),
      )
      .finally(() => setRailLoading(false))
  }, [])

  useEffect(() => {
    loadSessions()
  }, [loadSessions])

  // 侧栏 Escape 关闭（与 Shell 抽屉同一约定：只在打开时挂监听，不打扰正常键入）。
  useEffect(() => {
    if (!railOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setRailOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [railOpen])

  /**
   * 本地时钟只负责"已经过了多久"。它不猜测进度，也不推断剩余。
   * 心跳到达时会用它自己的 elapsed 校准，两者不该差太多。
   * 中断的轮次不走时钟——它的时间已经停在断掉的那一刻了。
   */
  useEffect(() => {
    if (run.status !== 'running' || run.orphaned) return
    const t = window.setInterval(() => {
      setRun((prev) =>
        prev.status === 'running' && !prev.orphaned
          ? { ...prev, elapsed: Date.now() - startedAt.current }
          : prev,
      )
    }, 1000)
    return () => window.clearInterval(t)
  }, [run.status, run.orphaned])

  /**
   * 跑的过程中持续存档（每 2 秒一次，有界）。
   * 目的是让"运行到一半页面被关掉"这件事留下痕迹：已经真实发生过的阶段与心跳
   * 不该因为一次刷新就凭空消失。用 ref 取最新值，避免把 effect 挂到每一批 token 上。
   */
  useEffect(() => {
    if (run.status !== 'running' || run.orphaned) return
    const t = window.setInterval(() => saveRun(runRef.current, textRef.current), 2000)
    return () => window.clearInterval(t)
  }, [run.status, run.orphaned])

  const send = useCallback(async (override?: unknown, opts?: { image?: { dishName: string; index: number; revision: boolean } }) => {
    if (!sessionId || run.status === 'running') return
    // override 供候选卡「选这个方案」直接发送。它**不能**走 setText 再 send：
    // 同一帧里 text 还是旧值，发出去的会是上一轮那句话。
    // 形参收成 unknown 是因为 send 也直接绑在按钮 onClick 上，那条路径会传进一个
    // MouseEvent——所以必须先判类型再用，否则事件对象会被当成消息正文发出去。
    const message = (typeof override === 'string' ? override : text).trim()
    if (!message) return

    // opts.image = 菜名右侧「配图 / 换一张图」发的图片轮：它不是新一轮对话，
    // 是给当前这张卡补/换配图——卡片身份（谁问的/何时问的/答是什么/落在哪条记录）
    // 必须原样活着，起跑就擦成 EMPTY_RUN 会让用户点完按钮回来时卡片凭空消失。
    const img = opts?.image
    const base = runRef.current
    const imgBase = img && base.status === 'succeeded' && base.origin && base.answer ? base : null
    const target = imgBase?.origin      // 钉卡片用的落点；没有它就不该发这一轮
    if (img && !imgBase) return

    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac

    startedAt.current = Date.now()
    // askedAt 用 startedAt.current（不是再读一次钟）：它就摆在上一行，
    // 两处各读一次 Date.now() 会让"发送时刻"和"计时起点"差出几毫秒。
    // 图片轮反过来：原话与询问时刻保留卡的（AskEcho 那句「记录于」说的是这道菜
    // 被问出来的时刻，不是按下配图按钮的时刻）；过程面从零重新起。
    setRun({
      ...EMPTY_RUN,
      status: 'running',
      request: imgBase?.request ?? message,
      askedAt: imgBase ? (imgBase.askedAt ?? null) : startedAt.current,
      answer: imgBase?.answer ?? null,
      origin: imgBase?.origin,
    })

    let sawFinish = false
    // 图片轮：answer 从卡的原答案起（image 事件就贴在它上面），origin 锁死为卡的
    // 落点——普通轮里它是 finish 的唯一权威，但图片轮 finish 给的编号是那条确认话
    // 新记录的号，采纳它会把★/评分/动作条引到一条只有确认话的空记录上。
    let answer: ChefAnswer | null = imgBase?.answer ?? null
    let origin: { sessionId: string; recordId: number } | undefined = imgBase?.origin
    let firstTokenAt: number | null = null
    let events: TraceEvent[] = []
    let heartbeats: number[] = []
    let currentStage: StreamStage | null = null
    let body = ''
    // 图片轮的本轮实话先攒着（等待页不该把上一轮的正文放出来）：图贴上了它就多余
    // （图本身就是交代），没贴上才并进说明。
    let imgTokens = ''
    let sawImage = false

    const push = (patch: Partial<RunState>) => {
      setRun((prev) => ({ ...prev, ...patch }))
    }

    // 图片轮的失败统一去处：不拆卡。图是这顿饭的附加物，它失败不该让卡片消失——
    // 卡片与它的落点原样保留，本轮实话并进说明（说明 = 原说明 + 本轮实话）。
    // 返 false = 不是图片轮，调用方按普通轮的失败路径走。
    const keepCardAfterImageFailure = (extra: string): boolean => {
      if (!imgBase) return false
      const done: RunState = { ...imgBase, status: 'succeeded', answer, body: imgBase.body + extra }
      saveRun(done, text)
      setRun(done)
      return true
    }

    try {
      for await (const ev of streamChat(
        {
          sessionId,
          message,
          wantImage: false,
          // 本餐用餐人（2026-10-15）。走 ref 取最新值，**不**把 crewPicked 加进
          // 下面 useCallback 的依赖：那会让 send 每勾一次人就变成一个新函数，
          // 首屏所有绑了它的按钮跟着重挂。与 textRef/runRef 同一套理由。
          // 空数组时后端收不到这个字段 ⇒ 不写 members 键 ⇒ 这一顿记为未归属。
          members: crewRef.current,
          // 图片轮三件套：钉到这条卡的这条记录（后端按 record+index 找，不吃菜名
          // 相似度的亏）。普通轮不带——后端只在三件套齐时走钉卡分支。
          ...(img && target
            ? { targetRecordId: target.recordId, targetRecipeIndex: img.index, targetDishName: img.dishName }
            : {}),
        },
        ac.signal,
      )) {
        const at = Date.now() - startedAt.current

        switch (ev.kind) {
          case 'heartbeat':
            heartbeats = [...heartbeats, at]
            // 心跳带的 elapsed 是后端自己的计时，用它校准本地时钟。
            push({ heartbeats, elapsed: Math.max(at, ev.elapsed * 1000) })
            break

          case 'stage': {
            const nth = events.filter((x) => x.stage === ev.stage).length + 1
            events = [...events, { at, stage: ev.stage, nth }]
            currentStage = ev.stage
            push({ events, currentStage, elapsed: at })
            break
          }

          case 'token':
            // 图片轮的实话攒着不进等待页正文（见 imgTokens 注释），计时照推。
            if (img) {
              imgTokens += ev.text
              push({ elapsed: at })
              break
            }
            if (firstTokenAt === null) firstTokenAt = at
            body += ev.text
            push({ body, firstTokenAt, elapsed: at })
            break

          case 'answer':
            answer = ev.answer
            push({ answer, elapsed: at })
            break

          case 'finish':
            sawFinish = true
            // 普通轮：finish 是落点的唯一权威，不记下来★/评分就永远没有定位。
            // 图片轮不采纳它——那编号是确认话新记录的号，不是这张卡的落点
            // （起跑时 origin 已锁为卡的，这里自然不再改）。
            if (!img && typeof ev.sessionId === 'string' && typeof ev.recordId === 'number') {
              origin = { sessionId: ev.sessionId, recordId: ev.recordId }
            }
            break

          case 'image': {
            if (img) {
              // 贴回原卡：与后端 update_answer_image_at_index 落在同一条记录同一个
              // 下标——recipes[index] 换新三件，index 0 还要同步顶层（图注读顶层）。
              const card = answer ?? { recipes: [] }
              const recipes = card.recipes.map((r, i) =>
                i === ev.index
                  ? { ...r, image_url: ev.url, image_ai_generated: ev.aiGenerated, image_note: ev.note }
                  : r,
              )
              const next: ChefAnswer = { ...card, recipes, image_requested: true }
              if (ev.index === 0) {
                next.image_url = ev.url
                next.image_ai_generated = ev.aiGenerated
                next.image_note = ev.note
              }
              answer = next
              sawImage = true
              break   // 只改局部量：卡片在收尾一次性落定，中途不 push
            }
            const card: ChefAnswer = answer ?? { recipes: [] }
            answer = { ...card, image_url: ev.url, image_ai_generated: ev.aiGenerated }
            push({ answer, elapsed: at })
            break
          }

          case 'error':
            // 图片轮不拆卡：把后端这句话并进说明，卡片与落点原样保留。
            if (keepCardAfterImageFailure(`${imgTokens}${ev.message}`)) return
            push({ status: 'failed', error: ev.message, elapsed: at })
            return

          default:
            break
        }
      }
    } catch (err: unknown) {
      // 用户主动取消不是故障：不要把它写成"这一轮没有成功"，那是在冤枉自己。
      // 只标记中断，保留中断前真实收到过的阶段与正文。
      if (err instanceof DOMException && err.name === 'AbortError') {
        // 必须立刻落盘。运行期那个 2 秒定时保存只认 status==='running' 且未中断，
        // 取消后它就不再写了；如果这里不写，存档里留的会是两秒前的"运行中"快照，
        // 刷新一次就会被重新读成"页面离开时断开"——那是在替用户编造他做过什么。
        setRun((prev) => {
          // 只有「确实还在跑」的那一轮才有资格被标成中断。跨房切走（dish-pick /
          // fav-open）也会 abort 一个已经不在跑的控制器，此时 prev 是别处刚设置的
          // 状态（预填话头或恢复详情）——那种情况下盖上去会把别人的状态抹掉。
          if (prev.status !== 'running') return prev
          // 图片轮被取消：卡片本来就完整，标成"中断"是冤枉这顿饭——原样还回。
          if (imgBase) {
            saveRun(imgBase, text)
            return imgBase
          }
          const stopped: RunState = {
            ...prev,
            orphaned: true,
            orphanCause: 'cancelled',
            elapsed: Date.now() - startedAt.current,
          }
          saveRun(stopped, text)
          return stopped
        })
        return
      }
      const message = err instanceof Error ? err.message : String(err)
      const httpStatus = err instanceof ChatStreamError ? err.httpStatus : undefined
      // 图片轮失败不拆卡：图没贴上就说实话，卡片与落点原样保留（HTTP 状态一并交代）。
      if (keepCardAfterImageFailure(`${imgTokens}配图没能完成：${message}${httpStatus ? `（HTTP ${httpStatus}）` : ''}`)) return
      setRun((prev) => ({
        ...prev,
        status: 'failed',
        error: message,
        httpStatus,
        elapsed: Date.now() - startedAt.current,
      }))
      return
    }

    if (!sawFinish) {
      // 半个流：连接在 finish 之前结束了。**绝不能当成成功**——
      // 把中断的结果恢复成"已完成"，比丢掉它更糟。
      // 图片轮同理但不拆卡：没跑完就说没跑完，卡与落点原样。
      if (keepCardAfterImageFailure(`${imgTokens}配图这一轮没有跑完：连接在结束标记之前断了，图可能没贴上。`)) return
      setRun((prev) => ({
        ...prev,
        status: 'failed',
        error: '连接在这一轮结束前就断了：收到了一部分内容，但没有收到结束标记。下面看到的内容可能是不完整的。',
        elapsed: Date.now() - startedAt.current,
      }))
      return
    }

    setRun((prev) => {
      const done: RunState = imgBase
        ? {
            // 图片轮整卡还回：事件/心跳/耗时/原话全是这张卡自己的（图片轮的过程面
            // 是临时的，不该顶掉卡片的真实账目），只换答案与状态。
            ...imgBase,
            status: 'succeeded',
            answer,
            // 图贴上了：图本身就是交代，把"已补上配图"再印一遍是同一件事说两遍；
            // 没贴上（走到确认话之前的分支）才把本轮实话并进说明。
            body: sawImage ? imgBase.body : imgBase.body + imgTokens,
            origin: imgBase.origin,
          }
        : {
            ...prev,
            status: 'succeeded',
            answer,
            body,
            events,
            heartbeats,
            currentStage,
            firstTokenAt,
            elapsed: Date.now() - startedAt.current,
            // 后端没给编号就不写（宁缺勿假），动作条会按「没有落点」整体隐藏。
            origin,
          }
      // 图片轮落盘时草稿传当前草稿 text：message 是按钮句（给「X」配张图），
      // 把它回填进输入框等于替用户打了一句话。
      saveRun(done, imgBase ? text : message)
      return done
    })
  }, [sessionId, text, run.status])

  /**
   * 菜名右侧「配图 / 换一张图」入口。三件套把这轮钉到卡上（后端按 record+index 找，
   * 不吃菜名相似度的亏）；没有落点的轮按钮本就不可见（上层按动作条同一可见性挡），
   * 这里再守一道：宁可不发，也不把图挂到错的记录上。
   * index 恒 0：首屏主菜就是 recipes[0]（buildResultVM 的 lead 定义）。
   */
  const requestImage = useCallback(
    (dishName: string, revision: boolean) => {
      const cur = runRef.current
      if (cur.status !== 'succeeded' || cur.restored || !cur.origin || !cur.answer) return
      const msg = revision ? `给「${dishName}」换一张图` : `给「${dishName}」配张图`
      void send(msg, { image: { dishName, index: 0, revision } })
    },
    [send],
  )

  const reset = useCallback(() => {
    localStorage.removeItem(RUN_KEY)
    setRun(EMPTY_RUN)
  }, [])

  /** 取消：主动断开这一轮。仍然保持 running 态并标记 orphaned，
   *  这样界面留在"这一轮被中断了、以下是中断前真实收到的"上，而不是假装什么都没发生。 */
  const cancel = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  /**
   * 历史会话切换（B 批次③，lave「会话切换」契约的落地）。
   *
   * 四条硬规矩，一条都不能松：
   *  1 正在跑的轮先真实断掉，**并且先存档再覆盖**：abort 前把
   *    orphaned=cancelled 写进 RUN_KEY，否则 AbortError 到达时 run 已被
   *    下面的 setRun 换成新内容，catch 里 `prev.status !== 'running'` 的
   *    保护会让这一轮静默消失——那是在替用户抹掉他等过的事。
   *    面板顶部同时给出明确文案（「是被取消，不是失败」）。
   *  2 sessionId 要同步写回 localStorage：ensureSession 刷新时读的是缓存，
   *    不写的话切了也白切，一刷新就回到旧会话。
   *  3 恢复只按该会话**真实存了什么**走：answer 是 JSON 字符串时 parse 成功
   *    才恢复成只读详情（restored=true，动作条按契约隐藏）；parse 失败说明
   *    这一轮落库的是**正文本身**（候选轮/纯散文轮），按正文恢复成 prose 结果；
   *    只有 answer 为空才回提问态，绝不编造详情。
   *  4 点当前会话不折腾（不触发取消、不重置界面）。
   */
  const pickSession = useCallback(
    (sid: string) => {
      if (sid === sessionId) {
        setRailOpen(false)
        return
      }

      const cur = runRef.current
      const runningNow = cur.status === 'running' && !cur.orphaned
      if (runningNow) {
        const stopped: RunState = {
          ...cur,
          orphaned: true,
          orphanCause: 'cancelled',
          elapsed: Date.now() - startedAt.current,
        }
        // 先存档：下面 setRun 换新状态后，AbortError 那条路就不会再写了。
        saveRun(stopped, textRef.current)
        setRailNotice('上一轮正在跑，这次切换把它取消了——是被取消，不是失败，中断记录已存档。')
        abortRef.current?.abort()
        abortRef.current = null
      } else {
        setRailNotice(null)
      }

      localStorage.setItem(SESSION_KEY, sid)
      setSessionId(sid)

      const s = railSessions.find((x) => x.session_id === sid)
      const msgs = s?.messages ?? []
      const last = msgs.length > 0 ? msgs[msgs.length - 1] : null
      const raw = last?.answer ?? ''
      let detail: ChefAnswer | null = null
      let prose = ''
      if (raw) {
        try {
          detail = JSON.parse(raw) as ChefAnswer
        } catch {
          // 这一轮落库的就是正文本身（候选轮/纯散文轮不产出结构化 JSON）。
          // 它不是「没有内容」，而是「内容不是卡片」——按正文恢复；
          // 否则用户点开历史只会看到一个空的提问页，像是这一轮从没答过。
          detail = null
          prose = raw
        }
      }
      setText(last?.user_text || '')
      setRun(
        detail
          ? {
              ...EMPTY_RUN,
              status: 'succeeded',
              request: last?.user_text ?? '',
              answer: detail,
              restored: true,
            }
          : prose
            ? {
                ...EMPTY_RUN,
                status: 'succeeded',
                request: last?.user_text ?? '',
                body: prose,
                restored: true,
              }
            : EMPTY_RUN,
      )
      setRailOpen(false)
    },
    [sessionId, railSessions],
  )

  /**
   * 周报房点「常做的菜 → 再做一顿」时接住话头。
   *
   * 今晚房在 Shell 里**常驻挂载**（只藏不卸，为了保住跑一半的那一轮），
   * 所以这个监听从进站起就一直活着，周报房切过来一定收得到。
   *
   * 先显式断掉正在跑的一轮再清空：直接 setRun(EMPTY_RUN) 会把「被中断」
   * 抹成「从没发生过」——正在跑的那轮必须留下中断的痕迹（同 cancel 的理由）。
   */
  useEffect(() => {
    const onDish = (e: Event) => {
      const name = String((e as CustomEvent<string>).detail ?? '')
      if (!name) return
      abortRef.current?.abort()
      abortRef.current = null
      localStorage.removeItem(RUN_KEY)
      setRun(EMPTY_RUN)
      setText(`我想再做一顿${name}`)
    }
    window.addEventListener('dish-pick', onDish)
    return () => window.removeEventListener('dish-pick', onDish)
  }, [])

  /**
   * 收藏房点条目回今晚（户型图第 4 步 · 与上面 dish-pick 同构的自定义事件）。
   *
   * 分两种情况，按收藏**实际存了什么**走（不替它编）：
   *   · 有 answer → 恢复成只读结果详情（restored=true）：ResultView 会把
   *     「真实过程」的账目与用时整块撤掉，因为收藏没存那些数，画出来只能是 0。
   *   · 没有 answer（纯散文那轮后端就没存下内容）→ 只把原话放回输入框，
   *     页面停在提问态——没有内容可恢复时，「恢复详情」就是假的。
   *
   * abort 同样放在最前：正在跑的那一轮要像 cancel 一样留下中断痕迹。
   */
  useEffect(() => {
    const onFav = (e: Event) => {
      const d = (e as CustomEvent<{ ask: string; dish: string; detail: ChefAnswer | null }>)
        .detail
      if (!d) return
      abortRef.current?.abort()
      abortRef.current = null
      localStorage.removeItem(RUN_KEY)
      setText(d.ask || '')
      setRun(
        d.detail
          ? {
              ...EMPTY_RUN,
              status: 'succeeded',
              request: d.ask,
              answer: d.detail,
              restored: true,
            }
          : EMPTY_RUN,
      )
    }
    window.addEventListener('fav-open', onFav)
    return () => window.removeEventListener('fav-open', onFav)
  }, [])

  const running = run.status === 'running'
  const blocked = !sessionId

  return (
    <div className="tn">
      <header className="tn-head">
        <span className="tn-brand">小膳管家</span>
        <span className="tn-dot" />
        <h1 className="tn-title">今天吃什么</h1>
        {/* 历史侧栏的常驻入口：只在今晚房（本组件）渲染，打开时顺手刷新列表。 */}
        <button
          type="button"
          className="tn-railbtn"
          onClick={() => {
            setRailOpen(true)
            loadSessions()
          }}
        >
          历史{railSessions.length > 0 ? ` ${railSessions.length}` : ''}
        </button>
        <span className={`tn-conn${sessionError ? ' is-bad' : ''}`}>
          {sessionError ? '会话不可用' : sessionId ? '就绪' : '连接中'}
        </span>
      </header>

      {(run.status === 'idle' || run.status === 'failed') && (
        <AskView
          run={run}
          text={text}
          onText={setText}
          onSend={() => void send()}
          running={running}
          blocked={blocked}
          fam={fam}
          week={week}
          cand={cand}
          fridge={fridgeBox}
          readErr={firstScreenError}
          /* 本餐用餐人（2026-10-15）：值 + 三个动作打包成一个对象。
             只在成员真的读出来、且至少有一位时才给——成员读不出来时给 null，
             AskView 那边就不设关卡（读不到人不能把首屏整个锁死）。
             `picked` 顺手过滤掉档案里已经删掉的名字：成员被移除后，localStorage
             里还留着他的名字，不该再显示成"选中"。 */
          crew={
            fam && fam.members.length > 0
              ? {
                  picked: crewPicked.filter((n) => fam.members.some((m) => m.name === n)),
                  confirmed: crewConfirmed,
                  onToggle: (name: string) =>
                    setCrewPicked((prev) =>
                      prev.includes(name) ? prev.filter((x) => x !== name) : [...prev, name],
                    ),
                  /* 全选 / 清空。判据用"已选人数是否等于成员总数"而不是另存一个
                     boolean——那个 boolean 会在成员被增删时与真实选择脱节。
                     注意这里判的是**当前**选择，所以在 crewPicked 已是全员时清空。 */
                  onSelectAll: () =>
                    setCrewPicked((prev) =>
                      prev.length === fam.members.length ? [] : fam.members.map((m) => m.name),
                    ),
                  onConfirm: () => {
                    setCrewConfirmed(true)
                    saveCrew(crewPicked)
                  },
                  onEdit: () => setCrewConfirmed(false),
                }
              : null
          }
        />
      )}

      {running && <WaitCard run={run} onCancel={cancel} onRestart={reset} />}

      {/* 结果页也要能接着说话（甲方案）：输入状态仍由本组件持有，
          与首屏是同一份 text——结果页只是把同一个书写面换了个位置渲染。 */}
      {run.status === 'succeeded' && (
        <RunResult
          run={run}
          onAgain={reset}
          text={text}
          onText={setText}
          onSend={() => void send()}
          blocked={blocked}
          onPickCandidate={(t) => void send(t)}
          onRequestImage={requestImage}
          /* 候选页右栏那三块（第 F 项）：与首屏读**同一份**已算好的数据，
             不重新拉接口——两处若各拉一次，"首屏说的人"和"候选页说的人"
             会在档案刚改过的那一瞬间不一致。cand 不传：待确认块是首屏专属。 */
          fam={fam}
          week={week}
          fridge={fridgeBox}
          readErr={firstScreenError}
        />
      )}

      {/* 历史会话侧栏：fixed 定位，不参与 .tn 的排版。 */}
      <SessionRail
        open={railOpen}
        sessions={railSessions}
        activeId={sessionId}
        notice={railNotice}
        loading={railLoading}
        error={railError}
        onPick={pickSession}
        onClose={() => setRailOpen(false)}
      />
    </div>
  )
}










