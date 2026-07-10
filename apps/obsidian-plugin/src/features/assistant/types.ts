export type QueryScope = "current" | "vault";

export interface ChatMessage {
  role: "system" | "user";
  content: string;
}
