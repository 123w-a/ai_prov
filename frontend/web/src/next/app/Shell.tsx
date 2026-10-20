import { useEffect, useState, type ReactNode } from 'react'
import { switchActiveMember, addMember, updateMember, deleteMember } from '../../api/client.ts'
import type { MemberInput } from '../../api/client.ts'
import { familyFacts, notifyMemberSwitched, useHousehold, type FamilyData } from '../data/household.ts'
import { ProfileView } from '../views/ProfileView.tsx'

/**
 * 全局导航壳（2026-09-29 户型图第 2 步）。
 *
 * 它只做一件事：让另外三个房间**看得见、点得动、刷新不丢、后退不迷路**。
 * 今晚房内的一切（AskView / WaitCard / ResultView 的三态、会话存档）
 * 全部原样保留在 TonightApp 里——这一步是接上导航，不是重建。
 *
 * 为什么用 hash 而不是路由库：
 *   · 刷新不丢房间 → URL 里本来就有 `#room=xxx`，读回来即可；
 *   · 后退不迷路   → 每次切换是一条真实的历史条目，浏览器自己管；
 *   · 不引依赖    → 当前架构刻意没有路由库，要引入得先单独论证成本（户型图红线）。
 * 两条判据都由浏览器保证，而不是我写的代码保证。
 *
 * 家庭成员抽屉（2026-10-01 B 批次①，lave gpt-5.6-sol 方案 B）：
 *   · 全局上下文不是房间 ⇒ 挂在本组件、由 React state 控制，
 *     **hash 不承载抽屉状态**（打开抽屉不该多一条历史、不该让后退键迷路）；
 *   · 任意房可开、Escape / 遮罩 / 关闭键三路关闭；
 *   · 成员切换走真端点 switchActiveMember（后端 active_id 是权威，刷新后仍保持），
 *     成功才广播 notifyMemberSwitched —— 起手页的「按谁推荐」会跟着刷新，
 *     而正在等待中的这一轮是 live:false 快照，不改口（绑定启动时的档案）。
 */

/** 房间顺序（2026-10-05 加入 profile）：身体档案紧跟今晚这一顿。
 *  放在这里而不是列尾，理由是它不是"管理工具"而是**每一轮推荐的前提**——
 *  放在收藏与服务之后，读起来会像一堆杂项里的最后一件。其余四项相对顺序没动。 */
const ROOMS = ['tonight', 'profile', 'weekly', 'fav', 'svc'] as const
type RoomId = (typeof ROOMS)[number]

const ROOM_LABEL: Record<RoomId, string> = {
  tonight: '今天吃什么',
  profile: '身体档案',
  weekly: '周报',
  fav: '收藏',
  svc: '服务',
}

/** 房间图标（2026-10-11 第三刀）：改用**从设计稿切片**的位图，替换原先的手绘 <Icon>。
 *
 *  为什么换：用户点名要「设计师给的导航小图标」逐个复刻。稿上这五枚是**双色**的
 *  （陶棕描边 + 浅陶填充），而 ui/Icon.tsx 是单色线稿（stroke=currentColor），
 *  画不出那一档；形状也对不上——稿里五项实测分别是房子 / 人像 / 折线图 / 五角星 /
 *  心形，原先映射的 utensils / people / calendar / star / hat 只有 star 一项撞对。
 *
 *  切片来源（.tmp-img/design-round/，1536×1024，S=1366/1536）：
 *  四项取自 profile-expect-v5.png；**「身体档案」取自 fav-expect-v4.png**——
 *  profile 稿里那一项正压在选中底块上、且是加深色，换一张同画风的稿才拿到
 *  与其余四枚一致的「未选中态」颜色。逐项 bbox / 闸门见
 *  assets/hero/manifest.json，样式见 styles/rail.css 的 .shell-tab-i-*。
 *
 *  取舍（诚实记）：位图把颜色也一起切死了（浅陶一档），稿里当前项图标会再加深；
 *  这里做不到，选中态仍由 .shell-tab.is-on 的整行浅陶块承担。
 *  类名直接用房间 id 拼（.shell-tab-i-${id}），不再需要一张常量映射表。 */

/** 从 hash 认房间。认不出（空、写错、手改过）一律回今晚——
 *  宁可把用户放回主面，也不要把他放进一个不存在的房间。 */
function readRoom(): RoomId {
  const m = /room=([a-z]+)/.exec(window.location.hash)
  if (m && (ROOMS as readonly string[]).includes(m[1])) return m[1] as RoomId
  return 'tonight'
}

const errText = (e: unknown): string => (e instanceof Error ? e.message : String(e))

export default function Shell({
  tonight,
  weekly,
  fav,
  svc,
}: {
  tonight: ReactNode
  weekly: ReactNode
  fav: ReactNode
  svc: ReactNode
}) {
  const [room, setRoom] = useState<RoomId>(readRoom)
  const [drawer, setDrawer] = useState(false)
  const [switching, setSwitching] = useState<string | null>(null)
  const [switchErr, setSwitchErr] = useState<string | null>(null)
  /** 增/改/删成员共用的进行中与错误（它们是同一种交互，不需要各有一份）。 */
  const [writeBusy, setWriteBusy] = useState(false)
  const [writeErr, setWriteErr] = useState<string | null>(null)

  // 不再解构 activeName：竖栏补密后成员名单直接列出并标出当前那位，
  // 「当前是谁」由 family.active_id 现算，多留一个派生量只会多一处可能不同步的名字。
  // readError 只往档案房传：那里是"读不到"最该说清原因的界面（竖栏只有一行位置，
  // 而且它已由兜底文案「还没有读出来」如实表达）。
  const { family, applyFamily, readError } = useHousehold()
  const facts = familyFacts(family)

  /**
   * 三个写动作共用的收口。
   *
   * 后端每个写端点都返回**整份**新 family，所以直接 applyFamily 用它，
   * 不另发一次 GET：重拉既多一次往返，又会在"后端已写、GET 未回"的窗口里显示旧档案。
   *
   * 成功之后仍然广播一次：本组件的实例已经由 applyFamily 更新了，但起手页有它自己的
   * useHousehold 实例——改了成员名字而不广播，那边会一直显示旧名字。
   * 等待页用的是 live:false 快照，它**不会**跟着变，这正是要的（那一轮绑定启动时的档案）。
   */
  const runWrite = async (fn: () => Promise<FamilyData>) => {
    if (writeBusy) return
    setWriteBusy(true)
    setWriteErr(null)
    try {
      applyFamily(await fn())
      notifyMemberSwitched()
    } catch (err) {
      setWriteErr(`没有保存成功，档案没有改动：${errText(err)}`)
    } finally {
      setWriteBusy(false)
    }
  }

  useEffect(() => {
    const sync = () => setRoom(readRoom())
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])

  // Escape 关闭（只在抽屉打开时挂监听，不打扰正常键入）。
  useEffect(() => {
    if (!drawer) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDrawer(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [drawer])

  const go = (id: RoomId) => {
    // 点当前项不写 hash：否则每点一次都多一条历史，后退键会被同一页塞满而失效。
    if (id === readRoom()) return
    // 改 hash 会触发 hashchange → setRoom，页面与 URL 由同一条路径更新，
    // 不在这里另调 setRoom，避免状态与地址栏各走各的。
    window.location.hash = 'room=' + id
  }

  const pick = async (id: string) => {
    if (!family || family.active_id === id || switching) return
    setSwitching(id)
    setSwitchErr(null)
    try {
      await switchActiveMember(id)
      // 以后端返回为准：成功才广播，起手页等 live 订阅者重读到新档案。
      notifyMemberSwitched()
      setDrawer(false)
    } catch (err) {
      setSwitchErr(`切换没有成功（当前推荐没有变）：${errText(err)}`)
    } finally {
      setSwitching(null)
    }
  }

  return (
    <div className="shell">
      {/* 版心分两段（2026-10-04 骨架裁决：左侧常驻竖导航）：.shell-body 提供版心与两列栅格，
          导航在左、房间在右。页脚留在 .shell-body 之外，按同一版心居中，所以三者的左右边界
          仍然对齐。下面两行的缩进刻意不重排——只加一层包裹，避免整段挪位产生看不出实际
          变化的巨型 diff（真要重排缩进应单独一次提交）。 */}
      <div className="shell-body">
      <nav className="shell-nav" aria-label="主导航">
        <span className="shell-brand">小膳管家</span>
        {/* 定位小注（2026-10-05 竖栏补密）：横排时品牌靠右侧的日期与家庭状态平衡，
            改成竖栏后顶端只剩一个词，下面是导航、再下面是一整片空。加这一行不是装饰——
            它就是页脚那句「家庭膳食规划助手」，写在这里让竖栏的上端有收束。 */}
        <span className="shell-brand-note">家庭膳食规划助手</span>
        {ROOMS.map((id) => (
          <button
            key={id}
            type="button"
            data-room={id}
            className={`shell-tab${room === id ? ' is-on' : ''}`}
            aria-current={room === id ? 'page' : undefined}
            onClick={() => go(id)}
          >
            {/* 位图图标（2026-10-11 第三刀）：图形本体由 .shell-tab-i-<room> 的
                background 供给，尺寸写死在 rail.css 里、取自 manifest.json 的
                cssWidth/cssHeight，不用 contain 硬撑。它是纯装饰，房间名在旁边的
                .shell-tab-t 里已经读得出来，所以不进无障碍树。 */}
            <span className={`shell-tab-i shell-tab-i-${id}`} aria-hidden="true" />
            <span className="shell-tab-t">{ROOM_LABEL[id]}</span>
          </button>
        ))}
        {/* 竖栏装饰块改形记录（2026-10-10）：上一版"书脊 + 三枚回纹章"（连同
            这里的三个 <i /> 与 rail.css 的对应规则）整段退役，换为首屏方向稿的
            轨底藤蔓——用户全盘认可该稿并点名「包括底下的树叶之类的背景图案」
            （六条之第 5 条）。艺术块本体保留（flex:1 吃竖栏剩余高度的职责不变），
            但按新稿**移到成员区之后**：稿里成员块不贴底、叶子在它下方的留白生长。
            旧注释"不要随意挪"是旧布局的实测约束（art 在成员前吃中间空），新位置
            由新稿裁决，同样靠 flex:1 吸收高度差。装饰内容与样式见 rail.css。 */}
        {/* 历史会话常驻块（2026-10-19）：插在家庭成员区与轨底藤蔓之间——
            成员管「这一顿给谁吃」，会话管「以前问过什么」，两者都是全局上下文，
            相邻放；藤蔓仍在其下吃剩余高度。块体由 SessionRail portal 进来
            （会话状态在今晚房手里，这里只给位置）。只在今晚房显示：
            会话是今晚房的东西，别的房间不该看到它。 */}
        <div id="session-rail-slot" className="shell-session" hidden={room !== 'tonight'} />
        {/* 轨底藤蔓（2026-10-10 换形后的新位置）：成员区之下、竖栏留白处生长——
            与首屏方向稿一致（稿里成员块不贴底，叶子在它下方）。flex:1 吃掉
            底部剩余高度，竖栏怎么变高都不用改数字；样式见 rail.css。 */}
        <span className="shell-rail-art" aria-hidden="true" />
      </nav>

      <div className="shell-main">
        {/*
          今晚房**常驻挂载、只藏不卸**：用户跑到一半切去别的房再回来，
          这一轮的 run、心跳、2 秒存档定时器必须还活着。卸载它会把那一轮
          判成断线——那是替用户编造"他离开过"（同 TonightApp 里取消不伪装
          成失败的理由）。
          其余房间没有跨房状态，按需挂载即可。
        */}
        <div className="shell-room" hidden={room !== 'tonight'}>
          {tonight}
        </div>
        {/* 身体档案房：与周报/收藏/服务同属"按需挂载"——它没有跨房状态。
            档案本身由本组件的 useHousehold 读一次再传下去（live=true，切换后自会刷新），
            房间内不许自己再拉一份。 */}
        {room === 'profile' && (
          <div className="shell-room">
            <ProfileView
              family={family}
              shared={facts?.shared ?? null}
              readErr={readError}
              onPick={(id) => void pick(id)}
              onAdd={(input: MemberInput) => void runWrite(() => addMember(input))}
              onUpdate={(id: string, input: MemberInput) => void runWrite(() => updateMember(id, input))}
              onDelete={(id: string) => void runWrite(() => deleteMember(id))}
              switching={switching}
              switchErr={switchErr}
              busy={writeBusy}
              writeErr={writeErr}
            />
          </div>
        )}
        {room === 'weekly' && <div className="shell-room">{weekly}</div>}
        {/* 收藏房同周报房：按需挂载。它唯一的跨房动作（点条目回今晚）走
            自定义事件，不需要自己常驻——常驻的只有今晚房。 */}
        {room === 'fav' && <div className="shell-room">{fav}</div>}
        {/* 服务房（第 5 步）：四个房都在下面四条分支里渲染完毕，
            RoomPlaceholder 占位组件已随之完成使命并删除。 */}
        {room === 'svc' && <div className="shell-room">{svc}</div>}
      </div>
      </div>

      {/* 页脚：五个房共用的底部收束。没有它时，周报这类内容较少的房间会在
          下方留 331px 纯空白（实测），页面看起来像"没画完"而不是"留白"。
          它是版式结构而不是装饰：一条发丝线 + 落款，把版心合上。 */}
      <footer className="sheet-foot">
        <span>小膳管家</span>
        <span className="sheet-foot-r">家庭膳食规划助手</span>
      </footer>

      {/* 家庭成员抽屉：遮罩点击与 Escape 都关；面板内点击 stopPropagation
          不穿透到底层房间（否则关不掉还会误点导航）。 */}
      {drawer && (
        <div className="sd-mask" onClick={() => setDrawer(false)}>
          <aside
            className="sd"
            role="dialog"
            aria-modal="true"
            aria-label="家庭成员"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sd-head">
              <h2 className="sd-h">家庭成员</h2>
              <button type="button" className="sd-close" onClick={() => setDrawer(false)}>
                关闭
              </button>
            </div>
            <p className="sd-sub">谁在被推荐 · 切换后下一条消息生效</p>

            {family && family.members.length > 0 ? (
              <ul className="sd-list">
                {family.members.map((m, i) => {
                  const on = family.active_id === m.id
                  // 标签复用 familyFacts 的同一份推导（推导只留一份，不许在这里抄一遍）。
                  // 但 facts 行以「名字 · 标签」开头，旁边已有 sd-name——直接放会显示成
                  // 「小美 小美 · 孕妇」，所以去掉名字前缀只留标签；没标签就不占位。
                  const full = facts?.members[i] ?? m.name
                  const prefix = `${m.name} · `
                  const label = full.startsWith(prefix) ? full.slice(prefix.length) : ''
                  return (
                    <li key={m.id}>
                      <button
                        type="button"
                        className="sd-item"
                        data-on={on}
                        disabled={on || switching !== null}
                        onClick={() => void pick(m.id)}
                      >
                        <span className="sd-name">{m.name}</span>
                        {label && <span className="sd-desc">{label}</span>}
                        {on && <span className="sd-on">当前推荐</span>}
                        {switching === m.id && <span className="sd-busy">切换中…</span>}
                      </button>
                    </li>
                  )
                })}
              </ul>
            ) : (
              <p className="sd-empty">档案还没有读出来，先不猜成员。</p>
            )}

            {facts?.shared && <p className="sd-shared">{facts.shared}</p>}
            {switchErr && (
              <p className="sd-err" role="alert">
                {switchErr}
              </p>
            )}
            <p className="sd-note">
              切换只影响下一条消息。正在等待中的这一轮仍按启动时的档案跑完，不会中途换人。
            </p>
          </aside>
        </div>
      )}
    </div>
  )
}
