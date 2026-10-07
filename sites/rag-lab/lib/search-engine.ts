import type { SearchMode, SearchResult } from './contracts';

export type Chunk = {
  chunk_id: string; parent_id?: string; document_id: string; issuer: string;
  card_name: string; page_start: number; page_end: number; section_path: string[];
  text: string; source_path?: string;
};
export type Corpus = {
  parents: Chunk[]; children: Chunk[]; postings: Record<string, [number, number][]>;
  lengths: number[]; norms: number[];
};
export type SearchOptions = {
  query: string; mode: SearchMode; top_k: number; candidate_k: number; alpha: number;
  issuer?: string | null; card_name?: string | null;
};
type Score = [number, number];

export function retrieve(corpus: Corpus, options: SearchOptions, vectors?: Float32Array, queryVector?: number[], dimensions = 1536): SearchResult[] {
  const { children, parents } = corpus;
  const allowed = (i: number) => (!options.issuer || children[i].issuer === options.issuer) && (!options.card_name || children[i].card_name === options.card_name);
  const sort = (scores: Score[]) => scores.sort((a, b) => b[1] - a[1] || (children[a[0]].chunk_id < children[b[0]].chunk_id ? -1 : 1));
  const keywordScores = new Map<number, number>();
  if (options.mode !== 'vector') {
    const tokens = [...new Set(options.query.toLowerCase().replaceAll('ß', 'ss').match(/[0-9a-z가-힣]+/g) ?? [])];
    const averageLength = corpus.lengths.reduce((a, b) => a + b, 0) / children.length;
    for (const token of tokens) {
      const postings = corpus.postings[token] ?? [];
      const idf = Math.max(1e-6, Math.log((children.length - postings.length + 0.5) / (postings.length + 0.5)));
      for (const [i, frequency] of postings) {
        if (!allowed(i)) continue;
        const score = idf * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * corpus.lengths[i] / averageLength));
        keywordScores.set(i, (keywordScores.get(i) ?? 0) + score);
      }
    }
  }
  const keyword = sort([...keywordScores]).slice(0, options.candidate_k);
  let vector: Score[] = [];
  if (options.mode !== 'keyword') {
    if (!vectors || !queryVector || queryVector.length !== dimensions || vectors.length !== children.length * dimensions || queryVector.some(v => !Number.isFinite(v))) {
      throw new Error('임베딩 차원 또는 인덱스가 일치하지 않습니다.');
    }
    const queryNorm = Math.sqrt(queryVector.reduce((sum, value) => sum + value * value, 0));
    if (!queryNorm) throw new Error('질문 임베딩이 비어 있습니다.');
    for (let i = 0; i < children.length; i++) {
      if (!allowed(i)) continue;
      let dot = 0;
      for (let d = 0; d < dimensions; d++) dot += vectors[i * dimensions + d] * queryVector[d];
      vector.push([i, dot / (corpus.norms[i] * queryNorm)]);
    }
    vector = sort(vector).slice(0, options.candidate_k);
  }
  const normalize = (scores: Score[]) => {
    if (!scores.length) return new Map<number, number>();
    const values = scores.map(s => s[1]);
    const min = Math.min(...values), max = Math.max(...values);
    return new Map(scores.map(([i, score]) => [i, max === min ? 1 : (score - min) / (max - min)]));
  };
  let fused = new Map<number, number>();
  if (options.mode === 'keyword') fused = new Map(keyword);
  else if (options.mode === 'vector') fused = new Map(vector);
  else if (options.mode === 'hybrid') {
    for (const ranking of [vector, keyword]) ranking.forEach(([i], rank) => fused.set(i, (fused.get(i) ?? 0) + 1 / (61 + rank)));
  } else {
    const v = normalize(vector), k = normalize(keyword);
    for (const i of new Set([...v.keys(), ...k.keys()])) fused.set(i, options.alpha * (v.get(i) ?? 0) + (1 - options.alpha) * (k.get(i) ?? 0));
  }
  const ranked = sort([...fused]).slice(0, options.candidate_k);
  const grouped = new Map<string, { child: Chunk; score: number; ordinal: number; support: string[] }>();
  for (const [i, score] of ranked) {
    const child = children[i];
    const key = child.parent_id!;
    const prior = grouped.get(key);
    if (!prior) grouped.set(key, { child, score, ordinal: i, support: [child.chunk_id] });
    else if (prior.support.length < 2) prior.support.push(child.chunk_id);
  }
  const parentById = new Map(parents.map(p => [p.chunk_id, p]));
  const v = new Map(vector), k = new Map(keyword);
  return [...grouped].slice(0, options.top_k).map(([id, item], rank) => {
    const p = parentById.get(id)!;
    return {
      rank: rank + 1, score: item.score, keyword_score: k.get(item.ordinal) ?? null,
      vector_score: v.get(item.ordinal) ?? null, rerank_score: null,
      document_id: p.document_id, issuer: p.issuer, card_name: p.card_name,
      page_start: p.page_start, page_end: p.page_end, section_path: p.section_path,
      child: item.child, parent: { chunk_id: id, text: p.text, supporting_children: item.support },
      citation: `${p.card_name} p.${p.page_start}`, source_path: p.source_path ?? null, source_url: null,
    };
  });
}
