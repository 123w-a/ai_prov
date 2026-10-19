import type { RunState } from '../data/model.ts'
import type { ResultVM } from '../data/viewModel.ts'
import { buildResultVM, isShown } from '../data/viewModel.ts'
import { guardText } from '../data/clean.ts'
import { DishHero } from './DishHero.tsx'
import { NutritionTable } from './NutritionTable.tsx'
import { Steps } from './Steps.tsx'
import { ExtraRecipes } from './ExtraRecipes.tsx'

/**
 * 等待页里的**活卡片**（2026-10-19 P0：answer 到达即渲染，不再等 finish 揭幕）。
 *
 * 后端的时序是「正文 token → structuring → answer →（补图线程，最长约 25s）→ finish」。
 * 在这之前，answer 一到就要让用户看见卡片——他要的东西已经算出来了，
 * 没有任何理由让他盯着「正在生成中」再等半分钟。三态：
 *   · structuring 到了、answer 未到 —— 卡片占位（静态空卡，不画进度）；
 *   · answer 到了 —— 成卡（与结果页同一套块：菜照+营养表+做法+其余菜）；
 *   · image / image_failed —— 贴图或补失败说明，都由调用方先写进 answer 再传进来。
 *
 * 复用结果页的同一套块（DishHero/NutritionTable/Steps/ExtraRecipes）而不是另画一种卡：
 * finish 时那一下从等待页到结果页是**同一张卡的重排**，不是换了一张脸。
 * 右栏（营养灯/护栏/依据）不在这里画——它们是"为什么适合"的审阅面，
 * 留给结果页；等待页先给结论（菜与做法），与结果页"首屏给结论"同一逻辑。
 */

function CardSlot() {
  return (
    <section className="wait-card-slot" aria-busy="true">
      <p className="wait-card-slot-h">正在整理卡片…</p>
    </section>
  )
}

export function WaitAnswer({ run }: { run: RunState }) {
  const a = run.answer
  const structured = Boolean(a && Array.isArray(a.recipes) && a.recipes.length > 0)

  if (!structured || !a) {
    // 占位触发源两个都认（2026-10-19 实测校准）：
    //   · stage:'structuring' —— 真正的窗口在它这里：实测「整理」阶段到 answer
    //     有 5.7s（14.8→20.5），这 5.7 秒就该显示占位；
    //   · 裸 structuring 事件 —— 后端明说「正在整理卡片」，但它与 answer 背靠背
    //     （实测 0.1s），只认它等于没有占位。
    // 候选轮也会带 stage:'structuring'，但那轮结束前只剩 ~0.1s（实测），
    // 闪一下就进结果页，不会把「正在整理卡片」挂成空头支票。
    const structuring =
      run.cardPending || run.events.some((e) => e.stage === 'structuring')
    return structuring ? <CardSlot /> : null
  }

  // 用时传 null：这一轮还没跑完，写 0 秒是编账目（与收藏恢复态同一判据）。
  const vm: ResultVM = buildResultVM(a, run.body, run.request, null, guardText)

  return (
    <section className="wait-answer">
      <DishHero vm={vm}>
        <NutritionTable vm={vm} />
      </DishHero>
      {isShown(vm.steps) && (
        <Steps steps={vm.steps.data} seasonings={vm.seasonings} ingredients={vm.ingredients} />
      )}
      {isShown(vm.rest) && <ExtraRecipes recipes={vm.rest.data} />}
    </section>
  )
}
