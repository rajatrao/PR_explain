export function createSession(userId: string, ttlMs: number): string {
  const token = `sess_${userId}_${ttlMs}`;
  return token;
}
