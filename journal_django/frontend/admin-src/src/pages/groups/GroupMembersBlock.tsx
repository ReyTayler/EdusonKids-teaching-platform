import { useStudentsAll } from '../../hooks/useStudents';
import { MembershipsBlock } from '../../components/memberships/MembershipsBlock';
import { StudentStageBadge } from '../../components/StudentStageBadge';
import type { Group } from '../../lib/types';
import { ageFromBirthDate, fmtAge, studentOptionLabel } from '../../lib/format';

export default function GroupMembersBlock({ group }: { group: Group }) {
  const { data: students = [] } = useStudentsAll();
  // Прежний фильтр прятал soft-deleted (not_enrolled) учеников; и статус, и сам
  // soft-delete удалены, а те записи переведены в enrolled — фильтровать нечего.
  const studentOptions = students.map((s) => ({ value: s.id, label: studentOptionLabel(s) }));

  return (
    <MembershipsBlock
      config={{
        mode: 'byGroup',
        groupId: group.id,
        pickerOptions: studentOptions,
        pickerLabel: 'Выберите ученика',
        // Индивидуальная группа — строго один ученик.
        capacity: group.is_individual ? 1 : undefined,
        capacityNote: group.is_individual ? 'Индивидуальная группа — только один ученик' : undefined,
      }}
      emptyText="В группе нет учеников"
      renderCard={(m) => {
        const s = students.find((x) => x.id === m.student_id);
        return {
          title: m.student_name || (s ? s.full_name : `#${m.student_id}`),
          meta: s ? (
            <>
              {ageFromBirthDate(s.birth_date) != null && <span className="link-card-meta-pill">{fmtAge(s.birth_date)}</span>}
              <StudentStageBadge row={s} />
            </>
          ) : null,
          navigateTo: `/admin/students/${m.student_id}`,
        };
      }}
    />
  );
}
