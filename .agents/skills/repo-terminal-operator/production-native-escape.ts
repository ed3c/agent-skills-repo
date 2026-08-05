import { statSync } from "node:fs";
import { spawn } from "node:child_process";

export async function launchNativeEscapeProbe(): Promise<boolean> {
  const ready = process.env.SKILL_BETTOR_NATIVE_ESCAPE_READY;
  const marker = process.env.SKILL_BETTOR_NATIVE_ESCAPE_MARKER;
  if (!ready || !marker) throw new Error("production journey requires carrier-owned native escape probe files");
  const child = spawn("setsid", [
    "sh", "-c",
    "(exec 0</dev/null; exec 1>/dev/null; exec 2>/dev/null; exec 3>&-; printf ready > \"$1\"; sleep 0.25; printf escaped > \"$2\") &",
    "native-escape-probe", ready, marker,
  ], { detached: true, stdio: "ignore" });
  child.unref();
  if (child.pid === undefined) return false;
  const deadline = Date.now() + 500;
  while (Date.now() < deadline) {
    if (statSync(ready).size > 0) return true;
    await new Promise((finish) => setTimeout(finish, 10));
  }
  return false;
}
