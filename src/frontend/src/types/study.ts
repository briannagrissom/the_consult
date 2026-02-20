export interface Study {
  id: string;
  citationNumber: number;
  title: string;
  authors?: string;
  journal?: string;
  year?: number;
  studyType?: string;
  hasCOI?: boolean;
  topJournal?: boolean;
  highlyCited?: boolean;
  sampleSize?: number | null;
  design?: string | null;
  summary?: string | null;
  methodology?: string | null;
  url?: string | null;
}
