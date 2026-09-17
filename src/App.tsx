import { useEffect, useMemo, useRef, useState, type DragEvent as ReactDragEvent, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { relaunch } from '@tauri-apps/plugin-process'
import { check, type Update } from '@tauri-apps/plugin-updater'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import {
  ArrowLeft, BookOpen, Check, ChevronLeft, ChevronRight, CircleHelp, FileText, Folder, FolderOpen,
  FolderPlus, Library, Moon, MoreHorizontal, Plus, Search, Settings, Sparkles, Sun,
  Download, RefreshCw, RotateCcw, Trash2, Upload, X, ZoomIn, ZoomOut,
} from 'lucide-react'
import { papers as seedPapers } from './lib/mockData'
import type { Paper, PaperFolder, ParserResponse, RenderResponse, StoredPaperResponse, SymbolDefinition, View } from './lib/types'
import { getCopy, type Language } from './lib/i18n'
import { displayAuthors } from './lib/authorDisplay'

const formula = 'V ≈ WH'

const isDesktop = () => Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)

const QWEN_CHAT_ENDPOINT = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
const DEEPSEEK_CHAT_ENDPOINT = 'https://api.deepseek.com/chat/completions'
const SIDEBAR_MIN_WIDTH = 180
const SIDEBAR_MAX_WIDTH = 420
const SIDEBAR_DEFAULT_WIDTH = 242

type ModelProvider = 'qwen' | 'deepseek' | 'custom'
type ModelConfig = { provider: ModelProvider; label: string; endpoint: string; model: string }
type ProviderApiKeyStatus = Record<ModelProvider, boolean>
type ProviderApiKeyDrafts = Record<ModelProvider, string>

const EMPTY_PROVIDER_API_KEY_STATUS: ProviderApiKeyStatus = { qwen: false, deepseek: false, custom: false }
const EMPTY_PROVIDER_API_KEY_DRAFTS: ProviderApiKeyDrafts = { qwen: '', deepseek: '', custom: '' }
const MASKED_API_KEY = '••••••••••••••••'
const API_KEY_STORAGE_KEYS: Record<ModelProvider, string> = {
  qwen: 'phireader.apiKey.qwen',
  deepseek: 'phireader.apiKey.deepseek',
  custom: 'phireader.apiKey.custom',
}

const PROVIDER_MODELS: Record<Exclude<ModelProvider, 'custom'>, string[]> = {
  // Keep provider catalogs isolated.  A model hosted by DashScope is not a
  // model exposed by the native DeepSeek endpoint, even if its name contains
  // "deepseek".
  // Keep the model supplied by the user first, followed by the current models
  // listed in each provider's official model catalog.
  qwen: ['qwen3.7-flash', 'qwen3.8-flash', 'qwen3.8-max', 'qwen-plus'],
  deepseek: ['deepseek-flash', 'deepseek-v4-pro'],
}

type MeaningUpdate = { id: string; meaning: string }
type SummaryResult = { updates: MeaningUpdate[]; failedBatches: number; firstError: string }
type BatchDeleteResponse = {
  deletedIds: string[]
  failures: Array<{ paperId: string; error: string }>
}
type QueuedImport = { file: File; id: string }
const SUMMARY_BATCH_SIZE = 16

function isPdfFile(file: File): boolean {
  return file.name.toLowerCase().endsWith('.pdf') || file.type.toLowerCase() === 'application/pdf'
}

function isPlaceholderTitle(value: string): boolean {
  const normalized = value.trim().toLowerCase()
  return !normalized || normalized.endsWith('.pdf') || ['paper', 'untitled', 'document', 'microsoft word'].includes(normalized)
}

function parsedTitleOrFallback(value: string | undefined, fallback: string): string {
  const candidate = value?.trim() ?? ''
  return isPlaceholderTitle(candidate) ? fallback : candidate
}

function clampSidebarWidth(value: number): number {
  return Math.round(Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Number.isFinite(value) ? value : SIDEBAR_DEFAULT_WIDTH)))
}

function summaryContentText(content: unknown): string {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content.map((part) => {
    if (typeof part === 'string') return part
    if (!part || typeof part !== 'object') return ''
    const text = 'text' in part && typeof part.text === 'string' ? part.text : ''
    return text
  }).join('')
}

function summaryRows(content: string, expectedIds: Set<string>, language: Language): Array<{ id: string; meaning: string }> {
  const jsonText = content.trim()
  let parsed: unknown
  try { parsed = JSON.parse(jsonText) } catch { throw new Error('返回内容不是合法 JSON') }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('返回内容必须是 JSON 对象')
  const record = parsed as Record<string, unknown>
  const items = record.items
  if (!Array.isArray(items)) throw new Error('返回 JSON 缺少 items 数组')
  if (items.length !== expectedIds.size) throw new Error(`摘要数量不匹配：期望 ${expectedIds.size}，实际 ${items.length}`)
  const seen = new Set<string>()
  const rows: Array<{ id: string; meaning: string }> = []
  for (const item of items) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) throw new Error('items 中存在无效项')
    const row = item as Record<string, unknown>
    if (typeof row.id !== 'string' || typeof row.meaning !== 'string') throw new Error('摘要项必须包含字符串 id 和 meaning')
    const meaning = row.meaning.replace(/\s+/g, ' ').trim()
    if (!expectedIds.has(row.id)) throw new Error(`返回了未知符号 id：${row.id}`)
    if (seen.has(row.id)) throw new Error(`符号 id 重复：${row.id}`)
    if (!meaning || meaning.length > 240) throw new Error(`摘要长度无效：${row.id}`)
    if (language === 'zh-CN' && meaning.length > 24) throw new Error(`中文摘要不能超过 24 字：${row.id}`)
    seen.add(row.id)
    rows.push({ id: row.id, meaning })
  }
  if (seen.size !== expectedIds.size) throw new Error('返回结果未覆盖全部符号')
  return rows
}

type CompletionContent = { content: string; reasoning: string }

function completionContent(raw: string): CompletionContent {
  const trimmed = raw.trim()
  if (!trimmed) return { content: '', reasoning: '' }
  try {
    const payload = JSON.parse(trimmed) as { choices?: Array<{ message?: { content?: unknown; reasoning_content?: unknown }; delta?: { content?: unknown; reasoning_content?: unknown } }> }
    const choice = payload.choices?.[0]
    return {
      content: summaryContentText(choice?.message?.content ?? choice?.delta?.content),
      reasoning: summaryContentText(choice?.message?.reasoning_content ?? choice?.delta?.reasoning_content),
    }
  } catch {
    // Streaming OpenAI-compatible APIs return Server-Sent Events.  Supporting
    // them here also covers DeepSeek's reasoning models, which may emit an
    // empty content field while the reasoning_content chunks are arriving.
    let content = ''
    let reasoning = ''
    for (const line of raw.split(/\r?\n/)) {
      const data = line.trim().startsWith('data:') ? line.trim().slice(5).trim() : ''
      if (!data || data === '[DONE]') continue
      try {
        const payload = JSON.parse(data) as { choices?: Array<{ delta?: { content?: unknown; reasoning_content?: unknown }; message?: { content?: unknown; reasoning_content?: unknown } }> }
        const choice = payload.choices?.[0]
        content += summaryContentText(choice?.delta?.content ?? choice?.message?.content)
        reasoning += summaryContentText(choice?.delta?.reasoning_content ?? choice?.message?.reasoning_content)
      } catch {
        // Ignore keep-alive/comment lines and malformed partial SSE frames.
      }
    }
    return { content, reasoning }
  }
}

async function summarizeSymbols(config: ModelConfig, language: Language, symbols: SymbolDefinition[]): Promise<SummaryResult> {
  const updates: MeaningUpdate[] = []
  let failedBatches = 0
  let firstError = ''
  const languageInstruction = language === 'en' ? '用 English 输出摘要。' : '用简体中文输出摘要。'
  for (let offset = 0; offset < symbols.length; offset += SUMMARY_BATCH_SIZE) {
    const chunk = symbols.slice(offset, offset + SUMMARY_BATCH_SIZE)
    const prompt = [
      '你是学术论文符号助手。根据每个符号的作者定义原文，为它写一个简短、准确的摘要。',
      languageInstruction,
      language === 'en' ? '只输出 JSON 对象，格式为 {"items":[{"id":"原样保留","meaning":"a short English phrase"}]}。' : '只输出 JSON 对象，格式为 {"items":[{"id":"原样保留","meaning":"简短中文术语或短语，不超过24字"}]}。',
      '必须为输入数组中的每个 id 返回且仅返回一项，id 必须逐字复制；不要添加原文没有的事实，不要解释过程，不要使用 Markdown。',
      JSON.stringify(chunk.map((symbol) => ({ id: symbol.id, symbol: symbol.surface, definition: symbol.definition.slice(0, 1200) }))),
    ].join('\n')
    try {
      const requestBody: Record<string, unknown> = {
        model: config.model,
        // Both providers document JSON mode for their OpenAI-compatible
        // Chat Completions endpoint.  Keep the response non-streaming so the
        // final content can be validated as one complete JSON document; the
        // parser still accepts SSE for compatible gateways.
        stream: false,
        response_format: { type: 'json_object' },
        messages: [
          { role: 'system', content: `你只返回包含 items 数组的合法 JSON 对象，不要输出 Markdown 代码围栏。${languageInstruction}` },
          { role: 'user', content: prompt },
        ],
      }
      if (config.provider === 'qwen') {
        // Qwen's thinking switch is a top-level field in the raw HTTP JSON
        // request (the SDK exposes the same field through extra_body).
        // Disable it for this structured, short summary so <think> content
        // cannot contaminate the JSON response.
        requestBody.enable_thinking = false
        // DashScope's current OpenAI-compatible documentation marks
        // max_tokens as deprecated for new integrations.
        requestBody.max_completion_tokens = 4096
        requestBody.temperature = 0.1
      } else if (config.provider === 'deepseek') {
        // DeepSeek documents an explicit thinking block.  These summaries are
        // short and do not need chain-of-thought, so disable it explicitly
        // instead of relying on the provider default (which is enabled).
        // Keep reasoning_content isolated in completionContent in case a
        // compatible gateway still sends it; it is never parsed as the answer.
        requestBody.thinking = { type: 'disabled' }
        requestBody.max_tokens = 4096
        requestBody.temperature = 0.1
      } else {
        requestBody.max_tokens = 4096
        requestBody.temperature = 0.1
      }
      const rawResponse = await invoke<string>('request_model_completion', {
        provider: config.provider,
        customEndpoint: config.endpoint,
        body: requestBody,
      })
      const completion = completionContent(rawResponse)
      if (!completion.content) {
        throw new Error(completion.reasoning ? '模型只返回了思维内容，未返回最终答案' : '模型未返回 content')
      }
      const rows = summaryRows(completion.content, new Set(chunk.map((symbol) => symbol.id)), language)
      for (const row of rows) updates.push({ id: row.id, meaning: row.meaning })
    } catch (error) {
      failedBatches += 1
      if (!firstError) {
        firstError = error instanceof Error ? error.message : String(error)
      }
    }
  }
  return { updates, failedBatches, firstError }
}

function storedPaperToPaper(stored: StoredPaperResponse): Paper {
  const symbols = stored.symbols.map((symbol) => {
    // The parser persists snake_case JSON while the frontend uses camelCase.
    // Normalize both forms here so clicks on rendered PDF occurrences keep
    // working for newly parsed and previously stored papers.
    const parsedSymbol = symbol as SymbolDefinition & { identity_key?: string }
    return {
      ...symbol,
      identityKey: symbol.identityKey ?? parsedSymbol.identity_key,
      status: symbol.status === 'unresolved' ? 'review' as const : symbol.status,
    }
  })
  const metadataTitle = parsedTitleOrFallback(stored.metadata.title, '未命名论文')
  return {
    id: stored.paperId,
    title: parsedTitleOrFallback(stored.title, metadataTitle),
    authors: stored.metadata.authors || '作者信息未提供',
    venue: stored.metadata.producer || '本地论文 · PDF',
    year: stored.metadata.year || '—',
    pages: stored.metadata.pages || 0,
    size: `${Math.max(1, Math.round(stored.metadata.file_size / 1024))} KB`,
    accent: '#d58bff',
    progress: 100,
    addedAt: '本地论文库',
    abstract: symbols.length ? '本地解析已完成。侧栏将显示正文中作者定义过的符号。' : '本地解析完成，但没有找到可确认的作者定义符号。',
    symbols,
    filePath: stored.paperPath,
    parsePath: stored.parsePath,
    candidateCount: stored.totals.candidate_chars,
    visualReviewItems: stored.totals.visual_review_items,
    reviewSymbolCount: stored.totals.review_symbols,
    visualReviewQueue: stored.visualReviewQueue,
    parseStatus: 'ready',
  }
}

function App() {
  const [view, setView] = useState<View>('library')
  const [papers, setPapers] = useState<Paper[]>(() => isDesktop() ? [] : seedPapers)
  const [folders, setFolders] = useState<PaperFolder[]>([])
  const [activeFolderId, setActiveFolderId] = useState<string | null>(null)
  const [folderPaper, setFolderPaper] = useState<Paper | null>(null)
  const [folderDraftIds, setFolderDraftIds] = useState<Set<string>>(new Set())
  const [folderSaving, setFolderSaving] = useState(false)
  const [activePaperId, setActivePaperId] = useState(() => isDesktop() ? '' : seedPapers[0].id)
  const [activeSymbol, setActiveSymbol] = useState<SymbolDefinition | null>(() => isDesktop() ? null : seedPapers[0].symbols[1])
  const [readingPages, setReadingPages] = useState<Record<string, number>>({})
  const [query, setQuery] = useState('')
  const [selectedPaperIds, setSelectedPaperIds] = useState<Set<string>>(() => new Set())
  const [dark, setDark] = useState(() => localStorage.getItem('phireader.dark') !== 'false')
  const [fontSize, setFontSize] = useState(() => Number(localStorage.getItem('phireader.fontSize')) || 17)
  const [modelProvider, setModelProvider] = useState<ModelProvider>(() => {
    const stored = localStorage.getItem('phireader.modelProvider')
    return stored === 'deepseek' || stored === 'custom' ? stored : 'qwen'
  })
  const [modelName, setModelName] = useState(() => {
    const stored = localStorage.getItem('phireader.modelName') ?? ''
    const options = PROVIDER_MODELS[modelProvider === 'custom' ? 'qwen' : modelProvider]
    return options.includes(stored) ? stored : options[0]
  })
  const [customModelName, setCustomModelName] = useState(() => localStorage.getItem('phireader.customModelName') ?? '')
  const [customEndpoint, setCustomEndpoint] = useState(() => localStorage.getItem('phireader.customEndpoint') ?? '')
  const [interfaceLanguage, setInterfaceLanguage] = useState<Language>(() => {
    const stored = localStorage.getItem('phireader.interfaceLanguage')
      ?? localStorage.getItem('phireader.language')
      ?? localStorage.getItem('phireader.summaryLanguage')
    return stored === 'en' ? 'en' : 'zh-CN'
  })
  const [summaryLanguage, setSummaryLanguage] = useState<Language>(() => {
    const stored = localStorage.getItem('phireader.summaryLanguage')
      ?? localStorage.getItem('phireader.language')
      ?? localStorage.getItem('phireader.interfaceLanguage')
    return stored === 'en' ? 'en' : 'zh-CN'
  })
  const [summaryBusyPaperId, setSummaryBusyPaperId] = useState('')
  const importingIds = useRef(new Set<string>())
  const importQueue = useRef<Promise<void>>(Promise.resolve())
  const apiKeySaveTimers = useRef<Partial<Record<ModelProvider, number>>>({})
  const apiKeyDraftsRef = useRef<ProviderApiKeyDrafts>({ ...EMPTY_PROVIDER_API_KEY_DRAFTS })
  const dragDepth = useRef(0)
  const activePaperIdRef = useRef(activePaperId)
  const [toast, setToast] = useState('')
  const [dragActive, setDragActive] = useState(false)
  const [dragFileCount, setDragFileCount] = useState(0)
  const fileRef = useRef<HTMLInputElement>(null)
  const [sidebarWidth, setSidebarWidth] = useState(() => clampSidebarWidth(Number(localStorage.getItem('phireader.sidebarWidth')) || SIDEBAR_DEFAULT_WIDTH))
  const [sidebarResizing, setSidebarResizing] = useState(false)
  const [providerApiKeyStatus, setProviderApiKeyStatus] = useState<ProviderApiKeyStatus>({ ...EMPTY_PROVIDER_API_KEY_STATUS })
  const [providerApiKeyDrafts, setProviderApiKeyDrafts] = useState<ProviderApiKeyDrafts>({ ...EMPTY_PROVIDER_API_KEY_DRAFTS })
  const [availableUpdate, setAvailableUpdate] = useState<Update | null>(null)
  const [updateStatus, setUpdateStatus] = useState<'available' | 'downloading' | 'installing' | 'error'>('available')
  const [updateProgress, setUpdateProgress] = useState<number | null>(null)
  const [updateError, setUpdateError] = useState('')
  const updateCheckStarted = useRef(false)
  const language = interfaceLanguage
  const copy = getCopy(interfaceLanguage)
  const apiKey = providerApiKeyDrafts[modelProvider]
  const apiKeyConfigured = providerApiKeyStatus[modelProvider]

  useEffect(() => {
    document.documentElement.lang = interfaceLanguage
    localStorage.setItem('phireader.interfaceLanguage', interfaceLanguage)
    // Keep the legacy key in sync for older installations.
    localStorage.setItem('phireader.language', interfaceLanguage)
  }, [interfaceLanguage])

  useEffect(() => {
    activePaperIdRef.current = activePaperId
  }, [activePaperId])

  useEffect(() => {
    if (!isDesktop() || updateCheckStarted.current) return
    updateCheckStarted.current = true
    void check().then((update) => {
      if (update) setAvailableUpdate(update)
    }).catch((error) => {
      console.warn('Unable to check for PhiReader updates', error)
    })
  }, [])

  useEffect(() => {
    if (!isDesktop()) return
    const legacyApiKeys: ProviderApiKeyDrafts = {
      // The oldest release used one key and only supported Qwen.
      qwen: localStorage.getItem(API_KEY_STORAGE_KEYS.qwen) || localStorage.getItem('phireader.apiKey') || '',
      deepseek: localStorage.getItem(API_KEY_STORAGE_KEYS.deepseek) ?? '',
      custom: localStorage.getItem(API_KEY_STORAGE_KEYS.custom) ?? '',
    }
    void (async () => {
      try {
        const stored = await invoke<Partial<ProviderApiKeyStatus>>('get_api_key_status')
        const restored = { ...EMPTY_PROVIDER_API_KEY_STATUS }
        let migrationFailed = false
        for (const provider of Object.keys(restored) as ModelProvider[]) {
          restored[provider] = stored[provider] ?? false
          if (!restored[provider] && legacyApiKeys[provider]) {
            try {
              await invoke('set_api_key', { provider, apiKey: legacyApiKeys[provider] })
              restored[provider] = true
            } catch {
              migrationFailed = true
            }
          }
          // Delete plaintext only after a secure value exists (or no legacy
          // value ever existed), so a keyring failure cannot lose credentials.
          if (restored[provider]) {
            localStorage.removeItem(API_KEY_STORAGE_KEYS[provider])
            if (provider === 'qwen') localStorage.removeItem('phireader.apiKey')
          }
        }
        setProviderApiKeyStatus(restored)
        if (migrationFailed) notify(language === 'en' ? 'Some API keys could not be moved to the system credential store' : '部分 API Key 无法迁移到系统凭据库')
      } catch {
        notify(language === 'en' ? 'Unable to access the system credential store' : '无法访问系统凭据库')
      }
    })()
  }, [])

  useEffect(() => {
    if (!isDesktop()) return
    Promise.all([
      invoke<StoredPaperResponse[]>('list_papers'),
      invoke<PaperFolder[]>('list_folders').catch(() => {
        notify(language === 'en' ? 'Unable to read custom folders' : '无法读取自定义目录')
        return []
      }),
    ]).then(([stored, restoredFolders]) => {
      const restored = stored.map(storedPaperToPaper)
      setPapers(restored)
      setFolders(restoredFolders)
      setActivePaperId(restored[0]?.id ?? '')
      setActiveSymbol(restored[0]?.symbols[0] ?? null)
    }).catch(() => notify(language === 'en' ? 'Unable to read the local library' : '无法读取本地论文库'))
  }, [])

  const activePaper = papers.find((paper) => paper.id === activePaperId) ?? papers[0]
  const activeFolder = folders.find((folder) => folder.id === activeFolderId)
  const filteredPapers = useMemo(() => {
    const folderPaperIds = activeFolder ? new Set(activeFolder.paperIds) : null
    return papers.filter((paper) =>
      (!folderPaperIds || folderPaperIds.has(paper.id))
      && `${paper.title} ${paper.authors} ${paper.venue}`.toLowerCase().includes(query.toLowerCase()),
    )
  }, [activeFolder, papers, query])

  function notify(message: string) {
    setToast(message)
    window.setTimeout(() => setToast(''), 2600)
  }

  async function installAvailableUpdate() {
    if (!availableUpdate || updateStatus === 'downloading' || updateStatus === 'installing') return
    setUpdateStatus('downloading')
    setUpdateError('')
    setUpdateProgress(0)
    let downloaded = 0
    let contentLength = 0
    try {
      await availableUpdate.downloadAndInstall((event) => {
        if (event.event === 'Started') {
          contentLength = event.data.contentLength ?? 0
        } else if (event.event === 'Progress') {
          downloaded += event.data.chunkLength
          setUpdateProgress(contentLength > 0 ? Math.min(100, Math.round(downloaded / contentLength * 100)) : null)
        } else if (event.event === 'Finished') {
          setUpdateProgress(100)
          setUpdateStatus('installing')
        }
      })
      setUpdateStatus('installing')
      await relaunch()
    } catch (error) {
      setUpdateStatus('error')
      setUpdateError(error instanceof Error ? error.message : String(error))
    }
  }

  function updateApiKey(provider: ModelProvider, value: string) {
    apiKeyDraftsRef.current = { ...apiKeyDraftsRef.current, [provider]: value }
    setProviderApiKeyDrafts((current) => ({ ...current, [provider]: value }))
    const pending = apiKeySaveTimers.current[provider]
    if (pending !== undefined) window.clearTimeout(pending)
    apiKeySaveTimers.current[provider] = window.setTimeout(() => {
      void invoke('set_api_key', { provider, apiKey: value }).then(() => {
        setProviderApiKeyStatus((current) => ({ ...current, [provider]: Boolean(value.trim()) }))
        localStorage.removeItem(API_KEY_STORAGE_KEYS[provider])
        if (provider === 'qwen') localStorage.removeItem('phireader.apiKey')
        if (apiKeyDraftsRef.current[provider] === value) {
          apiKeyDraftsRef.current = { ...apiKeyDraftsRef.current, [provider]: '' }
          setProviderApiKeyDrafts((current) => ({ ...current, [provider]: '' }))
        }
      }).catch(() => notify(language === 'en' ? 'Unable to save the API key securely' : '无法安全保存 API Key'))
    }, 600)
  }

  function nudgeSidebarWidth(delta: number) {
    const next = clampSidebarWidth(sidebarWidth + delta)
    setSidebarWidth(next)
    localStorage.setItem('phireader.sidebarWidth', String(next))
  }

  function startSidebarResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault()
    event.stopPropagation()
    const startX = event.clientX
    const startWidth = sidebarWidth
    const appShell = event.currentTarget.closest<HTMLElement>('.app-shell')
    const zoom = Number.parseFloat(getComputedStyle(appShell ?? document.documentElement).zoom) || 1
    let finalWidth = startWidth
    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    setSidebarResizing(true)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const handleMove = (moveEvent: PointerEvent) => {
      finalWidth = clampSidebarWidth(startWidth + (moveEvent.clientX - startX) / zoom)
      setSidebarWidth(finalWidth)
    }
    const finish = () => {
      window.removeEventListener('pointermove', handleMove)
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('pointercancel', finish)
      window.removeEventListener('blur', finish)
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      setSidebarResizing(false)
      localStorage.setItem('phireader.sidebarWidth', String(finalWidth))
    }
    window.addEventListener('pointermove', handleMove)
    window.addEventListener('pointerup', finish)
    window.addEventListener('pointercancel', finish)
    window.addEventListener('blur', finish)
  }

  function handleSidebarResizeKey(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      nudgeSidebarWidth(-8)
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      nudgeSidebarWidth(8)
    } else if (event.key === 'Home') {
      event.preventDefault()
      const next = SIDEBAR_MIN_WIDTH
      setSidebarWidth(next)
      localStorage.setItem('phireader.sidebarWidth', String(next))
    } else if (event.key === 'End') {
      event.preventDefault()
      const next = SIDEBAR_MAX_WIDTH
      setSidebarWidth(next)
      localStorage.setItem('phireader.sidebarWidth', String(next))
    }
  }

  async function enrichSymbolMeanings(
    paperId: string,
    paperPath: string,
    parsePath: string,
    symbols: SymbolDefinition[],
    announce = false,
  ) {
    if (!isDesktop()) {
      if (announce) notify(language === 'en' ? 'Summary generation is available in the desktop app' : '摘要生成仅在桌面版可用')
      return
    }
    if (!apiKeyConfigured) {
      if (announce) notify(language === 'en' ? 'Configure an API key in Settings first' : '请先在设置中配置 API Key')
      return
    }
    const endpoint = modelProvider === 'qwen' ? QWEN_CHAT_ENDPOINT : modelProvider === 'deepseek' ? DEEPSEEK_CHAT_ENDPOINT : customEndpoint.trim()
    const selectedModel = modelProvider === 'custom' ? customModelName.trim() : modelName.trim()
    if (!selectedModel) {
      if (announce) notify(language === 'en' ? 'Configure a model in Settings first' : '请先在设置中配置模型')
      return
    }
    if (!endpoint) {
      if (announce) notify(language === 'en' ? 'Configure a custom API endpoint in Settings first' : '请先在设置中配置自定义 API 地址')
      return
    }
    if (symbols.length === 0) {
      if (announce) notify(copy.reader.noSymbols)
      return
    }
    if (summaryBusyPaperId) return
    setSummaryBusyPaperId(paperId)
    const modelConfig: ModelConfig = {
      provider: modelProvider,
      label: modelProvider === 'qwen' ? 'Qwen' : modelProvider === 'deepseek' ? 'DeepSeek' : 'Custom model',
      endpoint,
      model: selectedModel,
    }
    if (announce) notify(language === 'en' ? `Asking ${modelConfig.label} to generate summaries…` : `正在调用 ${modelConfig.label} 生成摘要…`)
    try {
      const result = await summarizeSymbols(modelConfig, summaryLanguage, symbols)
      if (result.updates.length === 0) {
        if (announce) notify(`${language === 'en' ? 'No summaries generated' : '未生成摘要'}：${result.firstError || (language === 'en' ? 'The model returned no usable content' : '模型未返回可用内容')}`)
        return
      }
      const response = await invoke<ParserResponse>('update_symbol_meanings', {
        paperPath,
        parsePath,
        updates: result.updates,
        source: modelProvider,
      })
      const nextSymbols = response.symbols.map((symbol) => ({
        ...symbol,
        identityKey: symbol.identityKey ?? (symbol as SymbolDefinition & { identity_key?: string }).identity_key,
        status: symbol.status === 'unresolved' ? 'review' as const : symbol.status,
      }))
      setPapers((current) => current.map((paper) => paper.id === paperId ? {
        ...paper,
        symbols: nextSymbols,
      } : paper))
      setActiveSymbol((current) => current ? nextSymbols.find((item) => item.id === current.id) ?? current : null)
      notify(result.failedBatches > 0
        ? (language === 'en' ? `Generated ${result.updates.length} summaries; ${result.failedBatches} batches failed` : `已生成 ${result.updates.length} 个摘要，${result.failedBatches} 批失败`)
        : (language === 'en' ? `Generated ${result.updates.length} symbol summaries` : `已生成 ${result.updates.length} 个符号摘要`))
    } catch (error) {
      notify(`${language === 'en' ? 'Summary generation failed; local meanings were kept' : '符号摘要生成失败，已保留本地摘要'}：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setSummaryBusyPaperId((current) => current === paperId ? '' : current)
    }
  }

  function openPaper(paper: Paper) {
    setActivePaperId(paper.id)
    setActiveSymbol(paper.symbols[0] ?? null)
    setView('reader')
  }

  function updateReadingPage(paperId: string, page: number) {
    setReadingPages((current) => current[paperId] === page ? current : { ...current, [paperId]: page })
  }

  function selectFolder(folderId: string | null) {
    setActiveFolderId(folderId)
    setView('library')
    setQuery('')
    setSelectedPaperIds(new Set())
  }

  function updateLibraryQuery(value: string) {
    setQuery(value)
    setSelectedPaperIds(new Set())
  }

  async function createPaperFolder(selectAfterCreate = true): Promise<PaperFolder | undefined> {
    const name = window.prompt(language === 'en' ? 'Folder name' : '目录名称')?.trim()
    if (!name) return
    if (folders.some((folder) => folder.name.localeCompare(name, undefined, { sensitivity: 'accent' }) === 0)) {
      notify(language === 'en' ? 'A folder with that name already exists' : '已存在同名目录')
      return
    }
    try {
      const folder = isDesktop()
        ? await invoke<PaperFolder>('create_folder', { name })
        : { id: `folder-${Date.now()}`, name, paperIds: [] }
      setFolders((current) => [...current, folder])
      if (selectAfterCreate) selectFolder(folder.id)
      notify(language === 'en' ? `Created folder “${name}”` : `已创建目录“${name}”`)
      return folder
    } catch (error) {
      notify(`${language === 'en' ? 'Unable to create folder' : '创建目录失败'}：${error instanceof Error ? error.message : String(error)}`)
    }
  }

  function openFolderAssignment(paper: Paper) {
    setFolderPaper(paper)
    setFolderDraftIds(new Set(
      folders.filter((folder) => folder.paperIds.includes(paper.id)).map((folder) => folder.id),
    ))
  }

  function toggleFolderAssignment(folderId: string) {
    setFolderDraftIds((current) => {
      const next = new Set(current)
      if (next.has(folderId)) next.delete(folderId)
      else next.add(folderId)
      return next
    })
  }

  async function saveFolderAssignment() {
    if (!folderPaper || folderSaving) return
    const paper = folderPaper
    const selectedIds = [...folderDraftIds]
    setFolderSaving(true)
    try {
      if (isDesktop()) {
        await invoke('set_paper_folders', { paperId: paper.id, folderIds: selectedIds })
      }
      const selected = new Set(selectedIds)
      setFolders((current) => current.map((folder) => ({
        ...folder,
        paperIds: selected.has(folder.id)
          ? folder.paperIds.includes(paper.id) ? folder.paperIds : [...folder.paperIds, paper.id]
          : folder.paperIds.filter((paperId) => paperId !== paper.id),
      })))
      setFolderPaper(null)
      notify(language === 'en'
        ? `Saved to ${selectedIds.length} folder${selectedIds.length === 1 ? '' : 's'}`
        : `已保存到 ${selectedIds.length} 个目录`)
    } catch (error) {
      notify(`${language === 'en' ? 'Unable to update folders' : '更新论文目录失败'}：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setFolderSaving(false)
    }
  }

  async function deletePaperFolder(folder: PaperFolder) {
    const confirmed = window.confirm(language === 'en'
      ? `Delete folder “${folder.name}”? Its papers will remain in All papers.`
      : `删除目录“${folder.name}”？其中的论文仍会保留在“全部论文”中。`)
    if (!confirmed) return
    try {
      if (isDesktop()) await invoke('delete_folder', { folderId: folder.id })
      setFolders((current) => current.filter((item) => item.id !== folder.id))
      if (activeFolderId === folder.id) selectFolder(null)
      notify(language === 'en' ? 'Folder deleted; papers were kept' : '目录已删除，论文仍保留在主目录')
    } catch (error) {
      notify(`${language === 'en' ? 'Unable to delete folder' : '删除目录失败'}：${error instanceof Error ? error.message : String(error)}`)
    }
  }

  async function processQueuedImport({ file, id }: QueuedImport, importFolderId: string | null): Promise<boolean> {
    // Browser smoke tests keep the object URL path. The desktop build sends
    // bytes to the Tauri command, which persists the PDF and starts Python.
    if (!isDesktop()) {
      setPapers((current) => current.map((paper) => paper.id === id ? { ...paper, parseStatus: 'idle', progress: 0 } : paper))
      importingIds.current.delete(id)
      return true
    }

    try {
      const bytes = Array.from(new Uint8Array(await file.arrayBuffer()))
      const response = await invoke<ParserResponse>('import_and_parse_pdf', { fileName: file.name, bytes, folderId: importFolderId })
      if (!importingIds.current.has(id)) {
        // The user deleted this placeholder while parsing. Remove the
        // backend result as soon as the in-flight parser returns.
        await invoke('delete_paper', { paperId: response.paperId }).catch(() => undefined)
        return false
      }
      const metadata = response.metadata
      const symbols = response.symbols.map((symbol) => ({
        ...symbol,
        identityKey: symbol.identityKey ?? (symbol as SymbolDefinition & { identity_key?: string }).identity_key,
        status: symbol.status === 'unresolved' ? 'review' : symbol.status,
      }))
      setPapers((current) => current.map((paper) => paper.id === id ? {
        ...paper,
        id: response.paperId,
        title: parsedTitleOrFallback(metadata.title, paper.title),
        authors: metadata.authors || (language === 'en' ? 'Author information unavailable' : '作者信息未提供'),
        venue: metadata.producer || (language === 'en' ? 'Local paper · PDF' : '本地论文 · PDF'),
        year: metadata.year || '—',
        pages: metadata.pages,
        size: `${Math.max(1, Math.round(metadata.file_size / 1024))} KB`,
        abstract: symbols.length
          ? (language === 'en' ? 'Local parsing is complete. Author-defined symbols appear in the sidebar.' : '本地解析已完成。侧栏将显示正文中作者定义过的符号。')
          : (language === 'en' ? 'Local parsing is complete, but no author-defined symbols were found.' : '本地解析完成，但没有找到可确认的作者定义符号。'),
        symbols,
        progress: 100,
        parseStatus: 'ready',
        parsePath: response.parsePath,
        filePath: response.paperPath,
        candidateCount: response.totals.candidate_chars,
        visualReviewItems: response.totals.visual_review_items,
        reviewSymbolCount: response.totals.review_symbols,
        visualReviewQueue: response.visualReviewQueue,
      } : paper))
      if (importFolderId) {
        setFolders((current) => current.map((folder) => folder.id === importFolderId
          ? { ...folder, paperIds: folder.paperIds.map((paperId) => paperId === id ? response.paperId : paperId) }
          : folder))
      }
      if (activePaperIdRef.current === id) {
        activePaperIdRef.current = response.paperId
        setActivePaperId(response.paperId)
        setActiveSymbol(symbols[0] ?? null)
      }
      return true
    } catch (error) {
      if (!importingIds.current.has(id)) return false
      const message = error instanceof Error ? error.message : String(error)
      setPapers((current) => current.map((paper) => paper.id === id ? {
        ...paper,
        parseStatus: 'error',
        progress: 0,
        parseError: message,
        abstract: `${language === 'en' ? 'Parsing failed' : '解析失败'}：${message}`,
      } : paper))
      return false
    } finally {
      importingIds.current.delete(id)
    }
  }

  function importPapers(fileList: FileList | File[]) {
    const dropped = Array.from(fileList)
    const pdfFiles = dropped.filter(isPdfFile)
    const rejectedCount = dropped.length - pdfFiles.length
    if (pdfFiles.length === 0) {
      notify(copy.library.pdfOnly)
      return
    }

    const importFolderId = activeFolderId
    const queued: QueuedImport[] = pdfFiles.map((file) => ({
      file,
      id: `pending-${crypto.randomUUID()}`,
    }))
    const placeholders: Paper[] = queued.map(({ file, id }) => ({
      id,
      title: file.name.replace(/\.pdf$/i, ''),
      authors: language === 'en' ? 'Waiting for metadata' : '等待解析元数据',
      venue: language === 'en' ? 'Local paper · Queued' : '本地论文 · 排队中',
      year: '—',
      pages: 0,
      size: `${Math.max(1, Math.round(file.size / 1024))} KB`,
      accent: '#d58bff',
      progress: 8,
      addedAt: language === 'en' ? 'Just now' : '刚刚',
      abstract: language === 'en'
        ? 'The file is queued for local PDF parsing and symbol extraction.'
        : '文件已加入论文库，正在排队等待本地 PDF 解析和符号提取。',
      symbols: [],
      fileUrl: isDesktop() ? undefined : URL.createObjectURL(file),
      filePath: isDesktop() ? undefined : file.name,
      parseStatus: 'parsing',
    }))

    queued.forEach(({ id }) => importingIds.current.add(id))
    setPapers((current) => [...placeholders, ...current])
    if (importFolderId) {
      const queuedIds = queued.map(({ id }) => id)
      setFolders((current) => current.map((folder) => folder.id === importFolderId
        ? { ...folder, paperIds: [...queuedIds, ...folder.paperIds] }
        : folder))
    }
    if (!activePaperIdRef.current) {
      activePaperIdRef.current = queued[0].id
      setActivePaperId(queued[0].id)
      setActiveSymbol(null)
    }
    notify(copy.library.importQueued(pdfFiles.length, rejectedCount))

    importQueue.current = importQueue.current.catch(() => undefined).then(async () => {
      let succeeded = 0
      for (const item of queued) {
        if (await processQueuedImport(item, importFolderId)) succeeded += 1
      }
      notify(isDesktop()
        ? copy.library.importFinished(succeeded, queued.length - succeeded)
        : copy.library.browserQueued(succeeded))
    })
  }

  function handleDragEnter(event: ReactDragEvent<HTMLDivElement>) {
    if (!event.dataTransfer.types.includes('Files')) return
    event.preventDefault()
    dragDepth.current += 1
    setDragFileCount(Math.max(1, event.dataTransfer.items.length))
    setDragActive(true)
  }

  function handleDragOver(event: ReactDragEvent<HTMLDivElement>) {
    if (!event.dataTransfer.types.includes('Files')) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
  }

  function handleDragLeave(event: ReactDragEvent<HTMLDivElement>) {
    if (!event.dataTransfer.types.includes('Files')) return
    dragDepth.current = Math.max(0, dragDepth.current - 1)
    if (dragDepth.current === 0) setDragActive(false)
  }

  function handleDrop(event: ReactDragEvent<HTMLDivElement>) {
    event.preventDefault()
    dragDepth.current = 0
    setDragActive(false)
    setDragFileCount(0)
    if (event.dataTransfer.files.length > 0) importPapers(event.dataTransfer.files)
  }

  async function removePaper(paper: Paper) {
    const targetFolderId = activeFolderId
    if (targetFolderId) {
      const confirmed = window.confirm(language === 'en'
        ? `Remove “${paper.title}” from this folder? The paper will remain in All papers.`
        : `从当前目录移除“${paper.title}”？论文仍会保留在“全部论文”中。`)
      if (!confirmed) return
      try {
        if (isDesktop()) await invoke('remove_paper_from_folder', { folderId: targetFolderId, paperId: paper.id })
        setFolders((current) => current.map((folder) => folder.id === targetFolderId
          ? { ...folder, paperIds: folder.paperIds.filter((id) => id !== paper.id) }
          : folder))
        setSelectedPaperIds((current) => {
          const next = new Set(current)
          next.delete(paper.id)
          return next
        })
        if (activePaperId === paper.id) {
          setActiveSymbol(null)
          setView('library')
        }
        notify(language === 'en' ? 'Paper removed from folder' : '论文已从目录移除，主目录中的文件保持不变')
      } catch (error) {
        notify(`${language === 'en' ? 'Unable to remove paper' : '移除失败'}：${error instanceof Error ? error.message : String(error)}`)
      }
      return
    }
    const confirmed = window.confirm(language === 'en'
      ? `Permanently delete “${paper.title}”? It will also disappear from every folder.`
      : `永久删除“${paper.title}”？该论文也会从所有自定义目录中移除。`)
    if (!confirmed) return
    const isImporting = importingIds.current.has(paper.id)
    if (isImporting) importingIds.current.delete(paper.id)
    if (isDesktop() && paper.parsePath) {
      try {
        await invoke('delete_paper', { paperId: paper.id })
      } catch (error) {
        notify(`删除失败：${error instanceof Error ? error.message : String(error)}`)
        return
      }
    }
    if (paper.fileUrl) URL.revokeObjectURL(paper.fileUrl)
    setPapers((current) => current.filter((item) => item.id !== paper.id))
    setFolders((current) => current.map((folder) => ({
      ...folder,
      paperIds: folder.paperIds.filter((id) => id !== paper.id),
    })))
    setSelectedPaperIds((current) => {
      if (!current.has(paper.id)) return current
      const next = new Set(current)
      next.delete(paper.id)
      return next
    })
    if (activePaperId === paper.id) {
      setActiveSymbol(null)
      setView('library')
    }
    notify(language === 'en' ? 'Paper permanently deleted' : '论文已永久删除')
  }

  function togglePaperSelection(paperId: string, selected: boolean) {
    setSelectedPaperIds((current) => {
      const next = new Set(current)
      if (selected) next.add(paperId)
      else next.delete(paperId)
      return next
    })
  }

  function toggleAllPaperSelection(selected: boolean, visiblePapers: Paper[]) {
    setSelectedPaperIds((current) => {
      const next = new Set(current)
      for (const paper of visiblePapers) {
        if (paper.parseStatus === 'parsing') continue
        if (selected) next.add(paper.id)
        else next.delete(paper.id)
      }
      return next
    })
  }

  async function removeSelectedPapers() {
    const targetFolderId = activeFolderId
    const selected = filteredPapers.filter((paper) => selectedPaperIds.has(paper.id) && paper.parseStatus !== 'parsing')
    if (selected.length === 0) return
    const prompt = targetFolderId
      ? (language === 'en'
        ? `Remove ${selected.length} selected paper${selected.length === 1 ? '' : 's'} from this folder? The files will remain in All papers.`
        : `从当前目录移除选中的 ${selected.length} 篇论文？文件仍会保留在“全部论文”中。`)
      : (language === 'en'
        ? `Permanently delete ${selected.length} selected paper${selected.length === 1 ? '' : 's'}? They will also disappear from every folder.`
        : `永久删除选中的 ${selected.length} 篇论文？它们也会从所有自定义目录中移除。`)
    if (!window.confirm(prompt)) return
    const ids = selected.map((paper) => paper.id)
    try {
      if (targetFolderId) {
        if (isDesktop()) await invoke('remove_papers_from_folder', { folderId: targetFolderId, paperIds: ids })
        const removed = new Set(ids)
        setFolders((current) => current.map((folder) => folder.id === targetFolderId
          ? { ...folder, paperIds: folder.paperIds.filter((paperId) => !removed.has(paperId)) }
          : folder))
        setSelectedPaperIds(new Set())
        notify(language === 'en' ? `${ids.length} papers removed from folder` : `已从当前目录移除 ${ids.length} 篇论文`)
        return
      }

      const result = isDesktop()
        ? await invoke<BatchDeleteResponse>('delete_papers', { paperIds: ids })
        : { deletedIds: ids, failures: [] }
      const deleted = new Set(result.deletedIds)
      for (const paper of selected) {
        if (deleted.has(paper.id) && paper.fileUrl) URL.revokeObjectURL(paper.fileUrl)
      }
      setPapers((current) => current.filter((paper) => !deleted.has(paper.id)))
      setFolders((current) => current.map((folder) => ({
        ...folder,
        paperIds: folder.paperIds.filter((paperId) => !deleted.has(paperId)),
      })))
      setSelectedPaperIds((current) => new Set([...current].filter((paperId) => !deleted.has(paperId))))
      if (activePaperId && deleted.has(activePaperId)) {
        setActivePaperId('')
        setActiveSymbol(null)
        setView('library')
      }
      notify(result.failures.length > 0
        ? (language === 'en' ? `Deleted ${deleted.size}; ${result.failures.length} failed` : `已删除 ${deleted.size} 篇，${result.failures.length} 篇失败`)
        : (language === 'en' ? `${deleted.size} papers permanently deleted` : `已永久删除 ${deleted.size} 篇论文`))
    } catch (error) {
      notify(`${language === 'en' ? 'Batch operation failed' : '批量操作失败'}：${error instanceof Error ? error.message : String(error)}`)
    }
  }

  async function renamePaper(paper: Paper) {
    const nextTitle = window.prompt('修改论文标题', paper.title)?.trim()
    if (!nextTitle || nextTitle === paper.title) return
    if (isDesktop()) {
      try {
        await invoke('rename_paper', { paperId: paper.id, title: nextTitle })
      } catch (error) {
        notify(`重命名失败：${error instanceof Error ? error.message : String(error)}`)
        return
      }
    }
    setPapers((current) => current.map((item) => item.id === paper.id ? { ...item, title: nextTitle } : item))
    notify(language === 'en' ? 'Paper title updated' : '论文标题已更新')
  }

  return (
      <div className={dark ? 'app-shell dark' : 'app-shell light'} style={{ '--ui-scale': fontSize / 17, '--sidebar-width': `${sidebarWidth}px` } as React.CSSProperties} onDragEnter={handleDragEnter} onDragOver={handleDragOver} onDragLeave={handleDragLeave} onDrop={handleDrop}>
      <aside className="sidebar">
        <div className="brand-mark"><span className="brand-icon"><img src="/icon2.png" alt="PhiReader" /></span><div><strong>PhiReader</strong><small>paper intelligence</small></div></div>
        <nav className="primary-nav">
          <button className={view === 'library' && !activeFolderId ? 'nav-item active' : 'nav-item'} onClick={() => selectFolder(null)}><Library size={18} />{copy.sidebar.library}<span className="nav-count">{papers.length}</span></button>
          <button className={view === 'reader' ? 'nav-item active' : 'nav-item'} onClick={() => setView('reader')}><BookOpen size={18} />{copy.sidebar.reader}</button>
        </nav>
        <div className="sidebar-rule" />
        <div className="folder-section-heading"><span>{copy.sidebar.folders}</span><button className="folder-create-button" title={copy.sidebar.createFolder} aria-label={copy.sidebar.createFolder} onClick={() => void createPaperFolder()}><FolderPlus size={15} /></button></div>
        <div className="folder-list">
          <button className={!activeFolderId && view === 'library' ? 'collection-item folder-item active' : 'collection-item folder-item'} onClick={() => selectFolder(null)}><FolderOpen size={15} /><span>{copy.sidebar.allPapers}</span><span className="nav-count">{papers.length}</span></button>
          {folders.map((folder) => <div className="folder-row" key={folder.id}><button className={activeFolderId === folder.id && view === 'library' ? 'collection-item folder-item active' : 'collection-item folder-item'} onClick={() => selectFolder(folder.id)}><Folder size={15} /><span className="folder-name">{folder.name}</span><span className="nav-count">{folder.paperIds.filter((paperId) => papers.some((paper) => paper.id === paperId)).length}</span></button><button className="folder-delete-button" title={`${copy.sidebar.deleteFolder} ${folder.name}`} aria-label={`${copy.sidebar.deleteFolder} ${folder.name}`} onClick={() => void deletePaperFolder(folder)}><Trash2 size={13} /></button></div>)}
        </div>
        <div className="collection-item collection-summary"><span className="collection-dot mint" />{copy.sidebar.parsedSymbols}<span className="nav-count">{papers.reduce((total, paper) => total + paper.symbols.length, 0)}</span></div>
        <div className="sidebar-bottom">
          <button className="nav-item" onClick={() => setView('settings')}><Settings size={18} />{copy.sidebar.settings}</button>
          <div className="engine-status"><span className="status-pulse" /><div><small>{copy.sidebar.parser}</small><strong>{copy.sidebar.ready}</strong></div></div>
        </div>
      </aside>
      <div className={sidebarResizing ? 'app-sidebar-resize-handle dragging' : 'app-sidebar-resize-handle'} role="separator" aria-orientation="vertical" aria-label={language === 'en' ? 'Resize left sidebar' : '调整左侧边栏宽度'} aria-valuemin={SIDEBAR_MIN_WIDTH} aria-valuemax={SIDEBAR_MAX_WIDTH} aria-valuenow={sidebarWidth} tabIndex={0} onPointerDown={startSidebarResize} onKeyDown={handleSidebarResizeKey} />

      <main className="main-area">
        {view === 'library' && <LibraryView language={language} papers={filteredPapers} query={query} setQuery={updateLibraryQuery} folderName={activeFolder?.name} selectedPaperIds={selectedPaperIds} onToggleSelection={togglePaperSelection} onToggleAllSelection={toggleAllPaperSelection} onBatchDelete={() => void removeSelectedPapers()} onImport={() => fileRef.current?.click()} onOpen={openPaper} onRename={renamePaper} onManageFolders={openFolderAssignment} onDelete={removePaper} />}
        {view === 'reader' && activePaper && <ReaderView language={language} paper={activePaper} pageNumber={readingPages[activePaper.id] ?? 1} onPageChange={(page) => updateReadingPage(activePaper.id, page)} symbol={activeSymbol} onSelectSymbol={setActiveSymbol} onBack={() => setView('library')} onGenerateSummaries={() => void enrichSymbolMeanings(activePaper.id, activePaper.filePath ?? '', activePaper.parsePath ?? '', activePaper.symbols, true)} summaryBusy={summaryBusyPaperId === activePaper.id} fontSize={fontSize} />}
        {view === 'reader' && !activePaper && <EmptyReader language={language} onBack={() => setView('library')} />}
      {view === 'settings' && <SettingsView language={language} setInterfaceLanguage={setInterfaceLanguage} summaryLanguage={summaryLanguage} setSummaryLanguage={(value) => { setSummaryLanguage(value); localStorage.setItem('phireader.summaryLanguage', value) }} dark={dark} onToggleTheme={() => { setDark((value) => { const next = !value; localStorage.setItem('phireader.dark', String(next)); return next }) }} fontSize={fontSize} setFontSize={(value) => { setFontSize(value); localStorage.setItem('phireader.fontSize', String(value)) }} apiKey={apiKey} apiKeyConfigured={apiKeyConfigured} setApiKey={(value) => updateApiKey(modelProvider, value)} modelProvider={modelProvider} setModelProvider={(value) => { setModelProvider(value); localStorage.setItem('phireader.modelProvider', value); if (value !== 'custom') { const fallback = PROVIDER_MODELS[value][0]; setModelName((current) => PROVIDER_MODELS[value].includes(current) ? current : fallback); localStorage.setItem('phireader.modelName', PROVIDER_MODELS[value].includes(modelName) ? modelName : fallback) } }} modelName={modelName} setModelName={(value) => { setModelName(value); localStorage.setItem('phireader.modelName', value) }} customModelName={customModelName} setCustomModelName={(value) => { setCustomModelName(value); localStorage.setItem('phireader.customModelName', value) }} customEndpoint={customEndpoint} setCustomEndpoint={(value) => { setCustomEndpoint(value); localStorage.setItem('phireader.customEndpoint', value) }} />}
      </main>
      {folderPaper && <FolderAssignmentDialog language={language} paper={folderPaper} folders={folders} selectedIds={folderDraftIds} saving={folderSaving} onToggle={toggleFolderAssignment} onCreateFolder={async () => { const folder = await createPaperFolder(false); if (folder) setFolderDraftIds((current) => new Set(current).add(folder.id)) }} onClose={() => { if (!folderSaving) setFolderPaper(null) }} onSave={() => void saveFolderAssignment()} />}
      {availableUpdate && <UpdateDialog language={language} update={availableUpdate} status={updateStatus} progress={updateProgress} error={updateError} onClose={() => setAvailableUpdate(null)} onInstall={() => void installAvailableUpdate()} />}
      <input ref={fileRef} type="file" accept="application/pdf,.pdf" multiple hidden onChange={(event) => { if (event.target.files?.length) importPapers(event.target.files); event.target.value = '' }} />
      {dragActive && <div className="quick-upload-overlay" role="status" aria-live="polite"><div className="quick-upload-target"><span className="quick-upload-icon"><Upload size={31} /></span><strong>{copy.library.dropTitle(dragFileCount)}</strong><span>{copy.library.dropHint}</span><small>{copy.library.pdfOnly}</small></div></div>}
      {toast && <div className="toast"><Check size={16} />{toast}</div>}
    </div>
  )
}

function UpdateDialog({ language, update, status, progress, error, onClose, onInstall }: { language: Language; update: Update; status: 'available' | 'downloading' | 'installing' | 'error'; progress: number | null; error: string; onClose: () => void; onInstall: () => void }) {
  const busy = status === 'downloading' || status === 'installing'
  const isEnglish = language === 'en'
  const notes = update.body?.trim() || (isEnglish ? 'This release does not include release notes.' : '此版本未提供更新日志。')
  const statusText = status === 'installing'
    ? (isEnglish ? 'Installing update and preparing to restart…' : '正在安装更新并准备重启…')
    : progress === null
      ? (isEnglish ? 'Downloading update…' : '正在下载更新…')
      : (isEnglish ? `Downloading update ${progress}%` : `正在下载更新 ${progress}%`)

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [busy, onClose])

  return <div className="folder-dialog-backdrop update-dialog-backdrop" role="presentation">
    <section className="folder-dialog update-dialog" role="dialog" aria-modal="true" aria-labelledby="update-dialog-title">
      <header className="folder-dialog-header">
        <div><span className="folder-dialog-eyebrow">{isEnglish ? 'Software update' : '软件更新'}</span><h2 id="update-dialog-title">{isEnglish ? `PhiReader ${update.version} is available` : `PhiReader ${update.version} 已发布`}</h2><p className="update-version">{isEnglish ? `Current version ${update.currentVersion}` : `当前版本 ${update.currentVersion}`}</p></div>
        <button className="icon-button" type="button" aria-label={isEnglish ? 'Remind me later' : '稍后提醒'} disabled={busy} onClick={onClose}><X size={16} /></button>
      </header>
      <div className="update-dialog-content">
        <strong>{isEnglish ? 'What’s new' : '更新日志'}</strong>
        <div className="update-notes">{notes}</div>
        {busy && <div className="update-progress" aria-live="polite"><div className="update-progress-track"><span className={progress === null ? 'indeterminate' : ''} style={progress === null ? undefined : { width: `${progress}%` }} /></div><span><RefreshCw size={13} />{statusText}</span></div>}
        {status === 'error' && <div className="update-error" role="alert">{isEnglish ? 'Update failed' : '更新失败'}：{error}</div>}
      </div>
      <footer className="folder-dialog-footer update-dialog-footer">
        <span>{isEnglish ? 'Your local library and settings will be preserved.' : '本地论文库与设置不会受影响。'}</span>
        <div><button className="outline-button" type="button" disabled={busy} onClick={onClose}>{isEnglish ? 'Later' : '稍后提醒'}</button><button className="primary-button" type="button" disabled={busy} onClick={onInstall}>{busy ? <RefreshCw className="spin" size={15} /> : <Download size={15} />}{status === 'error' ? (isEnglish ? 'Try again' : '重试') : (isEnglish ? 'Update now' : '立即更新')}</button></div>
      </footer>
    </section>
  </div>
}

function EmptyReader({ language, onBack }: { language: Language; onBack: () => void }) {
  const copy = getCopy(language).reader
  return <div className="page-content empty-reader"><button className="back-button" onClick={onBack}><ArrowLeft size={17} />{copy.back}</button><div className="empty-reader-card"><BookOpen size={28} /><h1>{copy.noPaper}</h1><p>{copy.noPaperHint}</p><button className="primary-button" onClick={onBack}><Library size={16} />{copy.returnLibrary}</button></div></div>
}

function PaperReaderStatus({ language, paper }: { language: Language; paper: Paper }) {
  const copy = getCopy(language).reader
  const failed = paper.parseStatus === 'error'
  return <div className="document-view render-state reader-status-card"><Sparkles size={28} /><strong>{failed ? copy.parseFailedTitle : copy.parsingTitle}</strong><span>{failed ? paper.parseError || copy.parseFailedHint : copy.parsingHint}</span></div>
}

function LibraryView({ language, papers, query, setQuery, folderName, selectedPaperIds, onToggleSelection, onToggleAllSelection, onBatchDelete, onImport, onOpen, onRename, onManageFolders, onDelete }: { language: Language; papers: Paper[]; query: string; setQuery: (value: string) => void; folderName?: string; selectedPaperIds: Set<string>; onToggleSelection: (paperId: string, selected: boolean) => void; onToggleAllSelection: (selected: boolean, papers: Paper[]) => void; onBatchDelete: () => void; onImport: () => void; onOpen: (paper: Paper) => void; onRename: (paper: Paper) => void; onManageFolders: (paper: Paper) => void; onDelete: (paper: Paper) => void }) {
  const copy = getCopy(language).library
  const selectablePapers = papers.filter((paper) => paper.parseStatus !== 'parsing')
  const selectedVisibleCount = selectablePapers.filter((paper) => selectedPaperIds.has(paper.id)).length
  const allVisibleSelected = selectablePapers.length > 0 && selectedVisibleCount === selectablePapers.length
  return <div className="page-content library-page">
    <header className="page-header">
      <div><div className="eyebrow">{copy.eyebrow} <span className="eyebrow-line" /></div><h1>{folderName ?? copy.title}</h1><p className="page-subtitle">{folderName ? copy.folderSubtitle(folderName) : copy.subtitle}</p></div>
      <div className="header-actions"><button className="icon-button" title={copy.help}><CircleHelp size={18} /></button><button className="primary-button" onClick={onImport}><Plus size={17} />{copy.import}</button></div>
    </header>
    <section className="library-toolbar"><div className="search-box"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={copy.search} /></div><div className="library-selection-actions"><label className="select-all-control"><input type="checkbox" aria-label={copy.selectAll} checked={allVisibleSelected} disabled={selectablePapers.length === 0} onChange={(event) => onToggleAllSelection(event.target.checked, papers)} /><span>{copy.selectAll}</span></label>{selectedVisibleCount > 0 && <button className="batch-delete-button" onClick={onBatchDelete}>{copy.batchDelete} ({selectedVisibleCount})</button>}</div></section>
    <section className="library-summary"><div><span className="summary-number">{papers.length}</span><span className="summary-label">{copy.papers}</span></div><div><span className="summary-number mint-text">{papers.reduce((total, paper) => total + paper.symbols.length, 0)}</span><span className="summary-label">{copy.parsed}</span></div><div><span className="summary-number amber-text">{papers.filter((paper) => paper.progress < 100).length.toString().padStart(2, '0')}</span><span className="summary-label">{copy.pending}</span></div><span className="summary-note"><Sparkles size={14} /> {copy.note}</span></section>
    <div className="section-heading"><h2>{copy.recent}</h2><button className="quiet-button">{copy.all} <ChevronRight size={15} /></button></div>
    <section className="paper-grid">{papers.length === 0 && <div className="library-empty-state"><Search size={24} /><strong>{query ? copy.noMatch : copy.empty}</strong><span>{query ? copy.noMatchHint : copy.emptyHint}</span></div>}{papers.map((paper) => <PaperCard key={paper.id} language={language} paper={paper} selected={selectedPaperIds.has(paper.id)} deleteLabel={folderName ? copy.removeFromFolder : copy.delete} onToggleSelection={(selected) => onToggleSelection(paper.id, selected)} onOpen={() => onOpen(paper)} onRename={() => onRename(paper)} onManageFolders={() => onManageFolders(paper)} onDelete={() => onDelete(paper)} />)}<button className="add-card" onClick={onImport}><Upload size={21} /><strong>{copy.importNew}</strong><span>{copy.localStorage}</span></button></section>
  </div>
}

function PaperCard({ language, paper, selected, deleteLabel, onToggleSelection, onOpen, onRename, onManageFolders, onDelete }: { language: Language; paper: Paper; selected: boolean; deleteLabel: string; onToggleSelection: (selected: boolean) => void; onOpen: () => void; onRename: () => void; onManageFolders: () => void; onDelete: () => void }) {
  const copy = getCopy(language).library
  const [menuOpen, setMenuOpen] = useState(false)
  return <article className={`paper-card${selected ? ' selected' : ''}${menuOpen ? ' menu-open' : ''}`} role="button" tabIndex={0} aria-label={`${copy.open} ${paper.title}`} onClick={onOpen} onKeyDown={(event) => { if (event.target !== event.currentTarget) return; if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onOpen() } }} title={paper.parseError} style={{ '--paper-accent': paper.accent } as React.CSSProperties}>
    <div className="paper-card-top"><label className="paper-select-control"><input type="checkbox" aria-label={`${copy.select} ${paper.title}`} checked={selected} disabled={paper.parseStatus === 'parsing'} onClick={(event) => event.stopPropagation()} onChange={(event) => { event.stopPropagation(); onToggleSelection(event.target.checked) }} /><span /></label><span className="paper-type">PDF</span><div className={menuOpen ? 'card-menu open' : 'card-menu'}><button className="card-more" aria-label={`${copy.manage} ${paper.title}`} aria-expanded={menuOpen} onClick={(event) => { event.stopPropagation(); setMenuOpen((value) => !value) }}><MoreHorizontal size={17} /></button><div className="card-menu-popover"><button disabled={paper.parseStatus === 'parsing'} title={paper.parseStatus === 'parsing' ? copy.deletingDisabled : undefined} onClick={(event) => { event.stopPropagation(); setMenuOpen(false); onManageFolders() }}><FolderPlus size={13} />{copy.addToFolder}</button><button onClick={(event) => { event.stopPropagation(); setMenuOpen(false); onRename() }}>{copy.rename}</button><button disabled={paper.parseStatus === 'parsing'} title={paper.parseStatus === 'parsing' ? copy.deletingDisabled : undefined} onClick={(event) => { event.stopPropagation(); setMenuOpen(false); onDelete() }}>{deleteLabel}</button></div></div></div>
    <div className="paper-card-body"><div className="paper-info"><h3>{paper.title}</h3><p className="paper-authors">{displayAuthors(paper.authors, language)}</p><p className="paper-meta">{paper.year} <span>·</span> {paper.pages ? `${paper.pages} ${copy.pages}` : copy.pendingParse} <span>·</span> {paper.size}</p></div></div>
    <div className="paper-card-footer"><span className="added-time">{paper.parseStatus === 'parsing' ? copy.parsing : paper.parseStatus === 'error' ? copy.parseFailed : paper.visualReviewItems ? `${copy.visualReview} · ${paper.visualReviewItems}` : paper.reviewSymbolCount ? `${copy.symbolReview} · ${paper.reviewSymbolCount}` : paper.addedAt}</span><div className="progress-wrap"><div className="progress-track"><div className="progress-value" style={{ width: `${paper.progress}%` }} /></div><span>{paper.parseStatus === 'parsing' ? '…' : `${paper.progress}%`}</span></div></div>
  </article>
}

function FolderAssignmentDialog({ language, paper, folders, selectedIds, saving, onToggle, onCreateFolder, onClose, onSave }: { language: Language; paper: Paper; folders: PaperFolder[]; selectedIds: Set<string>; saving: boolean; onToggle: (folderId: string) => void; onCreateFolder: () => void; onClose: () => void; onSave: () => void }) {
  const copy = getCopy(language).library
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !saving) onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose, saving])

  return <div className="folder-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <section className="folder-dialog" role="dialog" aria-modal="true" aria-labelledby="folder-dialog-title">
      <header className="folder-dialog-header"><div><span className="folder-dialog-eyebrow">{copy.folderDialogTitle}</span><h2 id="folder-dialog-title">{paper.title}</h2></div><button className="icon-button" aria-label={copy.cancel} title={copy.cancel} disabled={saving} onClick={onClose}><X size={17} /></button></header>
      <div className="folder-dialog-list">
        {folders.length === 0 && <div className="folder-dialog-empty"><FolderOpen size={22} /><span>{copy.noFolders}</span></div>}
        {folders.map((folder) => <label className="folder-choice" key={folder.id}><span className="folder-choice-icon"><Folder size={16} /></span><span className="folder-choice-name">{folder.name}</span><input type="checkbox" checked={selectedIds.has(folder.id)} onChange={() => onToggle(folder.id)} /><span className="folder-choice-check"><Check size={14} /></span></label>)}
      </div>
      <footer className="folder-dialog-footer"><button className="quiet-button folder-dialog-create" type="button" disabled={saving} onClick={onCreateFolder}><FolderPlus size={15} />{copy.createFolder}</button><div><button className="outline-button" type="button" disabled={saving} onClick={onClose}>{copy.cancel}</button><button className="primary-button" type="button" disabled={saving} onClick={onSave}><Check size={15} />{saving ? copy.saving : copy.save}</button></div></footer>
    </section>
  </div>
}

function ReaderView({ language, paper, pageNumber, onPageChange, symbol, onSelectSymbol, onBack, onGenerateSummaries, summaryBusy, fontSize }: { language: Language; paper: Paper; pageNumber: number; onPageChange: (page: number) => void; symbol: SymbolDefinition | null; onSelectSymbol: (symbol: SymbolDefinition | null) => void; onBack: () => void; onGenerateSummaries: () => void; summaryBusy: boolean; fontSize: number }) {
  const copy = getCopy(language).reader
  const [returnPage, setReturnPage] = useState<number | null>(null)
  const [pageInput, setPageInput] = useState(String(pageNumber))
  const [renderedPage, setRenderedPage] = useState<RenderResponse | null>(null)
  const [renderError, setRenderError] = useState('')
  const [leftRailWidth, setLeftRailWidth] = useState(() => Number(localStorage.getItem('phireader.leftRailWidth')) || 92)
  const [rightPanelWidth, setRightPanelWidth] = useState(() => Number(localStorage.getItem('phireader.rightPanelWidth')) || 310)
  const [pdfScale, setPdfScale] = useState(() => Number(localStorage.getItem('phireader.pdfScale')) || 1)
  const desktop = Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
  const changePdfScale = (delta: number) => setPdfScale((scale) => {
    const next = Math.min(1.8, Math.max(0.2, Number((scale + delta * 0.1).toFixed(2))))
    localStorage.setItem('phireader.pdfScale', String(next))
    return next
  })

  const nudgeSidebar = (side: 'left' | 'right', delta: number) => {
    const available = document.querySelector<HTMLElement>('.main-area')?.clientWidth || window.innerWidth
    const minimumDocument = 360
    if (side === 'left') {
      const next = Math.round(Math.min(Math.max(72, available - rightPanelWidth - minimumDocument), Math.max(72, leftRailWidth + delta)))
      setLeftRailWidth(next)
      localStorage.setItem('phireader.leftRailWidth', String(next))
    } else {
      const next = Math.round(Math.min(Math.max(240, available - leftRailWidth - minimumDocument), Math.max(240, rightPanelWidth + delta)))
      setRightPanelWidth(next)
      localStorage.setItem('phireader.rightPanelWidth', String(next))
    }
  }

  const startSidebarResize = (side: 'left' | 'right', event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
    const startX = event.clientX
    const startWidth = side === 'left' ? leftRailWidth : rightPanelWidth
    const available = document.querySelector<HTMLElement>('.main-area')?.clientWidth || window.innerWidth
    const minimumDocument = 360
    let finalWidth = startWidth
    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const update = (clientX: number) => {
      const delta = clientX - startX
      const raw = side === 'left' ? startWidth + delta : startWidth - delta
      const minimum = side === 'left' ? 72 : 240
      const maximum = side === 'left'
        ? Math.max(minimum, available - rightPanelWidth - minimumDocument)
        : Math.max(minimum, available - leftRailWidth - minimumDocument)
      finalWidth = Math.round(Math.min(maximum, Math.max(minimum, raw)))
      if (side === 'left') setLeftRailWidth(finalWidth)
      else setRightPanelWidth(finalWidth)
    }
    const handleMove = (moveEvent: PointerEvent) => update(moveEvent.clientX)
    const finish = () => {
      window.removeEventListener('pointermove', handleMove)
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('pointercancel', finish)
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      localStorage.setItem(side === 'left' ? 'phireader.leftRailWidth' : 'phireader.rightPanelWidth', String(finalWidth))
    }
    window.addEventListener('pointermove', handleMove)
    window.addEventListener('pointerup', finish)
    window.addEventListener('pointercancel', finish)
  }

  const handleResizeKey = (side: 'left' | 'right', event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      nudgeSidebar(side, side === 'left' ? -8 : 8)
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      nudgeSidebar(side, side === 'left' ? 8 : -8)
    }
  }

  useEffect(() => {
    setReturnPage(null)
    setPageInput(String(pageNumber))
    setRenderedPage(null)
    setRenderError('')
  }, [paper.id])

  useEffect(() => {
    setPageInput(String(pageNumber))
  }, [pageNumber])

  useEffect(() => {
    if (!desktop || !paper.filePath || !paper.parsePath) return
    let cancelled = false
    setRenderedPage(null)
    setRenderError('')
    invoke<RenderResponse>('render_pdf_page', {
      paperPath: paper.filePath,
      parsePath: paper.parsePath,
      page: pageNumber,
    }).then((response) => {
      if (!cancelled) setRenderedPage(response)
    }).catch((error) => {
      if (!cancelled) setRenderError(error instanceof Error ? error.message : String(error))
    })
    return () => { cancelled = true }
  }, [desktop, paper.filePath, paper.parsePath, pageNumber])

  const pageButtons = Array.from({ length: Math.min(paper.pages || 1, 5) }, (_, index) => index + 1)
  const jumpToPage = (page: number) => onPageChange(Math.min(Math.max(1, page), Math.max(1, paper.pages || 1)))
  const jumpToDefinitionPage = (page: number) => {
    setReturnPage((current) => current ?? pageNumber)
    jumpToPage(page)
  }
  const returnToReadingPage = () => {
    if (returnPage === null) return
    jumpToPage(returnPage)
    setReturnPage(null)
  }
  const commitPageInput = () => {
    const parsed = Number.parseInt(pageInput, 10)
    if (Number.isFinite(parsed)) jumpToPage(parsed)
    else setPageInput(String(pageNumber))
  }
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' || target?.isContentEditable) return
      if (event.key === 'ArrowRight' || event.key === 'PageDown') {
        event.preventDefault()
        jumpToPage(pageNumber + 1)
      } else if (event.key === 'ArrowLeft' || event.key === 'PageUp') {
        event.preventDefault()
        jumpToPage(pageNumber - 1)
      } else if (event.key === 'Home') {
        event.preventDefault()
        jumpToPage(1)
      } else if (event.key === 'End') {
        event.preventDefault()
        jumpToPage(paper.pages || 1)
      } else if (event.key === '+' || event.key === '=') {
        event.preventDefault()
        changePdfScale(1)
      } else if (event.key === '-') {
        event.preventDefault()
        changePdfScale(-1)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [pageNumber, paper.pages])
  useEffect(() => {
    const handleWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return
      const target = event.target
      if (!(target instanceof Element) || !target.closest('.reader-page')) return
      event.preventDefault()
      changePdfScale(event.deltaY < 0 ? 1 : -1)
    }
    window.addEventListener('wheel', handleWheel, { passive: false })
    return () => window.removeEventListener('wheel', handleWheel)
  }, [])
  return <div className="reader-page" style={{ '--left-rail-width': `${leftRailWidth}px`, '--right-panel-width': `${rightPanelWidth}px` } as React.CSSProperties}>
    <div className="reader-resize-handle reader-resize-handle-left" role="separator" aria-orientation="vertical" aria-label={language === 'en' ? 'Resize page thumbnail rail' : '调整页面缩略图栏宽度'} tabIndex={0} onPointerDown={(event) => startSidebarResize('left', event)} onKeyDown={(event) => handleResizeKey('left', event)} />
    <div className="reader-resize-handle reader-resize-handle-right" role="separator" aria-orientation="vertical" aria-label={language === 'en' ? 'Resize symbol sidebar' : '调整符号侧栏宽度'} tabIndex={0} onPointerDown={(event) => startSidebarResize('right', event)} onKeyDown={(event) => handleResizeKey('right', event)} />
    <header className="reader-header"><button className="back-button" onClick={onBack}><ArrowLeft size={17} />{copy.back}</button><div className="reader-title"><span className="reader-status"><span />{copy.reading}</span><strong>{paper.title}</strong><small>{displayAuthors(paper.authors, language)}</small></div><div className="reader-tools"><div className="page-navigation"><button className="icon-button" title={copy.previousPage} disabled={pageNumber <= 1} onClick={() => jumpToPage(pageNumber - 1)}><ChevronLeft size={16} /></button><label className="page-readout"><span>{copy.page}</span><input aria-label={copy.page} inputMode="numeric" value={pageInput} onChange={(event) => setPageInput(event.target.value)} onBlur={commitPageInput} onKeyDown={(event) => { if (event.key === 'Enter') { commitPageInput(); event.currentTarget.blur() } }} /><span>/ {paper.pages || 1}</span></label><button className="icon-button" title={copy.nextPage} disabled={pageNumber >= (paper.pages || 1)} onClick={() => jumpToPage(pageNumber + 1)}><ChevronRight size={16} /></button></div><button className="summary-button" type="button" onClick={onGenerateSummaries} disabled={summaryBusy || paper.symbols.length === 0} title={paper.symbols.length === 0 ? copy.noSymbols : copy.summary}><Sparkles size={15} />{summaryBusy ? copy.summarizing : copy.summary}</button><button className="icon-button" title={copy.zoomOut} onClick={() => changePdfScale(-1)}><ZoomOut size={17} /></button><span className="zoom-value">{Math.round(pdfScale * 100)}%</span><button className="icon-button" title={copy.zoomIn} onClick={() => changePdfScale(1)}><ZoomIn size={17} /></button><button className="icon-button"><MoreHorizontal size={18} /></button></div></header>
    <div className="reader-layout"><aside className="page-rail"><div className="rail-label">PAGES</div>{pageButtons.map((page) => <PageThumbnail key={page} paper={paper} page={page} active={page === pageNumber} onClick={() => jumpToPage(page)} />)}{(paper.pages || 0) > 5 && <><span className="rail-more">···</span><button className="page-jump" onClick={() => jumpToPage(pageNumber >= paper.pages ? 1 : pageNumber + 1)}>{copy.nextPage}</button></>}{paper.pages > 0 && <span className="page-total">{paper.pages} {language === 'en' ? 'pages' : '页'}</span>}</aside>{paper.parseStatus === 'parsing' || paper.parseStatus === 'error' ? <PaperReaderStatus language={language} paper={paper} /> : desktop && paper.filePath && paper.parsePath ? <RenderedPdfPage language={language} paper={paper} pageNumber={pageNumber} rendered={renderedPage} renderError={renderError} selected={symbol} onSelect={onSelectSymbol} scale={pdfScale} /> : paper.fileUrl ? <iframe className="pdf-frame" src={paper.fileUrl} title={paper.title} /> : <article className="document-view" style={{ '--reader-size': `${fontSize}px` } as React.CSSProperties}><div className="document-paper"><div className="document-running-head"><span>{paper.venue}</span><span>02</span></div><div className="document-kicker">RESEARCH ARTICLE · {paper.year}</div><h1>{paper.title}</h1><p className="document-byline">{displayAuthors(paper.authors, language)}</p><div className="document-rule" /><h2>Abstract</h2><p>{paper.abstract}</p><p>We study the geometry of non-negative representations and their role in recovering meaningful components from high-dimensional observations. The central object is a matrix factorization that keeps every component interpretable.</p><div className="equation-block"><span>V</span><span>≈</span><span>W</span><span>H</span><em>(1)</em></div><p className="equation-note">The data matrix <SymbolButton value="V" active={symbol?.surface === 'V'} onClick={() => onSelectSymbol(paper.symbols.find((item) => item.surface === 'V') ?? paper.symbols[0])} /> is approximated by the product of two non-negative matrices. <SymbolButton value="W" active={symbol?.surface === 'W'} onClick={() => onSelectSymbol(paper.symbols.find((item) => item.surface === 'W') ?? paper.symbols[0])} /> contains the basis vectors, while <SymbolButton value="H" active={symbol?.surface === 'H'} onClick={() => onSelectSymbol(paper.symbols.find((item) => item.surface === 'H') ?? paper.symbols[0])} /> contains their coefficients.</p><h2>1. Problem formulation</h2><p>Let the observed samples be arranged as columns of a non-negative matrix. The number of basis vectors is deliberately smaller than the ambient dimension, making the representation useful for discovering parts and recurring structure.</p><div className="equation-block compact"><span>D(A ‖ B)</span><span>=</span><span>Σ</span><span>d(Aᵢⱼ ‖ Bᵢⱼ)</span><em>(2)</em></div><p>The divergence <SymbolButton value="D(A ‖ B)" active={symbol?.surface.startsWith('D') ?? false} onClick={() => onSelectSymbol(paper.symbols.find((item) => item.id === 'nmf-d') ?? paper.symbols[0])} /> measures the discrepancy between two non-negative matrices.</p></div></article>}<SymbolPanel language={language} paper={paper} symbol={symbol} onSelect={(selected) => onSelectSymbol(selected)} onClear={() => onSelectSymbol(null)} onJumpToPage={jumpToDefinitionPage} onReturnToPage={returnToReadingPage} returnPage={returnPage} /></div>
  </div>
}

function PageThumbnail({ paper, page, active, onClick }: { paper: Paper; page: number; active: boolean; onClick: () => void }) {
  const [imageData, setImageData] = useState<string | null>(null)
  const desktop = Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)

  useEffect(() => {
    if (!desktop || !paper.filePath || !paper.parsePath) return
    let cancelled = false
    invoke<RenderResponse>('render_pdf_page', {
      paperPath: paper.filePath,
      parsePath: paper.parsePath,
      page,
    }).then((response) => {
      if (!cancelled) setImageData(response.imageData)
    }).catch(() => {
      if (!cancelled) setImageData(null)
    })
    return () => { cancelled = true }
  }, [desktop, paper.filePath, paper.parsePath, page])

  return <button className={active ? 'page-thumb active' : 'page-thumb'} onClick={onClick}><span>{String(page).padStart(2, '0')}</span><div className={imageData ? 'thumb-paper has-image' : 'thumb-paper'}>{imageData ? <img src={imageData} alt={`${paper.title} 第 ${page} 页缩略图`} /> : <><i /><i /><i /><b /></>}</div></button>
}

function RenderedPdfPage({ language, paper, pageNumber, rendered, renderError, selected, onSelect, scale }: { language: Language; paper: Paper; pageNumber: number; rendered: RenderResponse | null; renderError: string; selected: SymbolDefinition | null; onSelect: (symbol: SymbolDefinition) => void; scale: number }) {
  const copy = getCopy(language).reader
  if (renderError) return <div className="document-view render-state"><strong>{copy.renderFailed}</strong><span>{renderError}</span></div>
  if (!rendered) return <div className="document-view render-state"><Sparkles size={25} /><strong>{copy.rendering(pageNumber)}</strong><span>{copy.renderHint}</span></div>
  const visibleOccurrences = rendered.occurrences.filter((item) => paper.symbols.some((candidate) => (candidate.identityKey ?? candidate.surface) === (item.identityKey ?? item.surface)))
  return <div className="document-view rendered-document"><div className="rendered-page-wrap" style={{ aspectRatio: `${rendered.pageWidth} / ${rendered.pageHeight}`, width: `${900 * scale}px`, maxWidth: 'none' }}><img src={rendered.imageData} alt={copy.pdfPage(paper.title, pageNumber)} /><div className="occurrence-layer">{visibleOccurrences.map((item, index) => { const [x0, top, x1, bottom] = item.bbox; const isSelected = (item.identityKey ?? item.surface) === (selected?.identityKey ?? selected?.surface); return <button key={`${item.identityKey ?? item.surface}-${index}`} className={isSelected ? 'occurrence-hotspot selected' : 'occurrence-hotspot'} title={copy.viewSymbol(item.surface)} style={{ left: `${x0 / rendered.pageWidth * 100}%`, top: `${top / rendered.pageHeight * 100}%`, width: `${Math.max(0.8, (x1 - x0) / rendered.pageWidth * 100)}%`, height: `${Math.max(1.2, (bottom - top) / rendered.pageHeight * 100)}%` }} onClick={() => { const match = paper.symbols.find((candidate) => (candidate.identityKey ?? candidate.surface) === (item.identityKey ?? item.surface)); if (match) onSelect(match) }} /> })}</div></div></div>
}

function latexSurface(value: string): string {
  return value
    .replaceAll('‖', '\\Vert ')
    .replaceAll('≈', '\\approx ')
    .replaceAll('∈', '\\in ')
    .replaceAll('≤', '\\le ')
    .replaceAll('≥', '\\ge ')
    .replace(/_([A-Za-z0-9Α-Ωα-ω]+)/g, '_{$1}')
    .replace(/\^([A-Za-z0-9Α-Ωα-ω]+)/g, '^{$1}')
}

function MathSurface({ value, style, className = '' }: { value: string; style?: string; className?: string }) {
  const html = useMemo(() => {
    const source = latexSurface(value)
    const styleCommand = value.length === 1 ? ({
      roman: '\\mathrm', italic: '\\mathit', bold: '\\mathbf', bold_italic: '\\boldsymbol',
      blackboard: '\\mathbb', fraktur: '\\mathfrak', script: '\\mathcal', sans: '\\mathsf', monospace: '\\mathtt',
    } as Record<string, string>)[style ?? ''] : undefined
    return katex.renderToString(styleCommand ? `${styleCommand}{${source}}` : source, { displayMode: false, throwOnError: false, trust: false })
  }, [value, style])
  return <span className={`math-surface ${className}`.trim()} dangerouslySetInnerHTML={{ __html: html }} />
}

function SymbolButton({ value, active, onClick }: { value: string; active: boolean; onClick: () => void }) { return <button aria-label={value} className={active ? 'symbol-button active' : 'symbol-button'} onClick={onClick}><MathSurface value={value} /></button> }

function SymbolPanel({ language, paper, symbol, onSelect, onClear, onJumpToPage, onReturnToPage, returnPage }: { language: Language; paper: Paper; symbol: SymbolDefinition | null; onSelect: (symbol: SymbolDefinition) => void; onClear: () => void; onJumpToPage: (page: number) => void; onReturnToPage: () => void; returnPage: number | null }) {
  const copy = getCopy(language).reader
  const panelRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    panelRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
  }, [symbol?.id])
  const sourcePage = symbol?.location.match(/第\s*(\d+)\s*页/)?.[1]
  const hasGeneratedMeaning = (item: SymbolDefinition | null | undefined) => item?.meaning_source === 'qwen' || item?.meaning_source === 'deepseek' || item?.meaning_source === 'remote'
  const displayMeaning = hasGeneratedMeaning(symbol) ? symbol?.meaning : copy.summaryPlaceholder
  return <aside className="symbol-panel"><div className="panel-heading"><div><span className="panel-eyebrow"><Sparkles size={13} />{copy.authorDefined}</span><h2>{copy.symbolIndex}</h2></div><button className="icon-button" type="button" aria-label={copy.clearSelection} title={copy.clearSelection} onClick={(event) => { event.stopPropagation(); onClear() }}><X size={17} /></button></div><div className="panel-rule" /><div ref={panelRef} className="panel-scroll">{symbol ? <div className="symbol-detail"><div className="symbol-hero"><span className="hero-symbol"><MathSurface value={symbol.surface} style={symbol.style} /></span><div><span className={symbol.status === 'review' ? 'confidence review' : 'confidence'}>{symbol.status === 'review' ? copy.review : copy.confirmed}</span><span className="occurrence-count">{symbol.occurrences} {copy.occurrences}</span></div></div><p className="symbol-meaning">{displayMeaning}</p><button className="source-link definition-jump" disabled={!sourcePage} title={sourcePage ? copy.viewDefinition : copy.definitionPageUnavailable} onClick={() => sourcePage && onJumpToPage(Number(sourcePage))}><FileText size={14} />{copy.viewDefinition}{sourcePage ? ` · ${symbol.location}` : ''}<ChevronRight size={14} /></button>{returnPage !== null && <button className="source-link return-link" type="button" title={copy.returnPosition(returnPage)} onClick={onReturnToPage}><RotateCcw size={14} />{copy.returnPosition(returnPage)}</button>}<div className="panel-section-title">{copy.otherSymbols} <span>{paper.symbols.length}</span></div><div className="symbol-list">{paper.symbols.map((item) => <button key={item.id} className={item.id === symbol.id ? 'symbol-list-item selected' : 'symbol-list-item'} onClick={() => onSelect(item)}><span className="list-surface"><MathSurface value={item.surface} style={item.style} /></span><span className="list-meaning">{hasGeneratedMeaning(item) ? item.meaning : copy.summaryPlaceholder}</span><span className="list-count">{item.occurrences}</span></button>)}</div></div> : <div className="empty-symbol"><Sparkles size={25} /><strong>{copy.selectSymbol}</strong><p>{copy.selectHint}</p></div>}</div><div className="panel-footer"><span className="footer-dot" />{copy.footerHint}</div></aside>
}

function SettingsView({ language, setInterfaceLanguage, summaryLanguage, setSummaryLanguage, dark, onToggleTheme, fontSize, setFontSize, apiKey, apiKeyConfigured, setApiKey, modelProvider, setModelProvider, modelName, setModelName, customModelName, setCustomModelName, customEndpoint, setCustomEndpoint }: { language: Language; setInterfaceLanguage: (value: Language) => void; summaryLanguage: Language; setSummaryLanguage: (value: Language) => void; dark: boolean; onToggleTheme: () => void; fontSize: number; setFontSize: (value: number) => void; apiKey: string; apiKeyConfigured: boolean; setApiKey: (value: string) => void; modelProvider: ModelProvider; setModelProvider: (value: ModelProvider) => void; modelName: string; setModelName: (value: string) => void; customModelName: string; setCustomModelName: (value: string) => void; customEndpoint: string; setCustomEndpoint: (value: string) => void }) {
  const copy = getCopy(language).settings
  const modelOptions = modelProvider === 'custom' ? [] : PROVIDER_MODELS[modelProvider]
  return <div className="page-content settings-page"><header className="page-header"><div><div className="eyebrow">{copy.eyebrow} <span className="eyebrow-line" /></div><h1>{copy.title}</h1><p className="page-subtitle">{copy.subtitle}</p></div></header><div className="settings-grid"><section className="settings-card"><div className="settings-card-head"><div className="setting-icon amber-bg"><Sun size={18} /></div><div><h2>{copy.appearance}</h2><p>{copy.appearanceHint}</p></div></div><div className="setting-row"><div><strong>{copy.theme}</strong><small>{copy.themeHint}</small></div><button className="theme-toggle" onClick={onToggleTheme}><span className={dark ? 'theme-option active' : 'theme-option'}><Moon size={14} />{copy.dark}</span><span className={!dark ? 'theme-option active' : 'theme-option'}><Sun size={14} />{copy.light}</span></button></div><div className="setting-row"><div><strong>{copy.bodySize}</strong><small>{copy.bodySizeHint(fontSize)}</small></div><div className="stepper"><button onClick={() => setFontSize(Math.max(15, fontSize - 1))}>−</button><span>{fontSize}</span><button onClick={() => setFontSize(Math.min(22, fontSize + 1))}>＋</button></div></div><div className="setting-row api-row"><div><strong>{copy.interfaceLanguage}</strong><small>{copy.interfaceLanguageHint}</small></div><select aria-label={copy.interfaceLanguage} className="language-select" value={language} onChange={(event) => setInterfaceLanguage(event.target.value as Language)}><option value="zh-CN">{copy.chinese}</option><option value="en">{copy.english}</option></select></div></section><section className="settings-card"><div className="settings-card-head"><div className="setting-icon mint-bg"><Sparkles size={18} /></div><div><h2>{copy.engine}</h2><p>{copy.engineHint}</p></div></div><div className="setting-row"><div><strong>{copy.localMode}</strong><small>{copy.localModeHint}</small></div><span className="enabled-tag"><Check size={13} />{copy.enabled}</span></div><div className="setting-row api-row"><div><strong>{copy.apiKey}</strong><small>{copy.apiKeyHint}</small></div><div className="api-input-wrap"><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={apiKeyConfigured ? MASKED_API_KEY : copy.pasteKey} autoComplete="off" /><span>{apiKeyConfigured ? copy.saved : copy.notConfigured}</span></div></div><div className="setting-row api-row"><div><strong>{copy.provider}</strong><small>{copy.providerHint}</small></div><select aria-label={copy.provider} className="language-select model-select" value={modelProvider} onChange={(event) => setModelProvider(event.target.value as ModelProvider)}><option value="qwen">Qwen</option><option value="deepseek">DeepSeek</option><option value="custom">{copy.custom}</option></select></div><div className="setting-row api-row"><div><strong>{copy.model}</strong><small>{copy.modelHint}</small></div><div className="api-input-wrap">{modelProvider === 'custom' ? <input type="text" value={customModelName} onChange={(event) => setCustomModelName(event.target.value)} placeholder={copy.customModelPlaceholder} autoComplete="off" /> : <select aria-label={copy.model} className="language-select model-select" value={modelName} onChange={(event) => setModelName(event.target.value)}>{modelOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>}<span>{(modelProvider === 'custom' ? customModelName : modelName).trim() ? copy.saved : copy.notConfigured}</span></div></div>{modelProvider === 'custom' && <div className="setting-row api-row"><div><strong>{copy.customEndpoint}</strong><small>{copy.customEndpointHint}</small></div><div className="api-input-wrap"><input type="url" value={customEndpoint} onChange={(event) => setCustomEndpoint(event.target.value)} placeholder={copy.customEndpointPlaceholder} autoComplete="off" /><span>{customEndpoint.trim() ? copy.saved : copy.notConfigured}</span></div></div>}<div className="setting-row api-row"><div><strong>{copy.summaryLanguage}</strong><small>{copy.summaryLanguageHint}</small></div><select aria-label={copy.summaryLanguage} className="language-select" value={summaryLanguage} onChange={(event) => setSummaryLanguage(event.target.value as Language)}><option value="zh-CN">{copy.chinese}</option><option value="en">{copy.english}</option></select></div></section></div><div className="settings-note"><CircleHelp size={16} /><span>{copy.note}</span></div></div>
}

export default App
