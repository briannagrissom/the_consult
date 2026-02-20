import { useMutation } from "@tanstack/react-query";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export interface EvidenceFilters {
  articleImpact: string[];
  publicationDate: string;
  coiDisclosure: string;
}

export interface Citation {
  id?: string | null;
  pmid?: string | null;
  title?: string | null;
  authors?: string | string[] | null;
  journal?: string | null;
  publication_date?: string | null;
  pubmed_url?: string | null;
  snippet?: string | null;
  coi_flag?: string | null;
  is_last_year?: string | null;
  is_last_5_years?: string | null;
  is_top_journal?: string | null;
}

export interface AskPayload {
  question: string;
  mode: "clinical" | "research";
  patient_context?: string;
  filters?: EvidenceFilters;
  onStream?: (partial: string) => void;
}

export interface AskResponse {
  answer: string;
  citations: Citation[];
}

async function callGemini(payload: AskPayload): Promise<AskResponse> {
  const { onStream, ...requestPayload } = payload;

  const streamingResponse = await fetch(`${API_BASE_URL}/api/ask/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(requestPayload),
  });

  if (streamingResponse.status === 404 || streamingResponse.status === 501) {
    return callGeminiLegacy(requestPayload, onStream);
  }

  if (!streamingResponse.ok) {
    const detail = await streamingResponse.text();
    throw new Error(detail || "Gemini request failed");
  }

  const reader = streamingResponse.body?.getReader();
  if (!reader) {
    throw new Error("Streaming is not supported in this environment.");
  }

  const decoder = new TextDecoder();
  let buffer = "";
  let answer = "";
  let citations: Citation[] = [];

  const flushEvents = (chunk: string) => {
    buffer += chunk;
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";

    for (const event of events) {
      if (!event.trim()) continue;

      let eventType = "message";
      const dataLines: string[] = [];

      for (const line of event.split("\n")) {
        if (line.startsWith("event:")) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trim());
        }
      }

      if (dataLines.length === 0) continue;

      const dataString = dataLines.join("\n");

      try {
        const parsed = JSON.parse(dataString) as {
          delta?: string;
          error?: string;
          citations?: Citation[];
        };

        if (parsed.error) {
          throw new Error(parsed.error);
        }

        if (eventType === "citations" && parsed.citations) {
          citations = parsed.citations;
          continue;
        }

        if (parsed.delta) {
          answer += parsed.delta;
          onStream?.(answer);
        }
      } catch (error) {
        throw error instanceof Error ? error : new Error("Failed to parse streaming response.");
      }

      if (eventType === "end") {
        buffer = "";
      }
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    flushEvents(decoder.decode(value, { stream: true }));
  }

  if (buffer.trim()) {
    flushEvents(decoder.decode());
  }

  return { answer, citations };
}

async function callGeminiLegacy(
  payload: Omit<AskPayload, "onStream">,
  onStream?: (partial: string) => void,
): Promise<AskResponse> {
  const response = await fetch(`${API_BASE_URL}/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "Gemini request failed");
  }

  const data: AskResponse = await response.json();
  if (data.answer) {
    onStream?.(data.answer);
  }
  return data;
}

export const useGeminiAsk = () =>
  useMutation({
    mutationFn: callGemini,
  });
