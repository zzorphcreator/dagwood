import { FileText } from 'lucide-react';
import { DELIVERABLE_LABELS, fileUrl, type FileInfo } from './api';

const fmtSize = (bytes: number) => (bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1048576).toFixed(1)} MB`);

/** Open / download links for files a publishing step produced. PowerPoint files can only be downloaded. */
export default function FileLinks({ runId, files }: { runId: string; files: FileInfo[] }) {
  return (
    <ul className="file-list">
      {files.map((f) => (
        <li key={f.name}>
          <span className="file-kind">
            <FileText size={14} /> {DELIVERABLE_LABELS[f.format]}
          </span>
          {f.format !== 'pptx' && (
            <a href={fileUrl(runId, f.name)} target="_blank" rel="noopener noreferrer">
              Open
            </a>
          )}
          <a href={fileUrl(runId, f.name, true)} download={f.name}>
            Download
          </a>
          <span className="muted">
            {f.name} · {fmtSize(f.size)}
          </span>
        </li>
      ))}
    </ul>
  );
}
