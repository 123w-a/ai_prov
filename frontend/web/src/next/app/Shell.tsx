import { useEffect, useState, type ReactNode } from 'react'
import { switchActiveMember, addMember, updateMember, deleteMember } from '../../api/client.ts'
import type { MemberInput } from '../../api/client.ts'
import { familyFacts, notifyMemberSwitched, useHousehold, type FamilyData } from '../data/household.ts'
import { ProfileView } from '../views/ProfileView.tsx'
import { Icon, type IconName } from '../ui/Icon.tsx'

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
  tonight: '今晚这一顿',
  profile: '身体档案',
  weekly: '周报',
  fav: '收藏',
  svc: '服务',
}

/** 房间图标（2026-10-05 竖栏补密第二刀）。
 *
 *  复用 ui/Icon.tsx 里已有的 13 个图标，不新画、不引依赖。Icon 是 width/height=1em
 *  且 stroke=currentColor，所以尺寸与颜色都由本片的 CSS 决定——文字调大它跟着大。
 *
 *  映射依据（不是随手配）：今晚这一顿=餐具、身体档案=人、周报=日历、收藏=星。
 *  服务这一项实测其页面是「附近餐厅 + 服务规划」（ServiceView 的 sv-k 写着"附近餐厅"），
 *  所以给厨师帽而不是对话或漏斗——配错图标比不配更糟，这一项是专门核对过的。 */
const ROOM_ICON: Record<RoomId, IconName> = {
  tonight: 'utensils',
  profile: 'people',
  weekly: 'calendar',
  fav: 'star',
  svc: 'hat',
}

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
  const { family, applyFamily } = useHousehold()
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
            <Icon name={ROOM_ICON[id]} />
            <span className="shell-tab-t">{ROOM_LABEL[id]}</span>
          </button>
        ))}
        {/* 竖栏装饰：册页"索引脊柱"（2026-10-05 第四刀）。
            用户拍板要纸面装饰纹，原话"前端本来就是为了好看而来的"，并要我再议一轮
            "做什么样的装饰最好看"。lave(gpt-6.1-sol) 首选索引脊柱、lave2 首选 45° 斜纹
            —— 此处采纳前者：斜纹与全站已有的方格纸底构成三个方向、会打架，且斜纹是
            商务科技语言而非中文册页。
            但把 lave2 的渐变遮罩技巧嫁了过来：脊柱上下两端淡出（见 rail.css 的 mask），
            否则硬截断会像上上轮那条贯穿边线一样失败。

            位置必须在**目录块之后、成员区之前**：它要靠 flex:1 吃掉两者之间的剩余高度。
            第一版我把它插在了成员区之后，实测成员区顶 y 与目录块底重合（都是 364），
            脊柱被挤到成员区下面去了——所以这一处的位置是有实测依据的，不要随意挪。
            用真实 flex spacer 而非绝对定位：竖栏高度变化时不需要改任何数字。
            aria-hidden——纯装饰，读屏不该念它。 */}
        <span className="shell-rail-art" aria-hidden="true" />
        {/* 家庭成员（2026-10-01 B 批次①，2026-10-05 竖栏补密时展开成区块）：
            它是全局上下文、不是房间，所以挂在本组件、由 React state 控制，
            hash 不承载它的状态（打开抽屉不该多一条历史、不该让后退键迷路）。

            为什么要展开：竖栏 900px 里导航只占 135px，空掉约 78%，而"再加几个房间"
            在数学上填不满（九项也才 300px）。这里展开是**零新数据**的——family 已经在
            useHousehold 手里，pick() 也早就存在（原先只被抽屉用）。所以它不是把留白
            装饰掉，而是把一件本来只能进抽屉做的事挪到看得见的地方：不进任何房间就能换人。

            与抽屉的分工：这里管"换人"（一次点击即生效），抽屉管"管理"（增删改在身体档案页）。
            两者不冲突，也不互相取代。 */}
        <span className="shell-ctx">
          <span className="shell-ctx-h">家庭成员</span>
          {family && family.members.length > 0 ? (
            <ul className="shell-members">
              {family.members.map((m) => {
                const on = family.active_id === m.id
                return (
                  <li key={m.id}>
                    <button
                      type="button"
                      className={`shell-member${on ? ' is-on' : ''}`}
                      aria-current={on ? 'true' : undefined}
                      disabled={switching !== null}
                      title={on ? '当前生效' : `切换到 ${m.name}`}
                      onClick={() => void pick(m.id)}
                    >
                      <span className="shell-member-name">{m.name}</span>
                      {on && <span className="shell-member-on">当前</span>}
                    </button>
                  </li>
                )
              })}
            </ul>
          ) : (
            <span className="shell-member-empty">还没有读出来</span>
          )}
          {/* 抽屉与上面那几行的分工：这里只有名字（快速换人），抽屉里有每个人的
              画像摘要标签与共同忌口（要不要换、换了会怎样，得先看清）。所以按钮叫
              「成员详情」——原先叫「管理成员 · 我」，后缀与上面已列出的当前成员重复，
              「管理」也不准：增删改在身体档案房，抽屉里只是看与切换。 */}
          <button type="button" className="shell-ctx-btn" onClick={() => setDrawer(true)}>
            成员详情
          </button>
        </span>
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
