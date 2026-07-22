import { copyFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const files = [
  ["node_modules/@xterm/xterm/lib/xterm.js", "src/muxdev/api/static/vendor/xterm.js"],
  ["node_modules/@xterm/xterm/css/xterm.css", "src/muxdev/api/static/vendor/xterm.css"],
  ["node_modules/@xterm/addon-fit/lib/addon-fit.js", "src/muxdev/api/static/vendor/addon-fit.js"],
  ["node_modules/@xterm/xterm/LICENSE", "src/muxdev/api/static/vendor/xterm-LICENSE.txt"],
  ["node_modules/@xterm/addon-fit/LICENSE", "src/muxdev/api/static/vendor/addon-fit-LICENSE.txt"]
];

for (const [source, target] of files) {
  const destination = resolve(target);
  await mkdir(dirname(destination), { recursive: true });
  await copyFile(resolve(source), destination);
}
