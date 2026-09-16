export type View = 'library' | 'reader' | 'settings'

export type SymbolStatus = 'confirmed' | 'review' | 'unresolved'

export interface SymbolDefinition {
  id: string
  surface: string
  identityKey?: string
  style?: string
  meaning: string
  meaning_source?: 'local' | 'qwen' | 'deepseek' | 'remote'
  definition: string
  location: string
  status: SymbolStatus
  occurrences: number
  evidence_level?: number
  evidence_types?: string[]
}

export interface VisualReviewItem {
  review_id: string
  kind: string
  page: number
  line_id?: string
  bbox: [number, number, number, number]
  surface?: string
  reason: string
}

export interface Paper {
  id: string
  title: string
  authors: string
  venue: string
  year: string
  pages: number
  size: string
  accent: string
  progress: number
  addedAt: string
  abstract: string
  symbols: SymbolDefinition[]
  fileUrl?: string
  filePath?: string
  parsePath?: string
  parseStatus?: 'idle' | 'parsing' | 'ready' | 'error'
  parseError?: string
  candidateCount?: number
  visualReviewItems?: number
  reviewSymbolCount?: number
  visualReviewQueue?: VisualReviewItem[]
}

export interface PaperFolder {
  id: string
  name: string
  paperIds: string[]
}

export interface ParserMetadata {
  title: string
  authors: string
  year: string
  producer: string
  creator: string
  pages: number
  file_size: number
}

export interface ParserTotals {
  candidate_chars: number
  definition_lines: number
  formula_lines: number
  definition_events: number
  final_symbols: number
  unresolved_candidates: number
  visual_fallback_regions: number
  visual_review_items: number
  review_symbols: number
}

export interface ParserResponse {
  paperId: string
  paperPath: string
  parsePath: string
  metadata: ParserMetadata
  symbols: SymbolDefinition[]
  totals: ParserTotals
  llmUsed: boolean
  visualReviewQueue: VisualReviewItem[]
}

export interface PageOccurrence {
  surface: string
  identityKey?: string
  style?: string
  bbox: [number, number, number, number]
}

export interface RenderResponse {
  imageData: string
  pageWidth: number
  pageHeight: number
  occurrences: PageOccurrence[]
}

export interface StoredPaperResponse {
  paperId: string
  paperPath: string
  parsePath: string
  title: string
  metadata: ParserMetadata
  symbols: SymbolDefinition[]
  totals: ParserTotals
  visualReviewQueue: VisualReviewItem[]
}
