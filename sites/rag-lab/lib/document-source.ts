import source from './document-source.json';

export { source as documentSource };
export function documentUrl(path: string | null) {
  if (!path || !path.startsWith('data/raw/') || path.split('/').includes('..') || !path.endsWith('.pdf')) return null;
  return `${source.repository}/blob/${source.commit}/${path.split('/').map(encodeURIComponent).join('/')}`;
}
