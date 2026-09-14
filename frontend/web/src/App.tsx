import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  cancelDecision,
  clearSession,
  createSession,
  deleteMessage,
  deleteSession,
  fetchSessions,
  renameSession,
  sendChat,
  transcribeAudio,
} from './api/client'
import type { ChatImageTarget } from './api/client'
import { ChatArea } from './components/ChatArea'
import { Icon } from './components/Icon'
import { InsightPanel } from './components/InsightPanel'
import { ServicePreview } from './components/ServicePreview'
import { WeeklyReportPage } from './components/WeeklyReportPage'
import { FavoritesPanel } from './components/FavoritesPanel'
import { SessionSidebar } from './components/SessionSidebar'
// P1: sidebar hosts FamilyPanel (member switcher + profiles)
import type {
  ChatMessage,
  ChefAnswer,
  DecisionMode,
  Session,
  WorkspaceView,
} from './types'

// 场景前缀由后端按 mode 处理，前端只传原始文本与 mode。

function parseHistoryAnswer(raw: string | undefined): ChefAnswer | null {
  if (!raw) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && Array.isArray((parsed as ChefAnswer).recipes)) {
      return parsed as ChefAnswer
    }
  } catch {
    // 历史数据可能是旧版纯文本回答。
  }
  return null
}

function answerTextOnly(answer?: ChefAnswer | null): string {
  return (answer?.opening || answer?.chef_tip || '').trim()
}

function sessionToMessages(session: Session): ChatMessage[] {
  const history: ChatMessage[] = []
  for (const record of session.messages ?? []) {
    const answer = parseHistoryAnswer(record.answer)
    history.push({
      id: `user-${session.session_id}-${record.id}`,
      recordId: record.id,
      role: 'user',
      text: record.user_text || (record.image_url ? '上传了一张食材图片' : '（图片）'),
      imageUrl: record.user_image_url !== undefined ? record.user_image_url : record.image_url,
      imageRequested: answer?.image_requested ?? false,
      time: record.time,
    })
    if (record.cancelled || record.answer === '__cancelled__') continue
    const imageCancelled = Boolean(record.image_cancelled)
    const textOnly = imageCancelled
      ? answerTextOnly(answer) ||
        (record.answer && !record.answer.startsWith('{') && record.answer !== '__pending__' ? record.answer : '')
      : ''
    history.push({
      id: `assistant-${session.session_id}-${record.id}`,
      recordId: record.id,
      role: 'assistant',
      text: imageCancelled
        ? textOnly || '已取消配图，本轮没有可保留的文字内容。'
        : answer?.opening || (record.answer === '__pending__' ? '（回答生成中，请稍后刷新查看…）' : record.answer || ''),
      answer: imageCancelled ? null : answer,
      starred: record.starred ?? false,
      feedback: record.feedback ?? null,
      time: record.time,
      imageCancelled,
    })
  }
  return history
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

type PendingImagePatch = {
  images: ImagePatchItem[]
  failedIndexes: number[]
}

type ImagePatchItem = { record_id?: number; index: number; url: string; ai_generated: boolean }

const IMAGE_FAILED_NOTE = '暂无成品图，文字做法完整可照做'
const STRONG_IMAGE_REQUESTS = ['配图', '配张图', '补图', '换图', '换张图', '生成图片', '生成一张图', '来张图', '发图', '发张图', '发图片', '发个图', '出图', '出个图', '带图', '带图片', '有图', '有图片', '看看图', '看看图片', '看图片', '看图', '看一下图', '看一下图片', '看个图', '给我看图', '给我看看', '让我看看', '想看图片', '想看图', '图片欣赏', '成品图', '成品照', '实拍图', '示意图', '效果图', '样图', '参考图', '想看看', '长什么样', '什么样子', '啥样', '样式', '外观', '照片', '实拍', '再来一张', '换一张', '另一张']
const CONTEXTUAL_IMAGE_REFS = ['这道', '这道菜', '这个', '这种', '那种', '这份', '这个方案', '它', '上面', '上一道', '上一道菜', '刚才', '刚刚', '前面', '上一份', '这张']
const IMAGE_SWITCH_WORDS = ['换成', '改成', '做成', '换做', '改做', '改为', '变成']

function hasStrongImageRequest(value: string): boolean {
  return STRONG_IMAGE_REQUESTS.some((phrase) => value.includes(phrase))
}

function isRecipeChangeRequest(value: string): boolean {
  const text = value.trim()
  const broadChangeWords = ['没胃口', '不想吃这个', '不想吃了', '换一道', '换一个', '换别的', '没食欲']
  if (broadChangeWords.some((word) => text.includes(word))) return true
  const changeWords = ['换成', '改成', '做成', '换做', '改做', '改为', '变成']
  const recipeWords = ['面', '汤', '菜', '饭', '粥', '粉', '肉', '鱼', '鸡', '牛', '虾', '豆腐']
  return changeWords.some((word) => text.includes(word)) && recipeWords.some((word) => text.includes(word))
}

function isRecipeConfirmRequest(value: string): boolean {
  const text = value.trim()
  const confirmWords = ['就做', '就吃', '来这个', '做这个', '吃这个', '定这个', '选这个', '就它', '就这道']
  return confirmWords.some((word) => text.includes(word)) || /第[一二三四五六七八九十123456789]\s*道/.test(text)
}

function isContextualImageRequest(value: string, wantImage: boolean): boolean {
  const text = value.trim()
  if (!wantImage && !hasStrongImageRequest(text)) return false
  if (IMAGE_SWITCH_WORDS.some((phrase) => text.includes(phrase))) return false
  if (CONTEXTUAL_IMAGE_REFS.some((phrase) => text.includes(phrase))) return true
  const compact = text
    .replace(/配张图|生成图片|生成一张图|图片欣赏|看看图片|看看图|看图片|看一下图片|看一下图|看个图|给我看图|给我看看|让我看看|成品图|成品照|实拍图|示意图|效果图|样图|参考图|配图|来张图|发图|发张图|发图片|发个图|出图|出个图|图片|照片|实拍|图/g, '')
    .replace(/帮我|给我|请|一下|看看|看下|展示|来展示|欣赏|不满意|不好看|换一张|再来一张|另一张|重新生成|想看看|想看图片|想看图|长什么样|什么样子|啥样|样式|外观/g, '')
    .replace(/[，。！？、,.!?：:\s]/g, '')
  return compact.length === 0
}

function latestAnswerWantsImage(messages: ChatMessage[]): boolean {
  for (const message of [...messages].reverse()) {
    if (message.role !== 'assistant') continue
    if (message.imagePending || message.imageRequested || message.answer?.image_requested || answerHasImage(message.answer)) {
      return true
    }
    if (message.answer?.recipes?.length) return false
  }
  return false
}

function findLatestRecipeImageTarget(messages: ChatMessage[]): ChatImageTarget | null {
  for (const message of [...messages].reverse()) {
    if (message.role !== 'assistant' || !message.answer?.recipes?.length) continue
    const index = message.answer.recipes.findIndex((recipe) => Boolean(recipe.name?.trim()))
    if (index < 0) continue
    const recipe = message.answer.recipes[index]
    return {
      recordId: message.recordId,
      recipeIndex: index,
      dishName: recipe.name.trim(),
    }
  }
  return null
}

function patchAnswerImage(answer: ChefAnswer, img: ImagePatchItem): ChefAnswer {
  const recipes = (answer.recipes ?? []).map((recipe, index) =>
    index === img.index
      ? { ...recipe, image_url: img.url, image_ai_generated: img.ai_generated }
      : recipe,
  )
  const updated: ChefAnswer = {
    ...answer,
    recipes,
    image_requested: true,
  }
  if (img.index === 0) {
    updated.image_url = img.url
    updated.image_ai_generated = img.ai_generated
  }
  return updated
}

function patchAnswerImageFailed(answer: ChefAnswer, indexes: number[]): ChefAnswer {
  const recipes = (answer.recipes ?? []).map((recipe, index) =>
    indexes.includes(index)
      ? { ...recipe, image_note: recipe.image_note || answer.image_note || IMAGE_FAILED_NOTE }
      : recipe,
  )
  const updated: ChefAnswer = { ...answer, recipes, image_requested: true }
  if (indexes.includes(0)) updated.image_note = recipes[0]?.image_note || updated.image_note || IMAGE_FAILED_NOTE
  return updated
}

function answerHasImage(answer?: ChefAnswer | null): boolean {
  if (!answer) return false
  if (answer.image_url) return true
  return (answer.recipes ?? []).some((recipe) => Boolean(recipe.image_url))
}

function mergeSyncedAnswer(localAnswer: ChefAnswer | null | undefined, serverAnswer: ChefAnswer | null | undefined): ChefAnswer | null | undefined {
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
  const merged: ChefAnswer = {
    ...serverAnswer,
    recipes,
    image_url: serverAnswer.image_url ?? localAnswer.image_url ?? null,
    image_ai_generated: serverAnswer.image_ai_generated ?? localAnswer.image_ai_generated ?? false,
    image_note: serverAnswer.image_note || localAnswer.image_note || '',
    image_requested: serverAnswer.image_requested ?? localAnswer.image_requested,
  }
  return merged
}

function mergeSyncedMessage(localMessage: ChatMessage, serverMessage: ChatMessage): ChatMessage {
  if (localMessage.role !== 'assistant') return serverMessage
  if (localMessage.imageCancelled) {
    return {
      ...serverMessage,
      text: localMessage.text || serverMessage.text,
      answer: null,
      imageRequested: false,
      imagePending: false,
      streaming: false,
      stage: undefined,
      imageCancelled: true,
    }
  }
  const serverHasImage = answerHasImage(serverMessage.answer)
  if ((localMessage.streaming || localMessage.imagePending) && !serverHasImage) {
    return localMessage
  }
  const mergedAnswer = mergeSyncedAnswer(localMessage.answer, serverMessage.answer)
  return {
    ...serverMessage,
    answer: mergedAnswer ?? serverMessage.answer,
    imagePending: localMessage.imagePending && !answerHasImage(mergedAnswer ?? serverMessage.answer),
    streaming: serverMessage.streaming ?? localMessage.streaming,
    stage: serverMessage.stage ?? localMessage.stage,
    imageRequested:
      serverMessage.imageRequested ?? localMessage.imageRequested ?? mergedAnswer?.image_requested ?? false,
  }
}

function mergeSyncedMessages(localMessages: ChatMessage[], serverMessages: ChatMessage[]): ChatMessage[] {
  const localById = new Map(localMessages.map((message) => [message.id, message]))
  return serverMessages.map((serverMessage) => {
    const localMessage = localById.get(serverMessage.id)
    return localMessage ? mergeSyncedMessage(localMessage, serverMessage) : serverMessage
  })
}


function hasPendingRecipeImages(answer: ChefAnswer, patch?: PendingImagePatch | null): boolean {
  if (!answer.image_requested) return false
  const failedIndexes = new Set(patch?.failedIndexes ?? [])
  const recipes = answer.recipes ?? []
  if (recipes.length === 0) return false
  return recipes.some((recipe, index) => {
    const imagePatch = patch?.images.find((item) => item.index === index)
    return !recipe.image_url && !imagePatch?.url && !failedIndexes.has(index)
  })
}

function applyPendingImagePatch(answer: ChefAnswer, patch?: PendingImagePatch | null): ChefAnswer {
  if (!patch || (patch.images.length === 0 && patch.failedIndexes.length === 0)) return answer
  const failedIndexes = new Set(patch.failedIndexes)
  const recipes = (answer.recipes ?? []).map((recipe, index) => {
    const imagePatch = patch.images.find((item) => item.index === index)
    const nextRecipe = imagePatch
      ? { ...recipe, image_url: imagePatch.url, image_ai_generated: imagePatch.ai_generated }
      : { ...recipe }
    if (failedIndexes.has(index) && !nextRecipe.image_url) {
      nextRecipe.image_note = nextRecipe.image_note || answer.image_note || IMAGE_FAILED_NOTE
    }
    return nextRecipe
  })

  const next: ChefAnswer = { ...answer, recipes }
  const first = recipes[0]
  if (first) {
    next.image_url = first.image_url ?? answer.image_url ?? null
    next.image_ai_generated = first.image_ai_generated ?? answer.image_ai_generated ?? false
  }
  if (!next.image_note && failedIndexes.has(0) && !next.image_url) {
    next.image_note = IMAGE_FAILED_NOTE
  }
  return next
}

function stripAnswerImages(answer: ChefAnswer): ChefAnswer {
  return {
    ...answer,
    image_url: null,
    image_ai_generated: false,
    image_requested: false,
    image_note: '',
    recipes: (answer.recipes ?? []).map((recipe) => ({
      ...recipe,
      image_url: null,
      image_ai_generated: false,
      image_note: '',
    })),
  }
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messagesBySession, setMessagesBySession] = useState<Record<string, ChatMessage[]>>({})
  const [view, setView] = useState<WorkspaceView>('decision')
  const [sendingSessions, setSendingSessions] = useState<Record<string, true>>({})
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [connection, setConnection] = useState<'checking' | 'online' | 'offline'>('checking')
  const [appError, setAppError] = useState('')
  const bootRef = useRef(false)
  const pendingImagePatchRef = useRef<Record<string, PendingImagePatch>>({})
  const sendingSessionsRef = useRef<Record<string, true>>({})
  const abortControllersRef = useRef<Record<string, AbortController>>({})
  const cancelledImageTurnsRef = useRef<Record<string, true>>({})

  const selectSession = useCallback((session: Session) => {
    setActiveId(session.session_id)
    setMessagesBySession((current) => {
      if (current[session.session_id]) return current
      return { ...current, [session.session_id]: sessionToMessages(session) }
    })
  }, [])

  const refreshSessions = useCallback(async () => {
    try {
      const list = await fetchSessions()
      setSessions(list)
      setConnection('online')
      setAppError('')
      return list
    } catch (error) {
      setConnection('offline')
      throw error
    }
  }, [])

  const syncActiveSession = useCallback(
    async (sessionId: string) => {
      const list = await refreshSessions()
      const current = list.find((session) => session.session_id === sessionId)
      if (current) {
        setMessagesBySession((state) => {
          const serverMessages = sessionToMessages(current)
          const localMessages = state[sessionId] ?? []
          return {
            ...state,
            [sessionId]: localMessages.length > 0
              ? mergeSyncedMessages(localMessages, serverMessages)
              : serverMessages,
          }
        })
      }
      return list
    },
    [refreshSessions],
  )

  const handleNew = useCallback(async () => {
    try {
      const session = await createSession()
      setSessions((current) => [session, ...current])
      setMessagesBySession((current) => ({ ...current, [session.session_id]: [] }))
      selectSession(session)
      setView('decision')
      setSidebarOpen(false)
      setConnection('online')
      setAppError('')
    } catch (error) {
      setConnection('offline')
      setAppError(`新建会话失败：${errorMessage(error)}`)
    }
  }, [selectSession])

  const handleDeleteSession = useCallback(
    async (sessionId: string) => {
      if (!window.confirm('确认删除这个会话？其中的历史记录将无法恢复。')) return
      try {
        await deleteSession(sessionId)
        setMessagesBySession((current) => {
          const next = { ...current }
          delete next[sessionId]
          return next
        })
        const list = await refreshSessions()
        if (activeId !== sessionId) return

        if (list.length > 0) {
          selectSession(list[0])
        } else {
          const freshSession = await createSession()
          setSessions([freshSession])
          selectSession(freshSession)
        }
      } catch (error) {
        setAppError(`删除会话失败：${errorMessage(error)}`)
      }
    },
    [activeId, refreshSessions, selectSession],
  )

  const handleClearSession = useCallback(async () => {
    const activeMessageCount = activeId ? (messagesBySession[activeId]?.length ?? 0) : 0
    if (!activeId || activeMessageCount === 0) return
    if (!window.confirm('清空当前会话中的全部问答？会话本身会保留。')) return
    try {
      await clearSession(activeId)
      await syncActiveSession(activeId)
    } catch (error) {
      setAppError(`清空会话失败：${errorMessage(error)}`)
    }
  }, [activeId, messagesBySession, syncActiveSession])

  const handleDeleteTurn = useCallback(
    async (messageId: number) => {
      if (!activeId) return
      if (!window.confirm('删除这一轮问答？')) return
      try {
        await deleteMessage(activeId, messageId)
        await syncActiveSession(activeId)
      } catch (error) {
        setAppError(`删除问答失败：${errorMessage(error)}`)
      }
    },
    [activeId, messagesBySession, syncActiveSession],
  )

  const handleRenameSession = useCallback(
    async (sessionId: string, title: string) => {
      try {
        await renameSession(sessionId, title)
        await syncActiveSession(sessionId)
      } catch (error) {
        setAppError(`重命名会话失败：${errorMessage(error)}`)
      }
    },
    [syncActiveSession],
  )

  const handleSend = useCallback(
    async (
      text: string,
      image: File | null,
      mode: DecisionMode,
      imagePreview: string | null,
      wantImage: boolean,
      locationContext?: string | null,
    ) => {
      let sessionId = activeId
      if (!sessionId) {
        try {
          const freshSession = await createSession()
          setSessions((current) => [freshSession, ...current])
          setActiveId(freshSession.session_id)
          setMessagesBySession((current) => ({ ...current, [freshSession.session_id]: [] }))
          sessionId = freshSession.session_id
        } catch (error) {
          setAppError(`无法开始对话：${errorMessage(error)}`)
          return
        }
      }
      if (sendingSessionsRef.current[sessionId]) return

      const userId = crypto.randomUUID()
      const assistantId = crypto.randomUUID()
      const displayText = text || '请根据这张图片帮我做膳食决策'
      const visibleMessages = messagesBySession[sessionId] ?? []
      const userMessage: ChatMessage = {
        id: userId,
        role: 'user',
        text: displayText,
        imageUrl: imagePreview,
        imageRequested: wantImage,
      }
      const explicitImageRequest = wantImage || hasStrongImageRequest(displayText)
      const inheritsImageRequest = isRecipeChangeRequest(displayText) && latestAnswerWantsImage(visibleMessages)
      const turnWantsImage = explicitImageRequest || inheritsImageRequest || isRecipeChangeRequest(displayText) || isRecipeConfirmRequest(displayText)
      const imageTarget = isContextualImageRequest(displayText, explicitImageRequest)
        ? findLatestRecipeImageTarget(visibleMessages)
        : null
      const assistantMessage: ChatMessage = {
        id: assistantId,
        role: 'assistant',
        text: '',
        streaming: true,
        stage: 'thinking',
        imageRequested: turnWantsImage,
      }
      setMessagesBySession((current) => {
        const nextMessages = current[sessionId!] ? [...current[sessionId!]] : []
        nextMessages.push(userMessage, assistantMessage)
        return { ...current, [sessionId!]: nextMessages }
      })
      if (turnWantsImage) {
        pendingImagePatchRef.current[assistantId] = { images: [], failedIndexes: [] }
      }
      const abortController = new AbortController()
      abortControllersRef.current[assistantId] = abortController
      sendingSessionsRef.current[sessionId] = true
      setSendingSessions((current) => ({ ...current, [sessionId!]: true }))
      setAppError('')

      try {
        await sendChat(sessionId, displayText, image, mode, explicitImageRequest, locationContext ?? null, imageTarget, {
          onStage: (stage) =>
            cancelledImageTurnsRef.current[assistantId]
              ? undefined
              :
            setMessagesBySession((current) => ({
              ...current,
              [sessionId!]: (current[sessionId!] ?? []).map((message) =>
                message.id === assistantId
                  ? { ...message, stage: stage as ChatMessage['stage'] }
                  : message,
              ),
            })),
          onWorking: () =>
            cancelledImageTurnsRef.current[assistantId]
              ? undefined
              :
            setMessagesBySession((current) => ({
              ...current,
              [sessionId!]: (current[sessionId!] ?? []).map((message) =>
                message.id === assistantId ? { ...message, stage: 'thinking' } : message,
              ),
            })),
          onToken: (token) =>
            cancelledImageTurnsRef.current[assistantId]
              ? undefined
              :
            setMessagesBySession((current) => ({
              ...current,
              [sessionId!]: (current[sessionId!] ?? []).map((message) =>
                message.id === assistantId
                  ? { ...message, text: message.text + token, stage: 'writing' }
                  : message,
              ),
            })),
          onStructuring: () =>
            cancelledImageTurnsRef.current[assistantId]
              ? undefined
              :
            setMessagesBySession((current) => ({
              ...current,
              [sessionId!]: (current[sessionId!] ?? []).map((message) =>
                message.id === assistantId ? { ...message, stage: 'structuring' } : message,
              ),
            })),
          onHeartbeat: (elapsed) =>
            cancelledImageTurnsRef.current[assistantId]
              ? undefined
              :
            setMessagesBySession((current) => ({
              ...current,
              [sessionId!]: (current[sessionId!] ?? []).map((message) =>
                message.id === assistantId ? { ...message, elapsed } : message,
              ),
            })),
          onAnswer: (answer) =>
            cancelledImageTurnsRef.current[assistantId]
              ? undefined
              :
            setMessagesBySession((current) => ({
              ...current,
              [sessionId!]: (current[sessionId!] ?? []).map((message) => {
                if (message.id !== assistantId) return message
                const answerWantsImage = Boolean(answer.image_requested ?? turnWantsImage)
                const patchedAnswer = answerWantsImage
                  ? applyPendingImagePatch(answer, pendingImagePatchRef.current[assistantId])
                  : stripAnswerImages(answer)
                const imagePending = answerWantsImage && hasPendingRecipeImages(patchedAnswer, pendingImagePatchRef.current[assistantId])
                return {
                  ...message,
                  text: '',
                  answer: patchedAnswer,
                  streaming: false,
                  imagePending,
                  stage: imagePending ? 'generating_image' : undefined,
                  imageRequested: answerWantsImage,
                }
              }),
            })),
          onImage: (img) => {
            if (cancelledImageTurnsRef.current[assistantId]) return
            if (img.turn_id && img.turn_id !== assistantId) return
            const targetRecordId = imageTarget?.recordId
            if (targetRecordId != null && img.record_id !== targetRecordId) return
            setMessagesBySession((current) => ({
              ...current,
              [sessionId!]: (current[sessionId!] ?? []).map((message) => {
                const isTarget = targetRecordId != null
                  ? message.recordId === targetRecordId
                  : message.id === assistantId
                if (!isTarget || !message.answer) return message
                const updated = patchAnswerImage(message.answer, img)
                return { ...message, answer: updated, imagePending: false, stage: undefined }
              }),
            }))
            if (targetRecordId != null) return
            const existingPatch = pendingImagePatchRef.current[assistantId] ?? { images: [], failedIndexes: [] }
            existingPatch.images = [
              ...existingPatch.images.filter((item) => item.index !== img.index),
              img,
            ]
            pendingImagePatchRef.current[assistantId] = existingPatch
            setMessagesBySession((current) => ({
              ...current,
              [sessionId!]: (current[sessionId!] ?? []).map((message) => {
                if (message.id !== assistantId) return message
                if (!message.answer) {
                  return message
                }
                const updated = patchAnswerImage(message.answer, img)
                const patch = pendingImagePatchRef.current[assistantId]
                const imagePending = hasPendingRecipeImages(updated, patch)
                return { ...message, answer: updated, imagePending, stage: imagePending ? 'generating_image' : undefined }
              }),
            }))
          },
          onImageFailed: (payload) => {
            if (cancelledImageTurnsRef.current[assistantId]) return
            if (payload.turn_id && payload.turn_id !== assistantId) return
            const targetRecordId = imageTarget?.recordId
            if (targetRecordId != null && payload.record_id !== targetRecordId) return
            setMessagesBySession((current) => ({
              ...current,
              [sessionId!]: (current[sessionId!] ?? []).map((message) => {
                const isTarget = targetRecordId != null
                  ? message.recordId === targetRecordId
                  : message.id === assistantId
                if (!isTarget || !message.answer) return message
                const updated = patchAnswerImageFailed(message.answer, payload.indexes)
                return { ...message, answer: updated, imagePending: false, stage: undefined }
              }),
            }))
            if (targetRecordId != null) return
            const existingPatch = pendingImagePatchRef.current[assistantId] ?? { images: [], failedIndexes: [] }
            existingPatch.failedIndexes = Array.from(new Set([...existingPatch.failedIndexes, ...payload.indexes]))
            pendingImagePatchRef.current[assistantId] = existingPatch
            setMessagesBySession((current) => ({
              ...current,
              [sessionId!]: (current[sessionId!] ?? []).map((message) => {
                if (message.id !== assistantId) return message
                if (!message.answer) {
                  return message
                }
                const updated = patchAnswerImageFailed(message.answer, payload.indexes)
                const patch = pendingImagePatchRef.current[assistantId]
                const imagePending = hasPendingRecipeImages(updated, patch)
                return { ...message, answer: updated, imagePending, stage: imagePending ? 'generating_image' : undefined }
              }),
            }))
          },
          onFinish: (payload) => {
            if (cancelledImageTurnsRef.current[assistantId]) return
            setMessagesBySession((current) => ({
              ...current,
              [sessionId!]: (current[sessionId!] ?? []).map((message) =>
                message.id === assistantId && message.answer
                  ? (() => {
                      const answerWantsImage = Boolean(message.answer?.image_requested ?? turnWantsImage)
                      const hasImage = answerHasImage(message.answer)
                      return {
                        ...message,
                        recordId: message.recordId ?? payload?.record_id,
                        streaming: false,
                        imagePending: answerWantsImage && !hasImage ? true : false,
                        stage: answerWantsImage && !hasImage ? 'generating_image' : undefined,
                      }
                    })()
                  : message.id === assistantId
                    ? { ...message, recordId: message.recordId ?? payload?.record_id, streaming: false, imagePending: false, stage: undefined }
                  : message,
              ),
            }))
          },
        }, abortController.signal, assistantId)
        window.setTimeout(() => {
          void syncActiveSession(sessionId)
        }, 1800)
        window.setTimeout(() => {
          void syncActiveSession(sessionId)
        }, 6000)
        await syncActiveSession(sessionId)
      } catch (error) {
        const wasCancelled = cancelledImageTurnsRef.current[assistantId]
        const isAbortError = error instanceof DOMException && error.name === 'AbortError'
        if (wasCancelled || isAbortError) {
          return
        }
        const detail = errorMessage(error)
        setMessagesBySession((current) => ({
          ...current,
          [sessionId!]: (current[sessionId!] ?? []).map((message) =>
            message.id === assistantId
              ? {
                  ...message,
                  text: `${message.text}${message.text ? '\n\n' : ''}本轮未能完成：${detail}`,
                  streaming: false,
                  stage: undefined,
                  error: true,
                }
              : message,
          ),
        }))
        setAppError(`膳食决策未完成：${detail}`)
      } finally {
        delete pendingImagePatchRef.current[assistantId]
        delete abortControllersRef.current[assistantId]
        delete cancelledImageTurnsRef.current[assistantId]
        if (sessionId) {
          delete sendingSessionsRef.current[sessionId]
          setSendingSessions((current) => {
            const next = { ...current }
            delete next[sessionId]
            return next
          })
        }
      }
    },
    [activeId, syncActiveSession],
  )

  const handleCancelDecision = useCallback(async (): Promise<'cancelled' | 'kept_text' | null> => {
    if (!activeId) return null
    const sessionId = activeId
    const currentAssistant = [...(messagesBySession[sessionId] ?? [])].reverse().find(
      (message) =>
        message.role === 'assistant' && (message.streaming || message.imagePending),
    )
    if (!currentAssistant) return null
    const keepText = Boolean(currentAssistant.imagePending)
    const confirmText = keepText
      ? '确定取消图片和菜谱卡片，只保留文字吗？'
      : '确定取消本次回答吗？'
    if (!window.confirm(confirmText)) return null

    cancelledImageTurnsRef.current[currentAssistant.id] = true
    abortControllersRef.current[currentAssistant.id]?.abort()
    delete pendingImagePatchRef.current[currentAssistant.id]
    setMessagesBySession((current) => ({
      ...current,
      [sessionId]: keepText
        ? (current[sessionId] ?? []).map((message) => {
            if (message.id !== currentAssistant.id) return message
            return {
              ...message,
              answer: null,
              imageRequested: false,
              imagePending: false,
              streaming: false,
              stage: undefined,
              text:
                answerTextOnly(message.answer) ||
                message.text ||
                '已取消配图，本轮没有可保留的文字内容。',
              imageCancelled: true,
            }
          })
        : (current[sessionId] ?? []).filter((message) => message.id !== currentAssistant.id),
    }))
    await cancelDecision(sessionId, currentAssistant.id, keepText).catch(() => {})
    return keepText ? 'kept_text' : 'cancelled'
  }, [activeId, messagesBySession])

  useEffect(() => {
    if (bootRef.current) return
    bootRef.current = true
    void (async () => {
      try {
        const list = await refreshSessions()
        if (list.length > 0) {
          selectSession(list[0])
        } else {
          const session = await createSession()
          setSessions([session])
          selectSession(session)
          setConnection('online')
        }
      } catch (error) {
        setConnection('offline')
        setAppError(`无法连接后端服务：${errorMessage(error)}`)
      }
    })()
  }, [refreshSessions, selectSession])

  const activeSession = useMemo(() => sessions.find((session) => session.session_id === activeId) ?? null, [activeId, sessions])
  const messages = useMemo(() => {
    if (!activeId) return []
    const local = messagesBySession[activeId]
    if (local) return local
    return activeSession ? sessionToMessages(activeSession) : []
  }, [activeId, activeSession, messagesBySession])

  const latestAnswer = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index].answer) return messages[index].answer ?? null
    }
    return null
  }, [messages])

  const pageTitle =
    view === 'decision'
      ? '今日膳食决策'
      : view === 'weekly'
        ? '本周饮食周报'
        : view === 'favorites'
          ? '我的收藏'
          : '上门私厨预演'
  const pageDescription =
    view === 'decision'
      ? '健康优先的 AI 膳食工作台'
      : view === 'weekly'
        ? '近 7 天决策的健康小结'
        : view === 'favorites'
          ? '点过 ★ 的好菜都在这里'
          : '查看真实可用能力与远期服务边界'

  return (
    <div className="app-shell">
      <SessionSidebar
        sessions={sessions}
        activeId={activeId}
        view={view}
        open={sidebarOpen}
        connection={connection}
        onViewChange={setView}
        onSelect={selectSession}
        onRename={handleRenameSession}
        onNew={() => void handleNew()}
        onDelete={(sessionId) => void handleDeleteSession(sessionId)}
        onClose={() => setSidebarOpen(false)}
      />

      <button
        type="button"
        className={sidebarOpen ? 'sidebar-overlay show' : 'sidebar-overlay'}
        aria-label="关闭导航"
        onClick={() => setSidebarOpen(false)}
      />

      <div className="main-shell">
        <header className="global-header">
          <button
            type="button"
            className="icon-btn mobile-menu"
            aria-label="打开导航"
            onClick={() => setSidebarOpen(true)}
          >
            <Icon name="menu" />
          </button>
          <div className="global-title">
            <span>{pageDescription}</span>
            <h1>{pageTitle}</h1>
          </div>
          <div className="global-status">
            <span className={`status-chip ${connection}`}>
              <i aria-hidden="true" />
              {connection === 'online' ? '服务在线' : connection === 'offline' ? '连接异常' : '连接中'}
            </span>
            <span className="date-chip">健康链透明可追溯</span>
          </div>
        </header>

        {appError && (
          <div className="app-alert" role="alert">
            <Icon name="warning" size={18} />
            <span>{appError}</span>
            <button type="button" className="icon-btn" aria-label="关闭错误提示" onClick={() => setAppError('')}>
              <Icon name="close" size={17} />
            </button>
          </div>
        )}

        {view === 'decision' ? (
          <div className="decision-layout">
            <ChatArea
              activeTitle={activeSession?.title || '新的膳食决策'}
              activeSessionId={activeSession?.session_id ?? null}
              onRenameTitle={(title) => {
                if (activeSession) void handleRenameSession(activeSession.session_id, title)
              }}
              messages={messages}
              sending={Boolean(activeId && sendingSessions[activeId])}
              onSend={(text, image, mode, preview, wantImage, locationContext) =>
                void handleSend(text, image, mode, preview, wantImage, locationContext)
              }
              onCancelDecision={handleCancelDecision}
              onClear={() => void handleClearSession()}
              onDeleteTurn={(messageId) => void handleDeleteTurn(messageId)}
              onTranscribe={transcribeAudio}
            />
            <InsightPanel answer={latestAnswer} />
          </div>
        ) : view === 'weekly' ? (
          <WeeklyReportPage />
        ) : view === 'favorites' ? (
          <FavoritesPanel
            onOpenSession={(sid) => {
              setView('decision')
              const target = sessions.find((s) => s.session_id === sid)
              if (target) selectSession(target)
            }}
          />
        ) : (
          <ServicePreview />
        )}
      </div>
    </div>
  )
}
