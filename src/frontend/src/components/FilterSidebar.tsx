import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Separator } from "@/components/ui/separator";
import type { EvidenceFilters } from "@/hooks/useGeminiAsk";

interface FilterSidebarProps {
  filters: EvidenceFilters;
  onFilterChange: (filters: EvidenceFilters) => void;
}

const ARTICLE_IMPACT_ALL = "All Articles";

export const FilterSidebar = ({ filters, onFilterChange }: FilterSidebarProps) => {
  const articleImpactOptions = ["All Articles", "Top Journal", "Highly Cited"];

  const publicationDateOptions = [
    "All Articles",
    "Within last year",
    "Within last 5 years",
  ];

  const coiDisclosureOptions = [
    "All Articles",
    "With Disclosures",
    "Without Disclosures",
  ];

  const toggleArticleImpact = (impact: string) => {
    if (impact === ARTICLE_IMPACT_ALL) {
      onFilterChange({ ...filters, articleImpact: [] });
    } else {
      const newImpacts = filters.articleImpact.includes(impact)
        ? filters.articleImpact.filter((i) => i !== impact)
        : [...filters.articleImpact, impact];
      onFilterChange({ ...filters, articleImpact: newImpacts });
    }
  };


  return (
    <Card className="shadow-soft sticky top-6">
      <CardHeader>
        <CardTitle className="text-lg">Filters</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Article Impact */}
        <div className="space-y-3">
          <h4 className="font-medium text-sm text-foreground">Article Impact</h4>
          <div className="space-y-2">
            {articleImpactOptions.map((option) => (
              <div key={option} className="flex items-center space-x-2">
                <Checkbox
                  id={`impact-${option}`}
                  checked={
                    option === ARTICLE_IMPACT_ALL
                      ? filters.articleImpact.length === 0
                      : filters.articleImpact.includes(option)
                  }
                  onCheckedChange={() => toggleArticleImpact(option)}
                />
                <Label
                  htmlFor={`impact-${option}`}
                  className="text-sm cursor-pointer"
                >
                  {option}
                </Label>
              </div>
            ))}
          </div>
        </div>

        <Separator />

        {/* Publication Date */}
        <div className="space-y-3">
          <h4 className="font-medium text-sm text-foreground">Publication Date</h4>
          <RadioGroup
            value={filters.publicationDate}
            onValueChange={(value) =>
              onFilterChange({ ...filters, publicationDate: value })
            }
          >
            {publicationDateOptions.map((option) => (
              <div key={option} className="flex items-center space-x-2">
                <RadioGroupItem value={option} id={option} />
                <Label
                  htmlFor={option}
                  className="text-sm cursor-pointer"
                >
                  {option}
                </Label>
              </div>
            ))}
          </RadioGroup>
        </div>

        <Separator />

        {/* Conflict of Interest */}
        <div className="space-y-3">
          <h4 className="font-medium text-sm text-foreground">Conflict of Interest</h4>
          <RadioGroup
            value={filters.coiDisclosure}
            onValueChange={(value) =>
              onFilterChange({ ...filters, coiDisclosure: value })
            }
          >
            {coiDisclosureOptions.map((option) => (
              <div key={option} className="flex items-center space-x-2">
                <RadioGroupItem value={option} id={`coi-${option}`} />
                <Label
                  htmlFor={`coi-${option}`}
                  className="text-sm cursor-pointer"
                >
                  {option}
                </Label>
              </div>
            ))}
          </RadioGroup>
        </div>
      </CardContent>
    </Card>
  );
};
