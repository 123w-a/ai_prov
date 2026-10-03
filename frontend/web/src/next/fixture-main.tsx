import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// 与 main.tsx 用的是**同一份样式、同一顺序**（七个切片按序拼回原 tonight.css，
// 后接 primitives/fav/service 三片只定义新类名的追加层）。
// 动这十行之前先读 main.tsx 里那段说明：顺序变了层叠就变，而且看不出来。
// 2026-10-01 修：这里曾漏掉 fav.css 与 service.css（注释还写着「同一份样式」）——
// 于是在夹具台量到的 .rs-acts margin 是 0，正式页却是 18px，「夹具几何=正式页几何」
// 的承诺直接破了。样式片清单必须与 main.tsx 逐行对齐。
import './styles/tokens.css'
import './styles/base.css'
import './styles/wait.css'
import './styles/result-v1v2.css'
import './styles/result-v3.css'
import './styles/steps.css'
import './styles/motion.css'
import './styles/primitives.css'
import './styles/fav.css'
import './styles/service.css'

import { RunResult } from './views/ResultView.tsx'
import { WaitCard } from './blocks/WaitCard.tsx'
import { AskRail } from './blocks/AskRail.tsx'
import { CASES, CASE_IDS } from './fixtures.ts'

/**
 * 只读夹具台（/fixture.html）。
 *
 * 外壳、页头、以及结果区／等待区的容器层级**逐字照抄 TonightApp 的真实结构**，
 * 这样在这里量到的几何（左边界、栏宽、行宽）就是正式页面的几何。
 *
 * 这里刻意**不定义组件函数**：本文件与 main.tsx 一样是入口，只做一次 render。
 * 定义组件会触发 react(only-export-components)（"Fast refresh only works when a file
 * has exports"）——那会让我自己的验收台污染 lint 计数，进而掩盖真正的回归。
 * 形态切换因此改用 URL 驱动（链接点击即整页重载），不引入任何状态。
 *
 *   /fixture.html?case=typical          干净渲染（用来截图与量 computed style）
 *   /fixture.html?case=full&bar=1       带底部切换条（用来人工翻各形态）
 */

const query = new URLSearchParams(window.location.search)
const asked = query.get('case') ?? 'typical'
const showBar = query.get('bar') === '1'
const caseId = CASES[asked] ? asked : 'typical'
const current = CASES[caseId]

/* 缩放档位：?zoom=1.25
 *
 * 浏览器真实缩放经这套验收工具设不了，而「text-stroke 伪粗体在非整数缩放下会不会发虚」
 * 必须按 100%/125%/150% 三档看。CSS zoom 是此处可控的等价替代——它把 28px 的字
 * 按 35px / 42px 交给同一个光栅化器，与浏览器缩放对**设备像素下字形尺寸**的作用一致。
 * 注意它不替代媒体查询/DPR 相关行为，本夹具台也不用来验那些。 */
const zoom = Number(query.get('zoom') ?? '')
if (Number.isFinite(zoom) && zoom > 0 && zoom !== 1) {
  document.documentElement.style.zoom = String(zoom)
}

/* ?stroke=0 —— 关掉伪粗体做对照。
 * 只看"有描边时糊不糊"是判不了的：描边本来就多加一圈墨，边沿像素必然变多。
 * 必须与不描边的同字号渲染并排比，才能把"笔画变粗"和"字形发虚"区分开。 */
if (query.get('stroke') === '0') {
  const s = document.createElement('style')
  s.textContent = '.dish-name { -webkit-text-stroke: 0 !important }'
  document.head.append(s)
}

/* ?fontlab=1 —— 字形实验室。
 *
 * 同一标题在若干**设备像素尺寸**下并排渲染，每档上下两行：
 * 上行是当前实现的 0.5px 描边，下行是同一字号的无描边对照。
 *
 * 为什么要并排：只看带描边那张图，"笔画变粗"和"字形边缘糊"是分不开的——
 * 描边本来就多加一圈墨，边沿像素必然变多。只有和同字号的无描边渲染比，
 * 才能把两者区分开。
 *
 * 为什么用 px 尺寸而不是 CSS zoom：DPR=1 时 zoom:1.25 对 28px 的字，
 * 就是让光栅化器按 35 个设备像素画，与 font-size:35px 完全等价——
 * 拿 100/125/150 三档测必然全清晰，等于白测。
 * 真正会出问题的是**落在非整数上的设备像素**（如 DPR1.25 × 浏览器 110% = 38.5px），
 * 所以档位里刻意放了 30.8 和 38.5 两个非整数。 */
const LAB_SIZES = [28, 30.8, 35, 38.5, 42]
const LAB_TITLE = '香煎三文鱼配蒜香西兰花'
const fontLab = query.get('fontlab') === '1'

const barStyle = {
  position: 'fixed' as const,
  left: 0,
  right: 0,
  bottom: 0,
  zIndex: 9999,
  display: 'flex',
  flexWrap: 'wrap' as const,
  alignItems: 'center',
  gap: 6,
  padding: '10px 16px',
  background: 'rgba(36,35,31,.94)',
  color: '#fff',
  font: '12px/1.6 system-ui, sans-serif',
}

const linkStyle = {
  padding: '3px 9px',
  border: '1px solid rgba(255,255,255,.35)',
  borderRadius: 3,
  color: '#fff',
  textDecoration: 'none',
}

const rootEl = document.getElementById('root')
if (!rootEl) throw new Error('夹具台页面缺少 #root 容器')

/* 只在容器还是空的时候挂载。
 *
 * 起因是一条真实抓到的 console 错误：「You are calling ReactDOMClient.createRoot() on a
 * container that has already been passed to createRoot() before.」——Vite 在入口模块不是
 * React 刷新边界时，保存后会把它整个重跑一遍，于是 createRoot 被调用第二次。
 * 模块级变量挡不住（重跑就是新模块作用域），所以判据只能取自 DOM：
 * 完整加载时 #root 必为空，重跑时已有子树。
 *
 * 为什么必须修：这个夹具台是我做视觉验收的证据源，而验收判据里有一条是
 * console 零错误。留一条自己制造的常驻错误，等于给后面每一步都埋一个假红灯。 */
if (rootEl.childElementCount === 0) {
  createRoot(rootEl).render(
    <StrictMode>
      <div className="tn">
        <header className="tn-head">
          <span className="tn-brand">小膳管家</span>
          <span className="tn-dot" />
          <h1 className="tn-title">今晚这一顿</h1>
          <span className="tn-conn">就绪</span>
        </header>

        {fontLab ? (
          <div style={{ maxWidth: 900 }}>
            <p style={{ font: '12px/1.7 system-ui, sans-serif', color: '#8e8271', margin: '0 0 22px' }}>
              字形实验室 · 每档上行 = 当前实现（-webkit-text-stroke: 0.5px），下行 = 同字号无描边对照
              <br />
              尺寸按**设备像素**给：DPR=1 且浏览器 100% 时就是 CSS px；30.8 与 38.5 用来复现非整数缩放
            </p>
            {LAB_SIZES.map((sz) => (
              <div key={sz} style={{ marginBottom: 30 }}>
                <div style={{ font: '11px system-ui, sans-serif', color: '#8e8271', marginBottom: 6 }}>
                  {sz}px {Number.isInteger(sz) ? '· 整数设备像素' : '· 非整数设备像素'}
                </div>
                <div className="dish-name" style={{ fontSize: sz, textAlign: 'left', margin: 0 }}>
                  {LAB_TITLE}
                </div>
                <div
                  className="dish-name"
                  style={{ fontSize: sz, textAlign: 'left', margin: 0, WebkitTextStroke: '0' }}
                >
                  {LAB_TITLE}
                </div>
              </div>
            ))}
          </div>
        ) : current.kind === 'ask' ? (
          /* 提问页外壳**逐字照抄** AskView.tsx 的结构：.starter > .ask-layout > (.ask-main + AskRail)。
           * 少掉 .ask-layout 就量不到两栏几何——它是 minmax(0,1fr) var(--rail,278px) / gap 0 40px，
           * 直接把 AskRail 单独塞进来，右栏宽度和左右边界都会是错的。 */
          <section className="starter">
            <div className="ask-layout">
              <div className="ask-main">
                <h2 className="ask-lead">
                  今晚这一顿，按<em>两个人的身体</em>来定。
                </h2>

                <label className="ask">
                  <span className="ask-label">你想吃什么</span>
                  {/* 用 defaultValue（非受控）而非 value+onChange：本文件刻意不定义组件、
                      不引状态，受控却缺 onChange 会触发 React 警告，把
                      「console 零错误」这条验收判据污染成假红灯。 */}
                  <textarea
                    className="ask-input"
                    rows={2}
                    defaultValue="今晚想用冰箱里的东西做一顿，我增肌、小美怀孕，做个我们俩都能吃的。"
                  />
                </label>

                <div className="ask-actions">
                  <span className="ask-hint">
                    一句话就够。小膳管家会结合家人的健康情况来定这一顿。
                  </span>
                  <button className="ask-go" type="button">
                    开始
                  </button>
                </div>
              </div>
              {/* 逐字段展开而不写 {...current.rail}：rail 是可选字段，逐字段兜底 null
                  能让「忘了给 rail 的 ask case」退化成四块全空，而不是 TS 报错或运行时崩。 */}
              <AskRail
                fam={current.rail?.fam ?? null}
                week={current.rail?.week ?? null}
                cand={current.rail?.cand ?? null}
                fridge={current.rail?.fridge ?? null}
              />
            </div>
          </section>
        ) : current.kind === 'result' ? (
          <RunResult run={current.run} onAgain={() => {}} />
        ) : (
          <WaitCard run={current.run} onCancel={() => {}} onRestart={() => {}} />
        )}
      </div>

      {showBar && (
        <nav style={barStyle}>
          <span style={{ opacity: 0.6, marginRight: 8 }}>夹具形态</span>
          {CASE_IDS.map((k) => (
            <a
              key={k}
              href={`?case=${k}&bar=1`}
              style={
                k === caseId ? { ...linkStyle, background: 'rgba(255,255,255,.9)', color: '#24231f' } : linkStyle
              }
            >
              {CASES[k].label}
            </a>
          ))}
          <span style={{ opacity: 0.55, marginLeft: 'auto' }}>构造样本，非真实返回</span>
        </nav>
      )}
    </StrictMode>,
  )
}
