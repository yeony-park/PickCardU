import { requireChatGPTUser } from '../chatgpt-auth';
import { CompareDashboard } from './compare-dashboard';

export const dynamic = 'force-dynamic';

export default async function ComparePage() {
  const user = await requireChatGPTUser('/compare');
  return <CompareDashboard user={{ displayName: user.displayName, email: user.email }} />;
}
