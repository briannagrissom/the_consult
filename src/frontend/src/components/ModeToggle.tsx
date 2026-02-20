import { Button } from "@/components/ui/button";
import { Stethoscope, FlaskConical } from "lucide-react";

type Mode = "clinical" | "research";

interface ModeToggleProps {
  mode: Mode;
  onChange: (mode: Mode) => void;
}

export const ModeToggle = ({ mode, onChange }: ModeToggleProps) => {
  return (
    <div className="inline-flex items-center gap-1 p-1 bg-muted rounded-lg shadow-soft">
      <Button
        variant={mode === "clinical" ? "default" : "ghost"}
        size="sm"
        onClick={() => onChange("clinical")}
        className={
          mode === "clinical"
            ? "bg-clinical text-clinical-foreground hover:bg-clinical/90"
            : "hover:bg-background/50 text-foreground"
        }
      >
        <Stethoscope className="h-4 w-4 mr-2" />
        Clinical Practice
      </Button>
      <Button
        variant={mode === "research" ? "default" : "ghost"}
        size="sm"
        onClick={() => onChange("research")}
        className={
          mode === "research"
            ? "bg-research text-research-foreground hover:bg-research/90"
            : "hover:bg-background/50 text-foreground"
        }
      >
        <FlaskConical className="h-4 w-4 mr-2" />
        Research
      </Button>
    </div>
  );
};
