import { createSession } from "./session";

export function googleCallback(userId: string): string {
  return createSession(userId, 3600);
}
