import { useState } from "react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ChevronDown, ChevronUp, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Study } from "@/types/study";

interface StudyCardProps {
  study: Study;
}

export const StudyCard = ({ study }: StudyCardProps) => {
  const [expanded, setExpanded] = useState(false);

  const currentYear = new Date().getFullYear();
  // const isNewPublication = Boolean(study.year && currentYear - study.year < 1);
  const authors = study.authors ?? "Authors not listed";
  const journal = study.journal ?? "Journal not specified";
  const yearLabel = study.year ? `(${study.year})` : "";
  const url = study.url ?? undefined;
  const studyType = study.studyType ?? "Study";
  const hasCOI = Boolean(study.hasCOI);
  const topJournal = Boolean(study.topJournal);
  const highlyCited = Boolean(study.highlyCited);
  // const summary = study.summary ?? "Summary not available for this reference.";
  // const design = study.design ?? "Study design not provided.";
  // const sampleSize = study.sampleSize;
  // const methodology = study.methodology ?? "Methodology details not provided by the source.";

  return (
    <Card id={`study-${study.citationNumber}`} className="shadow-soft hover:shadow-medium transition-shadow duration-300">
      <CardHeader className="space-y-3">
        <div className="flex items-start gap-4">
          <div className="flex-shrink-0 w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center">
            <span className="text-sm font-semibold text-primary">{study.citationNumber}</span>
          </div>
          <div className="flex-1 space-y-2">
            <h3 className="text-lg font-semibold leading-tight text-foreground">
              {url ? (
                <a
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-primary transition-colors"
                >
                  {study.title}
                </a>
              ) : (
                <span>{study.title}</span>
              )}
            </h3>
            <p className="text-sm text-muted-foreground">
              {authors} • {journal} {yearLabel}
            </p>
            {url ? (
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-primary hover:underline inline-block"
              >
                View full article →
              </a>
            ) : (
              <span className="text-sm text-muted-foreground inline-block">
                Link unavailable
              </span>
            )}
            <div className="flex flex-wrap gap-2">
              {/* {studyType && (
                <Badge variant="outline" className="bg-muted">
                  {studyType}
                </Badge>
              )} */}
              {topJournal && (
                <Badge variant="outline" className="bg-primary/10 text-primary border-primary/20">
                  Top Journal
                </Badge>
              )}
              {highlyCited && (
                <Badge variant="outline" className="bg-research/10 text-research border-research/20">
                  Highly Cited
                </Badge>
              )}
              {/* {isNewPublication && (
                <Badge variant="outline" className="bg-accent/10 text-accent border-accent/20">
                  New Publication
                </Badge>
              )} */}
              {hasCOI ? (
                <Badge variant="outline" className="bg-warning/10 text-warning border-warning/20">
                  <AlertCircle className="h-3 w-3 mr-1" />
                  COI
                </Badge>
              ) : (
                <Badge variant="outline" className="bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20">
                  No COI
                </Badge>
              )}
            </div>
          </div>
        </div>
      </CardHeader>

      {/* <CardContent className="space-y-4">
        <p className="text-sm text-foreground leading-relaxed">{summary}</p>

        <div className="pt-2 border-t border-border">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setExpanded(!expanded)}
            className="w-full justify-between text-muted-foreground hover:text-foreground"
          >
            <span className="text-sm font-medium">Methodology Details</span>
            {expanded ? (
              <ChevronUp className="h-4 w-4" />
            ) : (
              <ChevronDown className="h-4 w-4" />
            )}
          </Button>

          {expanded && (
            <div className="mt-4 p-4 bg-muted/50 rounded-lg space-y-2">
              <p className="text-sm">
                <span className="font-medium text-foreground">Study Design:</span>{" "}
                <span className="text-muted-foreground">{design}</span>
              </p>
              {sampleSize ? (
                <p className="text-sm">
                  <span className="font-medium text-foreground">Sample Size:</span>{" "}
                  <span className="text-muted-foreground">n={sampleSize}</span>
                </p>
              ) : (
                <p className="text-sm text-muted-foreground">Sample size not reported.</p>
              )}
              <p className="text-sm text-muted-foreground leading-relaxed">{methodology}</p>
            </div>
          )}
        </div>
      </CardContent> */}
    </Card>
  );
};
