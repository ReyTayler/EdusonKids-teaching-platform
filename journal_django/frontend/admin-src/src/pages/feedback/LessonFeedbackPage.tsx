import { LessonFeedbackTable } from '../../components/feedback/LessonFeedbackTable';
import { FEEDBACK_LABELS } from '../../lib/labels';

export default function LessonFeedbackPage() {
  // Шапку с лупой рисует сама лента: фильтры и колонки живут в ней.
  return <LessonFeedbackTable pageTitle={FEEDBACK_LABELS.title} />;
}
