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
/* 服务房样式（第 5 步新增）。同理：只定义 .sv-* 新类名，放末层不挪动已有层叠。 */
import './styles/service.css'
/* 浮层面板样式（2026-10-01 B 批次①③新增：家庭成员抽屉 + 历史会话侧栏）。
   从 base.css 拆出（base 加完两段越过 1000 行上限）：只定义 .sd-* / .rail-* /
   .tn-railbtn 新类名，与前面所有片选择器不相交，放末层不挪动已有层叠。
   夹具台（fixture-main）不需要它——面板不在夹具台渲染。 */
import './styles/panels.css'

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
