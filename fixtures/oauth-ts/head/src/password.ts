import { createSession } from "./session";

export function passwordLogin(userId: string): string {
  return `pw:${userId}`;
}
