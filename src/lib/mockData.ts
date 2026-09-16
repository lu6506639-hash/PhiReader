import type { Paper } from './types'

export const papers: Paper[] = [
  {
    id: 'nmf-2001',
    title: 'Algorithms for Non-negative Matrix Factorization',
    authors: 'Daniel D. Lee · H. Sebastian Seung',
    venue: 'NIPS 13 · MIT Press',
    year: '2001',
    pages: 7,
    size: '412 KB',
    accent: '#e6a15c',
    progress: 68,
    addedAt: '今天 09:42',
    abstract: 'Non-negative matrix factorization decomposes multivariate data into parts-based representations. This paper analyzes two multiplicative update algorithms and their convergence properties.',
    symbols: [
      { id: 'nmf-n', surface: 'n', meaning: '向量的维度', definition: '“V is an n × m matrix whose columns are the m-dimensional data vectors.”', location: '第 2 页 · Notation', status: 'confirmed', occurrences: 18 },
      { id: 'nmf-v', surface: 'V', meaning: '非负数据矩阵', definition: '“V is an n × m matrix whose columns are the m-dimensional data vectors.”', location: '第 2 页 · Notation', status: 'confirmed', occurrences: 42 },
      { id: 'nmf-r', surface: 'r', meaning: '分解后的低维度', definition: '“The number of basis vectors r is usually much smaller than either n or m.”', location: '第 2 页 · Notation', status: 'confirmed', occurrences: 11 },
      { id: 'nmf-d', surface: 'D(A ‖ B)', meaning: '从 A 到 B 的散度', definition: '“D(A||B) is called the divergence of A from B.”', location: '第 2 页 · §2', status: 'confirmed', occurrences: 9 },
      { id: 'nmf-w', surface: 'W', meaning: '基向量矩阵', definition: '“W is an n × r matrix and H is an r × m matrix.”', location: '第 2 页 · Notation', status: 'confirmed', occurrences: 31 },
      { id: 'nmf-h', surface: 'H', meaning: '系数矩阵', definition: '“W is an n × r matrix and H is an r × m matrix.”', location: '第 2 页 · Notation', status: 'confirmed', occurrences: 29 },
    ],
  },
  {
    id: 'separation-2020',
    title: 'Blind Audio Source Separation with Minimum-Volume Criterion',
    authors: 'A. Leplat · N. Gillis · A. M. S. Ang',
    venue: 'IEEE Transactions on Signal Processing',
    year: '2020',
    pages: 14,
    size: '1.8 MB',
    accent: '#72c6b1',
    progress: 12,
    addedAt: '昨天 18:16',
    abstract: 'A volume minimization framework for blind audio source separation, with a focus on non-negative matrix factorization and spectrogram geometry.',
    symbols: [
      { id: 'sep-x', surface: 'X', meaning: '观测到的混合信号', definition: '“X denotes the observed spectrogram, with N frequency bins and F time frames.”', location: '第 2 页 · §II', status: 'confirmed', occurrences: 24 },
      { id: 'sep-beta', surface: 'β', meaning: 'β-divergence 参数', definition: '“The β-divergence dβ(x|y) is used to measure the discrepancy between x and y.”', location: '第 3 页 · §II-B', status: 'review', occurrences: 17 },
      { id: 'sep-lambda', surface: 'λ', meaning: '体积惩罚系数', definition: '“λ is a penalty parameter controlling the volume regularization.”', location: '第 5 页 · §III', status: 'confirmed', occurrences: 13 },
    ],
  },
]
