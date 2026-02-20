import { useState } from "react";
import { SearchBar } from "@/components/SearchBar";
import { ModeToggle } from "@/components/ModeToggle";
import { AIAnswerSection } from "@/components/AIAnswerSection";
import { StudyCard } from "@/components/StudyCard";
import { FilterSidebar } from "@/components/FilterSidebar";
import { useToast } from "@/components/ui/use-toast";
import { useGeminiAsk, type EvidenceFilters, type Citation } from "@/hooks/useGeminiAsk";
import type { Study } from "@/types/study";
import heroImage from "@/assets/medical-hero.jpg";

type Mode = "clinical" | "research";

const transformCitations = (citations: Citation[]): Study[] => {
  const MAX_AUTHORS = 6;

  const toBoolean = (value?: string | null) => {
    if (!value) return false;
    const normalized = value.trim().toLowerCase();
    return normalized === "true" || normalized === "1";
  };

  const shortenAuthorString = (raw: string) => {
    // Authors come semicolon-delimited with affiliations in parentheses; strip affiliations and compress to "Last FI".
    const parts = raw.split(";").map((part) => part.trim()).filter(Boolean);
    const formatted = parts.map((part) => {
      const withoutAffil = part.replace(/\s*\(.*?\)\s*/g, " ").replace(/\s+/g, " ").trim();
      if (!withoutAffil) return "";
      const tokens = withoutAffil.split(/\s+/);
      if (tokens.length === 1) return tokens[0];
      const lastName = tokens[tokens.length - 1];
      const initials = tokens
        .slice(0, -1)
        .map((token) => token[0]?.toUpperCase())
        .filter(Boolean)
        .join("");
      return initials ? `${lastName} ${initials}` : lastName;
    }).filter(Boolean);

    if (!formatted.length) return raw.trim();
    return formatted.length > MAX_AUTHORS
      ? `${formatted.slice(0, MAX_AUTHORS).join(", ")} et al.`
      : formatted.join(", ");
  };

  const formatAuthors = (authors?: string | string[] | null) => {
    if (!authors || (Array.isArray(authors) && authors.length === 0)) {
      return "Authors not provided";
    }
    if (Array.isArray(authors)) {
      const trimmed = authors.map((author) => author.trim()).filter(Boolean);
      if (!trimmed.length) return "Authors not provided";
      return trimmed.length > MAX_AUTHORS
        ? `${trimmed.slice(0, MAX_AUTHORS).join(", ")} et al.`
        : trimmed.join(", ");
    }
    const shortened = shortenAuthorString(authors);
    return shortened || "Authors not provided";
  };

  return citations.map((citation, index) => {
    const parsedYear = citation.publication_date ? new Date(citation.publication_date).getFullYear() : undefined;
    const year = Number.isNaN(parsedYear) ? undefined : parsedYear;

    return {
      id: citation.id || citation.pmid || `citation-${index + 1}`,
      citationNumber: index + 1,
      title: citation.title || "Untitled reference",
      authors: formatAuthors(citation.authors),
      journal: citation.journal || "Journal not specified",
      year,
      studyType: "Study",
      hasCOI: toBoolean(citation.coi_flag),
      topJournal: toBoolean(citation.is_top_journal),
      highlyCited: false,
      sampleSize: null,
      design: "Study design not provided.",
      summary: citation.snippet || "Summary not available.",
      methodology: "Methodology details not provided by the source.",
      url: citation.pubmed_url || undefined,
    };
  });
};

const Index = () => {
  const [searchQuery, setSearchQuery] = useState("");
  const [mode, setMode] = useState<Mode>("clinical");
  const [showResults, setShowResults] = useState(false);
  const [aiAnswer, setAiAnswer] = useState("");
  const [references, setReferences] = useState<Study[]>([]);
  const [filters, setFilters] = useState<EvidenceFilters>({
    articleImpact: [],
    publicationDate: "All Articles",
    coiDisclosure: "All Articles",
  });
  const { toast } = useToast();
  const geminiMutation = useGeminiAsk();

  const handleSearch = () => {
    if (!searchQuery.trim()) {
      toast({
        title: "Question required",
        description: "Enter a clinical or research question to query Gemini.",
      });
      return;
    }

    setShowResults(true);
    setAiAnswer("");
    geminiMutation.mutate(
      {
        question: searchQuery.trim(),
        mode,
        filters,
        onStream: (partialAnswer) => {
          setAiAnswer(partialAnswer);
        },
      },
      {
        onSuccess: (data) => {
          setAiAnswer(data.answer?.trim() || "Gemini did not return an answer.");

          if (data.citations?.length) {
            setReferences(transformCitations(data.citations));
          } else {
            setReferences([]);
          }
        },
        onError: (error) => {
          const description = error instanceof Error ? error.message : "Unknown error";
          toast({
            title: "Unable to reach Gemini",
            description,
            variant: "destructive",
          });
        },
      },
    );
  };

  // Filter studies based on current filters
  const getFilteredStudies = () => {
    return references.filter((study) => {
      const studyYear = study.year;
      const hasCOI = Boolean(study.hasCOI);

      // Filter by article impact
      if (!filters.articleImpact.includes("All Articles") && filters.articleImpact.length > 0) {
        const hasTopJournal = filters.articleImpact.includes("Top Journal");
        const hasHighlyCited = filters.articleImpact.includes("Highly Cited");

        const matchesImpact = (hasTopJournal && study.topJournal) || (hasHighlyCited && study.highlyCited);

        if (!matchesImpact) {
          return false;
        }
      }

      // Filter by publication date
      if (filters.publicationDate !== "All Articles") {
        const currentYear = 2024;
        if (!studyYear) {
          return true;
        }
        if (filters.publicationDate === "Within last year" && currentYear - studyYear > 1) {
          return false;
        }
        if (filters.publicationDate === "Within last 5 years" && currentYear - studyYear > 5) {
          return false;
        }
      }

      // Filter by COI disclosure
      if (filters.coiDisclosure === "With Disclosures" && !hasCOI) {
        return false;
      }
      if (filters.coiDisclosure === "Without Disclosures" && hasCOI) {
        return false;
      }

      return true;
    });
  };

  const availableStudies = getFilteredStudies();
  const currentAnswer =
    geminiMutation.isPending
      ? aiAnswer || "Generating answer..."
      : aiAnswer || "Enter a question to receive an answer.";

  // Renumber studies for display (1, 2, 3, etc.)
  const displayStudies = availableStudies.map((study, index) => ({
    ...study,
    citationNumber: index + 1,
  }));


  return (
    <div className="min-h-screen bg-gradient-subtle">
      {/* Hero Section */}
      <header className="relative bg-gradient-primary text-white overflow-hidden">
        <div className="absolute inset-0 opacity-20">
          <img
            src={heroImage}
            alt="Medical research abstract visualization"
            className="w-full h-full object-cover"
          />
        </div>
        <div className="relative container mx-auto px-6 py-16 text-center">
          <h1 className="text-5xl font-bold mb-4">The Consult</h1>
          <p className="text-xl text-white/90 mb-8 max-w-2xl mx-auto">
            AI-powered medical literature search tailored for clinicians and researchers
          </p>

          <div className="flex flex-col items-center gap-6">
            <SearchBar
              value={searchQuery}
              onChange={setSearchQuery}
              onSearch={handleSearch}
            />
            <ModeToggle mode={mode} onChange={setMode} />
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="container mx-auto px-6 py-12">
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-8">
          {/* Filters Sidebar */}
          <aside className="lg:col-span-1">
            <FilterSidebar filters={filters} onFilterChange={setFilters} />
          </aside>

          {showResults ? (
            <div className="lg:col-span-3 space-y-8">
              {/* AI Answer */}
              <AIAnswerSection
                mode={mode}
                answer={currentAnswer}
              />

              {/* Study Results */}
              <div className="space-y-6">
                <h2 className="text-2xl font-bold text-foreground">
                  References
                </h2>

                <div className="space-y-4">
                  {displayStudies.map((study) => (
                    <StudyCard key={study.id} study={study} />
                  ))}
                </div>

                {displayStudies.length === 0 && (
                  <div className="text-center py-12">
                    <p className="text-muted-foreground">
                      No studies match your current filters.
                    </p>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="lg:col-span-3">
              <div className="max-w-2xl mx-auto py-12 text-center space-y-6">
                <h2 className="text-3xl font-bold text-foreground">
                  Get Started with Evidence-Based Answers
                </h2>
                <p className="text-lg text-muted-foreground">
                  Enter a clinical or research question to receive AI-generated answers with
                  referenced medical studies, complete with critical appraisal.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-8 text-left">
                  <div className="p-6 bg-card rounded-lg shadow-soft border border-border">
                    <h3 className="font-semibold mb-2 text-clinical-foreground">
                      Clinical Practice Mode
                    </h3>
                    <p className="text-sm text-muted-foreground">
                      Concise, guideline-driven answers optimized for point-of-care decisions
                    </p>
                  </div>
                  <div className="p-6 bg-card rounded-lg shadow-soft border border-border">
                    <h3 className="font-semibold mb-2 text-research">Research Mode</h3>
                    <p className="text-sm text-muted-foreground">
                      Detailed critical appraisal with methodology analysis and bias assessment
                    </p>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
};

export default Index;
