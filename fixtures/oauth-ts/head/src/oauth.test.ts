import { googleCallback } from "./oauth";

test("google callback creates a session", () => {
  googleCallback("user-2");
});
