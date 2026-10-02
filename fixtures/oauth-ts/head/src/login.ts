import { createSession } from "./session";

export function login(userId: string): string {
  return createSession(userId, 3600);
}
