import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

/* 样式按**导入顺序**拼成原来那一份 tonight.css —— 顺序不能动。
 *
 * 2026-09-26 机械拆分（架构方案第八节第 2 步）：把 2635 行的 tonight.css 按现有分区
 * **连续切片**成下面七个文件，一个字没加、一条规则没移、没有加文件头注释
 * （加了字节就变，本步的证明也就作废）。
 * 判据已通过：按此顺序拼回的 sha256 = A3E229B34AF02D53E909C0889DA22828AED82C5326F06953FB23167F3A4A1CE1，
 * 与原文件逐字节相同（72986 字节）。
 *
 * **为什么不是 tokens / base / primitives / blocks / views 那五个语义文件**：
 * 这份 CSS 是追加式长出来的，同一批选择器被定义了三轮——`.result-head` 在
 * 814 / 884 / 1560 各一份，`.alerts` 在 954 / 1762 / 1894，`.light` 在 1240 / 2075，
 * `@media` 与 `@keyframes` 夹在中间。**这三轮靠先后顺序互相覆盖**，按语义重排等于改层叠：
 * 代码看起来更干净，视觉会漂，而且漂在哪用文字描述不出来——那正是方案里点名的最大风险。
 * 五层文件是第 5 步（逐块拆 Result.tsx、把 @media 收进 views.css）的目标，到那时再重排。
 *
 * 因此：**任何时候都不要调整这七行的先后**。
 */
import './styles/tokens.css'
import './styles/base.css'
/* 首屏右栏四块卡片（.ask-side + .fs-*，2026-10-05 从 base.css 整族搬出：
   base.css 当时 1008 行越过 1000 行上限）。必须紧跟 base.css——全仓只有它
   定义 .fs-*，而 base.css 里排在原段落之后的只有 .tn-* 导航族，两者不相交，
   所以"紧跟 base.css"与"原先在 base.css 内部"等价。 */
import './styles/firstscreen.css'
/* 版心框架（2026-10-04 第六轮）。.tn/.shell-nav/.sheet-foot 的 auto margin 会取消
   stretch，宽度退回内容 max-content——必须补 width:100%，故排在 base.css 之后。 */
import './styles/frame.css'
/* 左侧常驻竖导航（2026-10-04 骨架裁决第一步）。它整体覆盖 base.css 与 frame.css
   里 .shell-nav/.shell-tab 的横排形态，必须排在 frame.css 之后。 */
import './styles/rail.css'
import './styles/wait.css'
import './styles/result-v1v2.css'
import './styles/result-v3.css'
/* 营养数值表（第 5 项新增）。只定义 .nutri-* 新类名，插在 result-v3 之后
   不挪动已有层叠——详见文件头那段说明。 */
import './styles/nutrition.css'
import './styles/composer.css'
import './styles/sheet.css'
import './styles/steps.css'
import './styles/motion.css'
/* 原语层样式（第 4 步新增）。放在最后：它只定义 .p-* 新类名，
   与前面七片里的任何选择器都不冲突，所以加在末尾不会挪动已有层叠。 */
import './styles/primitives.css'
/* 收藏房样式（第 4 步新增）。与 primitives 同理：只定义 .fv-* 新类名，
   与前面所有片的选择器不相交，放末层不挪动已有层叠。
   它单独成片而不进 base.css：当时 base.css 已 875 行，追加会越过 checkScale 的
   1000 行上限（2026-10-01 注：抽屉与侧栏两段又拆出 panels.css 后，base.css 现 647 行）。 */
import './styles/fav.css'
/* 收藏房卡片片（2026-10-11 第二轮照 fav-expect-v4 稿重建）。从 fav.css 拆出：
   那一轮加的规则让 fav.css 到了 1011 行，越过 checkScale 的 1000 行上限。
   拆法按"片 = 一组同源规则"：fav.css 留骨架、三态、动作与既有元素；
   本片是**照稿重建的那一层**（页头星形/副标题图标、工具行、卡片几何、
   配图与角叶、元信息、右栏三块、页底花章、窄屏断点），一律后写覆盖。
   必须紧跟 fav.css 之后：它大量使用同选择器覆盖（.fv-h/.fv-list/.fv-thumb…），
   同特异性下后写的胜出，顺序错了整页就回到第一轮的几何。 */
import './styles/fav-cards.css'
/* 服务房样式（第 5 步新增）。同理：只定义 .sv-* 新类名，放末层不挪动已有层叠。 */
import './styles/service.css'
/* 地图 overlay 样式（2026-10-19 新增）。`.amap-origin-marker` 一族的定义原本只在
   旧入口 index.css 里，新前端没加载它 ⇒ 定位蓝点是空 div（不可见）、餐厅标记只剩
   裸数字。本片只定义 .amap-* / .nearby-map-* 这几个类名，不碰 composer.css 里的
   地图外壳尺寸。夹具台不展开附近餐厅（地图默认收起），故 fixture-main 未同步登记。 */
import './styles/map.css'
/* 浮层面板样式（2026-10-01 B 批次①③新增：家庭成员抽屉 + 历史会话侧栏）。
   从 base.css 拆出（base 加完两段越过 1000 行上限）：只定义 .sd-* / .rail-* /
   .tn-railbtn 新类名，与前面所有片选择器不相交，放末层不挪动已有层叠。
   夹具台（fixture-main）不需要它——面板不在夹具台渲染。 */
import './styles/panels.css'
/* 候选卡样式（2026-10-05 新增）。同样只定义 .cand-* 新类名，放末层不挪层叠。
   注意它**必须**同时登记到 fixture-main.tsx：夹具台有 candidates 这个用例，
   漏登记就会重演「夹具几何≠正式页几何」那个坑。 */
import './styles/candidate.css'
/* 身体档案房样式（2026-10-05 新增）。只定义 .pf-* 新类名，与前面所有片不相交，
   所以登记为末层、排在 candidate.css 之后——check-layers 的 f_样式顺序要求
   main.tsx 里的相对顺序与 STYLE_ORDER 完全一致，插在中间会直接判违规。 */
import './styles/profile.css'
/* 冰箱块拍照入口（2026-10-14 新增）。从 profile.css 拆出：那一轮加进来之后
   profile.css 到了 981 行，再加就越过 checkScale 的 1000 行上限（与
   fav.css → fav-cards.css 是同一类拆分）。本片只定义 .pf-fridge-shot* 一族
   新类名，不覆盖 profile.css 的任何既有选择器，所以排它后面即可；
   weekly.css / atmosphere.css 仍是更后的层。
   夹具台（fixture-main）不渲染身体档案房，不需要同步登记这一行。 */
import './styles/profile-fridge.css'
/* 周报房逐餐明细（2026-10-14 新增）。从 panels.css 拆出：那一轮加进来之后
   panels.css 到了 1052 行，越过 checkScale 的 1000 行上限（与 fav.css →
   fav-cards.css 是同一类拆分）。本片只定义 .wk-dish-row / .wk-dish-more /
   .wk-meals / .wk-meal* 这一族新类名，唯一的覆盖是 `.wk-meal-light .wk-pip`
   把 hero 点阵的 13px 缩到 7px——被覆盖的定义在 panels.css 里，所以必须排在
   它之后。atmosphere.css 仍是最末层，故本片排在它前面。
   夹具台（fixture-main）不渲染周报房，不需要同步登记这一行。 */
import './styles/weekly.css'
import './styles/atmosphere.css'
/* 历史会话（2026-10-19 新增）。只定义 .shell-session / .sr-* / .hs-* 三族新类名，
   与前面所有片的选择器不相交，登记末层（atmosphere 之后）不挪动已有层叠。
   内容：左栏常驻会话块（SessionRail portal 落点）+ 历史轮次纵向列表 +
   滚动区/共享书写面/「回到底部」。顺序必须与 main.tsx 里的导入一致。 */
import './styles/session.css'

import Shell from './app/Shell.tsx'
import TonightApp from './app/TonightApp.tsx'
import WeeklyView from './views/WeeklyView.tsx'
import FavView from './views/FavView.tsx'
import ServiceView from './views/ServiceView.tsx'

// 新前端的正式入口（/next.html）。接通检查页在 /check.html，那份不画界面、只看真实返回。
// Shell 是全局导航壳（户型图第 2 步）：它只负责四个房间之间的切换与刷新/后退，
// 今晚房内的三态与成员上下文全部仍在 TonightApp 里，没有动过。
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Shell tonight={<TonightApp />} weekly={<WeeklyView />} fav={<FavView />} svc={<ServiceView />} />
  </StrictMode>,
)
