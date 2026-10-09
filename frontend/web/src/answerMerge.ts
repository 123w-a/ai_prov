import type { ChefAnswer } from './types'

const STREAMED_OPENING_REPLACE_THRESHOLD = 80
const KNOWN_SHORT_OPENING_FALLBACKS = new Set([
  '已按你的要求整理好这一道，完整做法见图卡。',
])
const KNOWN_SHORT_OPENING_PREFIXES = [
  '这次没查到足够具体的做法，我先给你一个稳妥的方向：',
]

export function shouldPreferStreamedOpening(
  streamedText: string | null | undefined,
  opening: string | null | undefined,
): boolean {
  const streamed = (streamedText ?? '').trim()
  if (!streamed) return false
  const current = (opening ?? '').trim()
  if (!current) return true
  if (KNOWN_SHORT_OPENING_FALLBACKS.has(current)) return true
  if (KNOWN_SHORT_OPENING_PREFIXES.some((prefix) => current.startsWith(prefix))) return true
  return streamed.length > current.length + STREAMED_OPENING_REPLACE_THRESHOLD
}

export function applyStreamedOpening(answer: ChefAnswer, streamedText: string): ChefAnswer {
  const streamed = streamedText.trim()
  return shouldPreferStreamedOpening(streamed, answer.opening)
    ? { ...answer, opening: streamed }
    : answer
}

function chooseSyncedOpening(
  localOpening: string | null | undefined,
  serverOpening: string | null | undefined,
): string {
  const local = (localOpening ?? '').trim()
  const server = (serverOpening ?? '').trim()
  return local && shouldPreferStreamedOpening(local, server) ? local : server
}

export function mergeSyncedAnswer(
  localAnswer: ChefAnswer | null | undefined,
  serverAnswer: ChefAnswer | null | undefined,
): ChefAnswer | null | undefined {
  if (!serverAnswer) return localAnswer
  if (!localAnswer) return serverAnswer
  const serverRecipes = serverAnswer.recipes ?? []
  const localRecipes = localAnswer.recipes ?? []
  const recipes = serverRecipes.map((recipe, index) => {
    const localRecipe = localRecipes[index]
    if (!localRecipe) return recipe
    return {
      ...recipe,
      image_url: recipe.image_url ?? localRecipe.image_url ?? null,
      image_ai_generated: recipe.image_ai_generated ?? localRecipe.image_ai_generated ?? false,
      image_note: recipe.image_note || localRecipe.image_note || '',
    }
  })
  return {
    ...serverAnswer,
    opening: chooseSyncedOpening(localAnswer.opening, serverAnswer.opening),
    chef_tip: serverAnswer.chef_tip || localAnswer.chef_tip || '',
    recipes,
    image_url: serverAnswer.image_url ?? localAnswer.image_url ?? null,
    image_ai_generated: serverAnswer.image_ai_generated ?? localAnswer.image_ai_generated ?? false,
    image_note: serverAnswer.image_note || localAnswer.image_note || '',
    image_requested: serverAnswer.image_requested ?? localAnswer.image_requested,
  }
}
