export type Language = 'zh-CN' | 'en'

export type Copy = {
  sidebar: {
    library: string
    reader: string
    workspace: string
    localLibrary: string
    parsedSymbols: string
    folders: string
    allPapers: string
    createFolder: string
    deleteFolder: string
    settings: string
    parser: string
    ready: string
  }
  library: {
    eyebrow: string
    title: string
    subtitle: string
    help: string
    import: string
    search: string
    papers: string
    parsed: string
    pending: string
    note: string
    recent: string
    all: string
    noMatch: string
    empty: string
    noMatchHint: string
    emptyHint: string
    importNew: string
    localStorage: string
    dropTitle: (count: number) => string
    dropHint: string
    pdfOnly: string
    importQueued: (accepted: number, rejected: number) => string
    importFinished: (succeeded: number, failed: number) => string
    browserQueued: (count: number) => string
    rename: string
    delete: string
    select: string
    selectAll: string
    batchDelete: string
    deletingDisabled: string
    open: string
    manage: string
    parsing: string
    parseFailed: string
    visualReview: string
    symbolReview: string
    pendingParse: string
    pages: string
    folderSubtitle: (name: string) => string
    removeFromFolder: string
    addToFolder: string
    folderDialogTitle: string
    noFolders: string
    createFolder: string
    cancel: string
    save: string
    saving: string
  }
  reader: {
    back: string
    reading: string
    previousPage: string
    nextPage: string
    page: string
    pagesLabel: string
    clearSelection: string
    summary: string
    summarizing: string
    noSymbols: string
    zoomOut: string
    zoomIn: string
    symbolIndex: string
    authorDefined: string
    confirmed: string
    review: string
    occurrences: string
    summaryPlaceholder: string
    viewDefinition: string
    definitionPageUnavailable: string
    returnPosition: (page: number) => string
    otherSymbols: string
    selectSymbol: string
    selectHint: string
    footerHint: string
    noPaper: string
    noPaperHint: string
    returnLibrary: string
    parseFailedTitle: string
    parsingTitle: string
    parseFailedHint: string
    parsingHint: string
    renderFailed: string
    rendering: (page: number) => string
    renderHint: string
    pdfPage: (title: string, page: number) => string
    viewSymbol: (surface: string) => string
  }
  settings: {
    eyebrow: string
    title: string
    subtitle: string
    appearance: string
    appearanceHint: string
    theme: string
    themeHint: string
    dark: string
    light: string
    bodySize: string
    bodySizeHint: (size: number) => string
    engine: string
    engineHint: string
    localMode: string
    localModeHint: string
    enabled: string
    apiKey: string
    apiKeyHint: string
    pasteKey: string
    saved: string
    notConfigured: string
    model: string
    modelHint: string
    provider: string
    providerHint: string
    custom: string
    customEndpoint: string
    customEndpointHint: string
    customEndpointPlaceholder: string
    customModelPlaceholder: string
    interfaceLanguage: string
    interfaceLanguageHint: string
    summaryLanguage: string
    summaryLanguageHint: string
    chinese: string
    english: string
    note: string
  }
}

const zh: Copy = {
  sidebar: { library: '论文库', reader: '当前阅读', workspace: 'WORKSPACE', localLibrary: '本地论文库', parsedSymbols: '已解析符号', folders: '目录', allPapers: '全部论文', createFolder: '新建目录', deleteFolder: '删除目录', settings: '设置', parser: '解析引擎', ready: '本地模式 · 就绪' },
  library: { eyebrow: 'YOUR RESEARCH DESK', title: '论文库', subtitle: '把值得反复阅读的论文，放在一个有上下文的地方。', help: '帮助', import: '导入论文', search: '搜索标题、作者或期刊…', papers: '篇论文', parsed: '个已解析符号', pending: '篇待解析', note: 'Phi 会记住你读过的定义', recent: '最近添加', all: '查看全部', noMatch: '没有找到匹配的论文', empty: '论文库还是空的', noMatchHint: '试试标题、作者或期刊中的其他关键词。', emptyHint: '导入一篇本地 PDF，开始建立你的符号索引。', importNew: '导入一篇新论文', localStorage: 'PDF · 本地存储', dropTitle: (count) => count > 1 ? `释放以快速上传 ${count} 个文件` : '释放以快速上传 PDF', dropHint: '松开鼠标后，文件将加入本地解析队列', pdfOnly: '仅支持 PDF 文件', importQueued: (accepted, rejected) => rejected > 0 ? `已加入 ${accepted} 个 PDF，跳过 ${rejected} 个非 PDF 文件` : `已加入 ${accepted} 个 PDF 的解析队列`, importFinished: (succeeded, failed) => failed > 0 ? `批量导入完成：${succeeded} 个成功，${failed} 个失败` : `${succeeded} 个 PDF 已完成本地解析`, browserQueued: (count) => `已加入 ${count} 个 PDF；桌面版启动后将自动解析`, rename: '重命名', delete: '永久删除', select: '选择', selectAll: '全选', batchDelete: '批量处理', deletingDisabled: '解析完成前不可操作', open: '打开', manage: '管理', parsing: '正在解析 · 本地', parseFailed: '解析失败', visualReview: '待视觉复核', symbolReview: '待确认符号', pendingParse: '待解析', pages: '页', folderSubtitle: (name) => `“${name}”中的论文仍只保存一份，本目录记录它们的引用。`, removeFromFolder: '从目录移除', addToFolder: '添加到目录', folderDialogTitle: '添加到目录', noFolders: '暂无自定义目录', createFolder: '新建目录', cancel: '取消', save: '保存', saving: '保存中…' },
  reader: { back: '论文库', reading: 'READING', previousPage: '上一页', nextPage: '下一页', page: '第', pagesLabel: '页', clearSelection: '清除符号选择', summary: '生成摘要', summarizing: '生成中…', noSymbols: '没有可生成摘要的符号', zoomOut: '缩小 PDF', zoomIn: '放大 PDF', symbolIndex: '符号索引', authorDefined: 'AUTHOR DEFINED SYMBOLS', confirmed: '已确认', review: '待确认', occurrences: '次出现', summaryPlaceholder: '生成摘要后查看', viewDefinition: '查看原定义', definitionPageUnavailable: '定义页码不可用', returnPosition: (page) => `返回阅读位置（第 ${page} 页）`, otherSymbols: '本文中的其他符号', selectSymbol: '选择一个符号', selectHint: '点击正文中的高亮符号，或从符号列表选择一个定义。', footerHint: '点击正文中的高亮符号查看定义', noPaper: '还没有打开的论文', noPaperHint: '从论文库导入或打开一篇 PDF，开始阅读作者定义的符号。', returnLibrary: '返回论文库', parseFailedTitle: '论文解析失败', parsingTitle: '论文正在解析', parseFailedHint: '本地解析器没有返回详细错误。请从论文库删除后重新导入。', parsingHint: '解析完成后即可阅读原始 PDF，并点击正文中的符号查看定义。', renderFailed: '页面渲染失败', rendering: (page) => `正在渲染第 ${page} 页`, renderHint: '按需生成页面图像，不会预先渲染整篇论文。', pdfPage: (title, page) => `${title} 第 ${page} 页`, viewSymbol: (surface) => `查看 ${surface} 的定义` },
  settings: { eyebrow: 'PREFERENCES', title: '设置', subtitle: '让阅读环境适合你的注意力。', appearance: '阅读外观', appearanceHint: '调整界面和正文的显示方式', theme: '界面主题', themeHint: '选择阅读时的明暗氛围', dark: '深色', light: '浅色', bodySize: '正文大小', bodySizeHint: (size) => `当前 ${size}px · 仅影响阅读正文与页面缩放`, interfaceLanguage: '界面语言', interfaceLanguageHint: '修改软件界面文字和系统字体', engine: '解析引擎', engineHint: '控制作者定义符号的识别方式', localMode: '本地优先模式', localModeHint: 'PDF 字体和版面解析始终在本机完成', enabled: '已启用', apiKey: '当前服务商 API Key', apiKeyHint: 'Qwen、DeepSeek 和自定义服务商分别保存 Key，不会跨服务商发送', pasteKey: '粘贴 API Key', saved: '已保存', notConfigured: '未配置', provider: '服务商', providerHint: '每个服务商使用自己的官方端点和模型目录，不要跨服务商混用模型或 Key', custom: '自定义模型', model: '模型名称', modelHint: '选择当前服务商的模型 ID（如 Qwen qwen3.7-flash 或 DeepSeek deepseek-flash）', customEndpoint: 'API 地址', customEndpointHint: '填写完整的 Chat Completions 地址', customEndpointPlaceholder: 'https://example.com/v1/chat/completions', customModelPlaceholder: '例如 my-model', summaryLanguage: '摘要语言', summaryLanguageHint: '修改新生成的符号摘要语言', chinese: '简体中文', english: 'English', note: '论文默认只在本机解析；只有配置 API Key 后，定义原文片段才会发送给所选模型。自定义模型需要提供 OpenAI 兼容的 Chat Completions 地址。' },
}

const en: Copy = {
  sidebar: { library: 'Library', reader: 'Reading', workspace: 'WORKSPACE', localLibrary: 'Local papers', parsedSymbols: 'Parsed symbols', folders: 'FOLDERS', allPapers: 'All papers', createFolder: 'New folder', deleteFolder: 'Delete folder', settings: 'Settings', parser: 'Parser', ready: 'Local mode · Ready' },
  library: { eyebrow: 'YOUR RESEARCH DESK', title: 'Library', subtitle: 'Keep the papers worth returning to in one contextual workspace.', help: 'Help', import: 'Import paper', search: 'Search title, author, or venue…', papers: 'papers', parsed: 'parsed symbols', pending: 'pending', note: 'Phi remembers the definitions you read', recent: 'Recently added', all: 'View all', noMatch: 'No matching papers', empty: 'Your library is empty', noMatchHint: 'Try another title, author, or venue keyword.', emptyHint: 'Import a local PDF to start building your symbol index.', importNew: 'Import a new paper', localStorage: 'PDF · Local storage', dropTitle: (count) => count > 1 ? `Drop to quick-upload ${count} files` : 'Drop to quick-upload PDF', dropHint: 'Release to add the files to the local parsing queue', pdfOnly: 'PDF files only', importQueued: (accepted, rejected) => rejected > 0 ? `Queued ${accepted} PDF${accepted === 1 ? '' : 's'}; skipped ${rejected} non-PDF file${rejected === 1 ? '' : 's'}` : `Queued ${accepted} PDF${accepted === 1 ? '' : 's'} for parsing`, importFinished: (succeeded, failed) => failed > 0 ? `Batch complete: ${succeeded} imported, ${failed} failed` : `${succeeded} PDF${succeeded === 1 ? '' : 's'} parsed locally`, browserQueued: (count) => `Added ${count} PDF${count === 1 ? '' : 's'}; parsing starts in the desktop app`, rename: 'Rename', delete: 'Delete permanently', select: 'Select', selectAll: 'Select all', batchDelete: 'Process selected', deletingDisabled: 'Unavailable while parsing', open: 'Open', manage: 'Manage', parsing: 'Parsing · Local', parseFailed: 'Parse failed', visualReview: 'Visual review', symbolReview: 'Symbol review', pendingParse: 'Pending parse', pages: 'pages', folderSubtitle: (name) => `Papers in “${name}” remain single stored files; this folder keeps references.`, removeFromFolder: 'Remove from folder', addToFolder: 'Add to folder', folderDialogTitle: 'Add to folders', noFolders: 'No custom folders', createFolder: 'New folder', cancel: 'Cancel', save: 'Save', saving: 'Saving…' },
  reader: { back: 'Library', reading: 'READING', previousPage: 'Previous page', nextPage: 'Next page', page: 'Page', pagesLabel: 'pages', clearSelection: 'Clear symbol selection', summary: 'Generate summary', summarizing: 'Generating…', noSymbols: 'No symbols available for summary', zoomOut: 'Zoom out PDF', zoomIn: 'Zoom in PDF', symbolIndex: 'Symbol index', authorDefined: 'AUTHOR DEFINED SYMBOLS', confirmed: 'Confirmed', review: 'Needs review', occurrences: 'occurrences', summaryPlaceholder: 'View after generating summary', viewDefinition: 'View original definition', definitionPageUnavailable: 'Definition page unavailable', returnPosition: (page) => `Return to reading position (page ${page})`, otherSymbols: 'Other symbols in this paper', selectSymbol: 'Select a symbol', selectHint: 'Click a highlighted symbol in the paper or choose one from the list.', footerHint: 'Click a highlighted symbol to view its definition', noPaper: 'No paper is open', noPaperHint: 'Import or open a PDF from the library to read author-defined symbols.', returnLibrary: 'Return to library', parseFailedTitle: 'Paper parsing failed', parsingTitle: 'Paper is parsing', parseFailedHint: 'The local parser returned no details. Delete the paper from the library and import it again.', parsingHint: 'When parsing finishes, you can read the original PDF and click symbols to inspect their definitions.', renderFailed: 'Page rendering failed', rendering: (page) => `Rendering page ${page}`, renderHint: 'Pages are rendered on demand; the whole paper is not pre-rendered.', pdfPage: (title, page) => `${title}, page ${page}`, viewSymbol: (surface) => `View the definition of ${surface}` },
  settings: { eyebrow: 'PREFERENCES', title: 'Settings', subtitle: 'Shape the reading environment around your attention.', appearance: 'Reading appearance', appearanceHint: 'Adjust the interface and body text', theme: 'Interface theme', themeHint: 'Choose the light or dark reading atmosphere', dark: 'Dark', light: 'Light', bodySize: 'Body size', bodySizeHint: (size) => `Current ${size}px · affects body text and page zoom`, interfaceLanguage: 'Interface language', interfaceLanguageHint: 'Change the app interface text and system font', engine: 'Parser', engineHint: 'Control how author-defined symbols are detected', localMode: 'Local-first mode', localModeHint: 'PDF fonts and layout are always parsed locally', enabled: 'Enabled', apiKey: 'Current provider API key', apiKeyHint: 'Qwen, DeepSeek, and custom providers keep separate keys', pasteKey: 'Paste API key', saved: 'Saved', notConfigured: 'Not configured', provider: 'Provider', providerHint: 'Each provider uses its official endpoint and model catalog; do not mix models or keys', custom: 'Custom model', model: 'Model name', modelHint: 'Choose the model ID for the selected provider, such as qwen3.7-flash or deepseek-flash', customEndpoint: 'API endpoint', customEndpointHint: 'Enter the complete Chat Completions endpoint', customEndpointPlaceholder: 'https://example.com/v1/chat/completions', customModelPlaceholder: 'for example my-model', summaryLanguage: 'Summary language', summaryLanguageHint: 'Choose the language for newly generated symbol summaries', chinese: '简体中文', english: 'English', note: 'Papers are parsed locally by default. Definition excerpts are sent to the selected model only after an API key is provided. Custom models must expose an OpenAI-compatible Chat Completions endpoint.' },
}

export function getCopy(language: Language): Copy {
  return language === 'en' ? en : zh
}
