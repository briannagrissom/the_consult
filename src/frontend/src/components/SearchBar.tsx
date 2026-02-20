import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

interface SearchBarProps {
  value: string;
  onChange: (value: string) => void;
  onSearch: () => void;
}

export const SearchBar = ({ value, onChange, onSearch }: SearchBarProps) => {
  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      onSearch();
    }
  };

  return (
    <div className="relative flex items-center gap-2 w-full max-w-3xl">
      <div className="relative flex-1">
        <Search className="absolute left-4 top-1/2 -translate-y-1/2 h-5 w-5 text-muted-foreground" />
        <Input
          type="text"
          placeholder="Enter clinical or research questions..."
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyPress={handleKeyPress}
          className="pl-12 pr-4 h-14 text-base shadow-medium border-border/50 focus-visible:ring-primary text-foreground"
        />
      </div>
      <Button
        onClick={onSearch}
        size="lg"
        className="h-14 px-8 bg-primary hover:bg-primary/90"
      >
        Search
      </Button>
    </div>
  );
};
