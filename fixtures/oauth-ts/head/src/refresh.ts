import { createSession } from "./session";

export function refreshToken(userId: string): string {
  return createSession(userId, 7200);
}
