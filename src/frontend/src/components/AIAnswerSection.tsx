import ReactMarkdown from "react-markdown";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Sparkles } from "lucide-react";

interface AIAnswerSectionProps {
  mode: "clinical" | "research";
  answer: string;
}

export const AIAnswerSection = ({ mode, answer }: AIAnswerSectionProps) => {
  const handleCitationClick = (studyId: string) => {
    const element = document.getElementById(`study-${studyId}`);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' });
      element.classList.add('ring-2', 'ring-primary', 'ring-offset-2');
      setTimeout(() => {
        element.classList.remove('ring-2', 'ring-primary', 'ring-offset-2');
      }, 2000);
    }
  };

  const renderAnswerWithCitations = (text: string) => {
    const markdownWithAnchors = text.replace(/\[\s*(\d+(?:\s*,\s*\d+)*)\s*\]/g, (_match, citations) => {
      const links = (citations.match(/\d+/g) || []).map((c) => `[${c}](#study-${c})`);
      return links.join(", ");
    });

    return (
      <div className="prose prose-sm dark:prose-invert max-w-none">
        <ReactMarkdown
          components={{
            p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
            a: ({ href, children }) => {
              const match = href?.match(/#study-(\d+)/);
              if (match) {
                const studyId = match[1];
                return (
                  <a
                    href={href}
                    onClick={(e) => {
                      e.preventDefault();
                      handleCitationClick(studyId);
                    }}
                    className="text-primary hover:underline font-medium cursor-pointer"
                  >
                    {children}
                  </a>
                );
              }
              return (
                <a href={href} className="text-primary underline" target="_blank" rel="noreferrer">
                  {children}
                </a>
              );
            },
          }}
        >
          {markdownWithAnchors}
        </ReactMarkdown>
      </div>
    );
  };

  return (
    <Card className="shadow-medium border-2 border-primary/20">
      <CardHeader className="pb-4">
        <div className="flex items-center gap-2">
          <div className={`p-2 rounded-lg ${mode === "clinical" ? "bg-clinical" : "bg-research/20"}`}>
            <Sparkles className={`h-5 w-5 ${mode === "clinical" ? "text-clinical-foreground" : "text-research"}`} />
          </div>
          <h2 className="text-xl font-semibold">
            AI-Generated Answer
            <span className="ml-2 text-sm font-normal text-muted-foreground">
              ({mode === "clinical" ? "Clinical Practice" : "Research"} Mode)
            </span>
          </h2>
        </div>
      </CardHeader>
      <CardContent>
        <div className="text-base leading-relaxed text-foreground space-y-3">
          {renderAnswerWithCitations(answer)}
        </div>
     </CardContent>
   </Card>
 );
};
