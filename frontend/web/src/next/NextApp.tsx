import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChefAnswer, StreamStage } from '../types.ts'
import { createSession } from '../api/client.ts'
import { ChatStreamError, streamChat } from './data/chatStream.ts'
import type { ChatStreamEvent } from './data/chatStream.ts'

/**
 * 第 1 增量：真实接通检查页。
 *
 * 它故意不画成品界面。目的是把 /api/chat 的真实返回捞出来看清楚：
 *   - 后端到底填了 ChefAnswer 的哪些字段（哪些常年是空的）
 *   - structuring / answer / image / finish 的真实时序
 *   - answer_kind=candidates 那条不推 answer 的分支长什么样
 *   - 失败时到底是 HTTP 非 2xx，还是 200 之后流里出错
 *
 * 看清之后再把它换成真正的界面，而不是照着 schema 猜。
 */

const STAGE_LABEL: Record<StreamStage, string> = {
  thinking: '思考中',
  writing: '撰写中',
  searching: '查证中',
  auditing: '审核中',
  generating_image: '生成配图中',
  structuring: '整理结构化',
  switching_model: '切换模型',
}

/** 一次发送的四种结局。四态必须分明，不许把失败画成空。 */
type Outcome =
  | { kind: 'idle' }
  | { kind: 'running' }
  | { kind: 'succeeded'; answer: ChefAnswer | null; hadStructuring: boolean; elapsed: number }
  | { kind: 'failed'; message: string; httpStatus?: number }

interface LogLine {
  at: number
  text: string
}

interface FieldFact {
  field: string
  present: boolean
  summary: string
}

/** 把一份 ChefAnswer 摊平成"后端到底给了什么"的事实清单。 */
function answerFacts(answer: ChefAnswer): FieldFact[] {
  const recipes = answer.recipes ?? []
  const lights = answer.health_lights ?? []
  const matrix = answer.dish_matrix ?? []
  const adjustments = answer.member_adjustments ?? []
  const guardrails = answer.guardrails ?? []
  const sources = answer.sources ?? []

  const lightTally = ['green', 'yellow', 'red']
    .map((level) => {
      const n = lights.filter((l) => l.level === level).length
      return n > 0 ? `${level}×${n}` : ''
    })
    .filter(Boolean)
    .join(' ')

  const withReason = lights.filter((l) => (l.reason ?? '').trim() !== '').length
  const matrixTally = ['可吃', '需调整', '待确认', '不可吃']
    .map((v) => {
      const n = matrix.filter((r) => r.verdict === v).length
      return n > 0 ? `${v}×${n}` : ''
    })
    .filter(Boolean)
    .join(' ')

  return [
    { field: 'opening', present: Boolean(answer.opening?.trim()), summary: answer.opening?.trim() ? `${answer.opening.trim().length} 字` : '空' },
    {
      field: 'recipes',
      present: recipes.length > 0,
      summary: recipes.length > 0 ? `${recipes.length} 道：${recipes.map((r) => r.name).join('、')}` : '空',
    },
    {
      field: 'health_lights',
      present: lights.length > 0,
      summary: lights.length > 0 ? `${lights.length} 盏（${lightTally}），其中 ${withReason} 盏带 reason` : '空（合法空态，提示词允许省略）',
    },
    {
      field: 'dish_matrix',
      present: matrix.length > 0,
      summary: matrix.length > 0 ? `${matrix.length} 行（${matrixTally}）` : '空（拿不准时后端与模型都允许留空）',
    },
    {
      field: 'member_adjustments',
      present: adjustments.length > 0,
      summary: adjustments.length > 0 ? `${adjustments.length} 条：${adjustments.slice(0, 2).join(' / ')}` : '空',
    },
    {
      field: 'guardrails',
      present: guardrails.length > 0,
      summary: guardrails.length > 0 ? `${guardrails.length} 条（${guardrails.map((g) => g.status).join('、')}）` : '空',
    },
    {
      field: 'sources',
      present: sources.length > 0,
      summary: sources.length > 0 ? `${sources.length} 条；带 snippet 的 ${sources.filter((s) => (s.snippet ?? '').trim() !== '').length} 条` : '空',
    },
    { field: 'primary_member', present: Boolean(answer.primary_member?.trim()), summary: answer.primary_member?.trim() || '空（后端事后注入，模型不产出）' },
    { field: 'chef_tip', present: Boolean(answer.chef_tip?.trim()), summary: answer.chef_tip?.trim() ? '有' : '空' },
    {
      field: 'image_requested',
      present: answer.image_requested === true,
      summary: answer.image_requested ? '真' : '假（无菜品时不置位）',
    },
  ]
}

const SESSION_KEY = 'xiaoshan.next.sessionId'

/**
 * 会话只建一次：刷新要接回原来那条，而不是每次加载都新开一条。
 * 用模块级 promise 兜住 React StrictMode 在开发模式下的双调用——
 * 否则一次加载会建出两个会话（实测留下过 3 个空会话）。
 */
let sessionPromise: Promise<string> | null = null

function ensureSession(): Promise<string> {
  const cached = sessionStorage.getItem(SESSION_KEY)
  if (cached) return Promise.resolve(cached)
  if (sessionPromise === null) {
    sessionPromise = createSession()
      .then((s) => {
        sessionStorage.setItem(SESSION_KEY, s.session_id)
        return s.session_id
      })
      .catch((err: unknown) => {
        // 失败不缓存：下一次重试必须真的重新去建，不能把一个失败的 promise 钉死。
        sessionPromise = null
        throw err
      })
  }
  return sessionPromise
}

export default function NextApp() {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [sessionError, setSessionError] = useState<string | null>(null)
  // 探针页的输入也要来自真实档案。曾经这里写的是「我爸血压高，做个全家都能吃的」——
  // 那是编的：档案里只有小美（孕期）和我（增肌），没有爸爸，也没有高血压。
  const [text, setText] = useState('今晚想用冰箱里的东西做一顿，我增肌、小美怀孕，做个我们俩都能吃的。')
  const [wantImage, setWantImage] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' })
  const [stage, setStage] = useState<StreamStage | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [body, setBody] = useState('')
  const [answer, setAnswer] = useState<ChefAnswer | null>(null)
  const [log, setLog] = useState<LogLine[]>([])
  const [tokenStats, setTokenStats] = useState({ count: 0, chars: 0 })
  const startedAt = useRef(0)

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

  const pushLog = useCallback((text: string) => {
    setLog((prev) => [...prev, { at: Date.now(), text }])
  }, [])

  const send = useCallback(async () => {
    if (sessionId === null) return
    const message = text.trim()
    if (!message) return

    setOutcome({ kind: 'running' })
    setStage(null)
    setElapsed(0)
    setBody('')
    setAnswer(null)
    setLog([{ at: Date.now(), text: `→ 发送（want_image=${wantImage ? '1' : '0'}）` }])
    setTokenStats({ count: 0, chars: 0 })
    startedAt.current = Date.now()

    let gotAnswer: ChefAnswer | null = null
    let sawStructuring = false
    let firstTokenAt: number | null = null

    try {
      for await (const event of streamChat({ sessionId, message, wantImage })) {
        // token 不进日志：一次真实调用能刷出三千多条，日志会被自己淹掉。
        // 只记第一条的到达时刻——「首字延迟」才是这里唯一有信息量的指标。
        if (event.kind === 'token') {
          if (firstTokenAt === null) {
            firstTokenAt = Date.now()
            pushLog(`第一个 token（距发送 ${((firstTokenAt - startedAt.current) / 1000).toFixed(1)}s）`)
          }
          setTokenStats((prev) => ({ count: prev.count + 1, chars: prev.chars + event.text.length }))
          setBody((prev) => prev + event.text)
          continue
        }

        logEvent(event, pushLog)
        switch (event.kind) {
          case 'heartbeat':
            setElapsed(event.elapsed)
            break
          case 'stage':
            setStage(event.stage)
            // {stage:'structuring'} 与独立的 {structuring:true} 是两个不同的事件，
            // 但两者都表示"正文说完、正在整理卡片"。判据必须都认，
            // 否则日志里明明有 structuring、面板却说"没有 structuring"。
            if (event.stage === 'structuring') sawStructuring = true
            break
          case 'structuring':
            sawStructuring = true
            break
          case 'answer':
            gotAnswer = event.answer
            setAnswer(event.answer)
            break
          case 'finish':
            setOutcome({
              kind: 'succeeded',
              answer: gotAnswer,
              hadStructuring: sawStructuring,
              elapsed: Math.round((Date.now() - startedAt.current) / 100) / 10,
            })
            break
          default:
            break
        }
      }

      // 流正常结束但没收到 finish：这本身是一种可疑状态，不许当成功。
      setOutcome((prev) =>
        prev.kind === 'succeeded'
          ? prev
          : {
              kind: 'failed',
              message: '流已结束，但没有收到 finish 事件',
            },
      )
    } catch (err) {
      const status = err instanceof ChatStreamError ? err.httpStatus : undefined
      setOutcome({
        kind: 'failed',
        message: err instanceof Error ? err.message : String(err),
        httpStatus: status,
      })
    }
  }, [sessionId, text, wantImage, pushLog])

  const facts = answer ? answerFacts(answer) : []

  return (
    <div className="nx">
      <header className="nx-head">
        <h1>小膳管家 · 接通检查</h1>
        <p className="nx-sub">
          第 1 增量。不画界面，只看真实返回：字段填了没、时序对不对、失败长什么样。
        </p>
      </header>

      <section className="nx-card">
        <div className="nx-row">
          <span className="nx-key">会话</span>
          <span className={sessionError ? 'nx-bad' : sessionId ? 'nx-ok' : 'nx-wait'}>
            {sessionError ? `建会话失败：${sessionError}` : (sessionId ?? '正在建会话…')}
          </span>
        </div>
        <div className="nx-row">
          <span className="nx-key">提醒</span>
          <span className="nx-warn">
            发送会真实调用 /api/chat：写一条真实会话记录、消耗真实额度。图片开关默认关。
          </span>
        </div>
      </section>

      <section className="nx-card">
        <textarea
          className="nx-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          aria-label="今晚想吃什么"
        />
        <div className="nx-actions">
          <label className="nx-check">
            <input type="checkbox" checked={wantImage} onChange={(e) => setWantImage(e.target.checked)} />
            同时要配图（会额外调用生图）
          </label>
          <button className="nx-btn" onClick={() => void send()} disabled={sessionId === null || outcome.kind === 'running'}>
            {outcome.kind === 'running' ? '进行中…' : '发送'}
          </button>
        </div>
      </section>

      <OutcomePanel outcome={outcome} stage={stage} elapsed={elapsed} />

      {body && (
        <section className="nx-card">
          <h2 className="nx-h2">
            正文（token 累积）
            <span className="nx-count">
              {tokenStats.count} 条 / {tokenStats.chars} 字
            </span>
          </h2>
          <pre className="nx-body">{body}</pre>
        </section>
      )}

      {answer !== null && (
        <section className="nx-card">
          <h2 className="nx-h2">后端实际填了什么</h2>
          <table className="nx-table">
            <thead>
              <tr>
                <th>字段</th>
                <th>有没有</th>
                <th>内容</th>
              </tr>
            </thead>
            <tbody>
              {facts.map((f) => (
                <tr key={f.field}>
                  <td className="nx-mono">{f.field}</td>
                  <td className={f.present ? 'nx-ok' : 'nx-empty'}>{f.present ? '有' : '空'}</td>
                  <td>{f.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className="nx-card">
        <h2 className="nx-h2">原始事件</h2>
        {log.length === 0 ? (
          <p className="nx-empty">还没有事件。</p>
        ) : (
          <ol className="nx-log">
            {log.map((l, i) => (
              <li key={`${l.at}-${i}`}>
                <span className="nx-mono">{(l.at - startedAt.current) / 1000 >= 0 ? `+${(((l.at - startedAt.current) / 1000)).toFixed(1)}s` : '—'}</span>
                <span>{l.text}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  )
}

function OutcomePanel({ outcome, stage, elapsed }: { outcome: Outcome; stage: StreamStage | null; elapsed: number }) {
  if (outcome.kind === 'idle') {
    return (
      <section className="nx-card nx-state nx-state-idle">
        <strong>还没开始</strong>
        <span>填入一句你真想吃的，点发送。</span>
      </section>
    )
  }
  if (outcome.kind === 'running') {
    return (
      <section className="nx-card nx-state nx-state-running">
        <strong>进行中</strong>
        <span>
          {stage ? STAGE_LABEL[stage] : '已连上，等第一个事件'}
          {elapsed > 0 ? ` · 后端已报告 ${elapsed}s` : ''}
        </span>
      </section>
    )
  }
  if (outcome.kind === 'failed') {
    return (
      <section className="nx-card nx-state nx-state-failed">
        <strong>失败</strong>
        <span>
          {outcome.httpStatus != null ? `HTTP ${outcome.httpStatus}：` : ''}
          {outcome.message}
        </span>
      </section>
    )
  }

  // 收窄必须先落到局部变量上：直接写 outcome.answer 的话，optional 链上的判空不会传播到下面。
  const result = outcome.answer
  if (result === null || (result.recipes ?? []).length === 0) {
    return (
      <section className="nx-card nx-state nx-state-empty">
        <strong>成功，但没有菜品卡</strong>
        <span>
          用时 {outcome.elapsed}s。
          {outcome.hadStructuring
            ? '过程走到了整理阶段，但整轮没有收到 answer 事件 —— 本轮只有正文，没有卡片。'
            : '整轮既没有 structuring 也没有 answer。'}
        </span>
      </section>
    )
  }
  return (
    <section className="nx-card nx-state nx-state-ok">
      <strong>成功</strong>
      <span>用时 {outcome.elapsed}s，{result.recipes.length} 道菜。</span>
    </section>
  )
}

function logEvent(event: ChatStreamEvent, push: (text: string) => void) {
  switch (event.kind) {
    case 'working':
      push('working')
      break
    case 'heartbeat':
      push(`heartbeat ${event.elapsed}s`)
      break
    case 'stage':
      push(`stage ${event.stage}`)
      break
    case 'structuring':
      push('structuring（正文说完，正在整理卡片）')
      break
    case 'answer':
      push(`answer（${(event.answer.recipes ?? []).length} 道菜）`)
      break
    case 'image':
      push(`image #${event.index} ai=${event.aiGenerated}`)
      break
    case 'image_failed':
      push(`image_failed ${event.indexes.join(',')}`)
      break
    case 'finish':
      push(`finish record_id=${event.recordId ?? '—'}`)
      break
    default:
      break
  }
}
