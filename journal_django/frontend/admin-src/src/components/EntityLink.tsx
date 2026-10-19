import { Link } from 'react-router-dom';

interface Props {
  section: string;
  id: string | number | null | undefined;
  text: string | null | undefined;
  muted?: boolean;
}

export function EntityLink({ section, id, text, muted }: Props) {
  if (id == null || id === '' || text == null || text === '') {
    return <>{text || '—'}</>;
  }
  return (
    <Link
      to={`/admin/${section}/${encodeURIComponent(String(id))}`}
      className={muted ? 'entity-link entity-link--muted' : 'entity-link'}
    >
      {text}
    </Link>
  );
}
