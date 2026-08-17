import { type UserCompany } from '../contexts/AuthContext';
import { BookcometLogo } from './BookcometLogo';
import './CompanyPickerModal.css';

interface Props {
  companies: UserCompany[];
  onSelect: (companyId: string) => void;
}

export function CompanyPickerModal({ companies, onSelect }: Props) {
  return (
    <div className="cpicker-backdrop">
      <div className="cpicker-card">
        <div className="cpicker-logo">
          <BookcometLogo variant="picker" alt="" />
          <h1>Bookcomet</h1>
          <p>Select a company to continue</p>
        </div>

        <div className="cpicker-list">
          {companies.map((c) => (
            <button
              key={c.id}
              className="cpicker-item"
              onClick={() => onSelect(c.id)}
            >
              <div className="cpicker-item-icon">
                {c.name.charAt(0).toUpperCase()}
              </div>
              <div className="cpicker-item-body">
                <div className="cpicker-item-name">{c.name}</div>
                <div className="cpicker-item-role">{c.roleLabel}</div>
              </div>
              <div className="cpicker-item-right">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" className="cpicker-chevron">
                  <path d="M6 3L11 8L6 13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
