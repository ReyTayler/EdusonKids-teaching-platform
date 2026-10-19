import { PageHeader } from '../../components/shell/PageHeader';
import { LessonFeedbackTable } from '../../components/feedback/LessonFeedbackTable';
import { FEEDBACK_LABELS } from '../../lib/labels';

export default function LessonFeedbackPage() {
  return (
    <>
      <PageHeader title={FEEDBACK_LABELS.title} sub={FEEDBACK_LABELS.subtitle} />
      <LessonFeedbackTable />
    </>
  );
}
